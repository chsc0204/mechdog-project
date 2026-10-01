"""
WiFi(UDP) 에스코트 - escort_ble_no_camera.py 를 WiFi로 이식한 버전 (2026-09-27)

흐름 (BLE 버전과 동일):
  목적지 입력 -> 웨이포인트 이동(장애물 자동 회피) -> 도착 인사
  -> 터치 대기(타임아웃) -> 출발지 복귀 -> idle

BLE 버전과 달라진 점:
  - 통신: BLE -> UDP (로봇 main_wifi.py, 포트 9027)
  - keepalive: 이동 중 0.3초마다 이동 명령 재전송
    (로봇 펌웨어의 통신두절 자동정지(1초) 때문에 필수 - 2026-09-27 실기 검증)
  - 정지 명령은 UDP 유실 대비 3회 전송
  - 비상정지: 이동 중 'q' 키 (Enter 불필요) 또는 Ctrl+C
  - 회피 실패(escort_lost) 시 남은 경로를 계속 가지 않고 에스코트 전체 중단
    (BLE 버전은 실패한 스텝만 건너뛰고 다음 스텝/도착 인사까지 진행하던 문제 수정)
  - MQTT 연결을 매번 새로 만들지 않고 1회 연결 후 재사용

명령 프로토콜 (main_wifi.py):
  CMD|3|{dir}|$   이동 (0=정지,1=강우,2=약우,3=직진,4=약좌,5=강좌,7=후진)
  CMD|2|1|8|$     인사 동작 (scrape_a_bow)
  CMD|4|1|$       거리 조회 -> "CMD|4|{mm}|$"
  CMD|11|1|$      터치 조회 -> "CMD|11|{0/1}|$"
  CMD|6|$         배터리 조회 -> "CMD|6|{값}|$"

사전 준비:
  - 노트북, 로봇, 카메라 모두 같은 핫스팟에 연결
  - 로봇에 main_wifi.py (인사동작 수정본) 업로드
  - docker compose up -d (MQTT/DB) - 꺼져 있어도 이동은 동작함
"""

import json
import socket
import threading
import time

# ===== 설정 =====
ROBOT_IP = "172.20.10.6"
ROBOT_PORT = 9027
DISTANCE_FILE = "demo_routes.json"      # 거리/각도로 적은 시연 경로 (시연 장소에서 숫자만 수정)
DEST_FILE = "destinations.json"          # record_route.py 로 기록한 경로 (팀 목적지 코드 기준)
ROUTE_PRIORITY = "distance"              # 같은 목적지가 두 파일에 다 있으면: "distance" 또는 "recorded" 우선
DEST_FILE_FALLBACK = "destinations_ble.json"  # 기록 파일이 없으면 기존 BLE 웨이포인트 사용

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
ROBOT_ID = 3

OBSTACLE_THRESHOLD = 15     # cm 이내면 장애물
DWELL_SECONDS = 8           # 도착 후 터치 대기 시간
BOW_WAIT_SECONDS = 5.5      # 인사 동작 대기 (펌웨어: 기본자세 1초 + 동작 4초)
KEEPALIVE_INTERVAL = 0.3    # 펌웨어 타임아웃 1초보다 충분히 짧게
QUERY_TIMEOUT = 0.4         # 거리/터치 응답 대기
CHECK_INTERVAL = 0.25       # 이동 중 거리 확인 주기
MAX_AVOID_ATTEMPTS = 4
STOP_RESEND = 3
PAUSE_TIMEOUT_SEC = 60     # 터치 일시정지 후 이 시간 안에 다시 터치하지 않으면 에스코트 중단
TURN_SETTLE_SEC = 0.5      # 회전 전후 정지 안정화 시간 (걸음 관성이 회전/직진에 섞이지 않게)

# U턴 설정 - 제자리 회전 대신 "걸으면서 크게 도는" 방식 (제자리 회전은 불안정했음)
# 기존 실측: 강좌회전(dir=5) 1.5초 = 약 60도 -> 180도는 약 4.5초 (실측으로 보정할 것)
# 도착지/출발지에서 같은 방향으로 U턴 -> 옆으로 밀린 거리가 상쇄되어 원위치 복귀
UTURN_DIR = 5         # 5=강좌회전+전진
UTURN_SEC = 4.5       # 목적지 입력에 "uturn" 치면 U턴만 단독 테스트 가능

# ===== 카메라 복귀 (출발지 표식: 초록 공) =====
# 초록 공은 로봇 출발 위치 "뒤쪽 약 50cm" 바닥에 둠 -> 공 앞 약 50cm에서 멈추면 출발 위치
# 실측(2026-09-28): 초록 공 r = 50cm 13.7px / 1m 7.6px / 1.5m 5.9px
CAMERA_ENABLED = True    # 카메라 스트림 사용 (사람 인식, 경고 사진 저장)
PERSON_DETECT = True     # 전방 사람 인식 (YOLO) - 사람이면 기다리고, 물건이면 회피
PERSON_STOP_AREA = 12.0  # 이동 중 전방 사람이 화면의 이 %보다 크게 보이면 미리 멈춤 (약 1m 이내)
PERSON_RECENT_SEC = 2.0  # 거리센서 감지 시 "방금 전 사람이 보였는지" 확인하는 시간 범위
PERSON_WAIT_SEC = 10.0   # 사람이 비켜가기를 기다리는 최대 시간 -> 지나면 기존 회피 기동
SNAPSHOT_DIR = "snapshots"
HOMING_ENABLED = False   # 기록한 복귀 경로 사용 시 기본 끔. 마지막 보정이 필요하면 True (초록 공 필요)
R_STOP = 13.0            # 이 크기(px) 이상이면 도착 (약 50cm)
STEER_DEADBAND = 0.25    # |offset|이 이보다 작으면 직진
LOST_GRACE = 1.0         # 공이 잠깐 안 보여도 이 시간(초) 동안은 하던 동작 유지
SEARCH_MODE = "spin"     # "walk": 강좌회전 걸음(앞으로 이동함) / "spin": 제자리 회전(CMD|9)
SEARCH_DIR = 5           # walk 모드 탐색 방향 (5=강좌회전)
SPIN_DEG = 40            # spin 모드 회전 세기 (양수=좌회전)
SPIN_COUNT = 40          # spin 모드 1회 회전량 (0.05초 x count) - 20은 약 10도로 너무 작았음
CANDIDATE_WAIT = 1.5     # 후보가 보이면 돌지 않고 이만큼 더 기다리며 확인
SETTLE_SEC = 1.0         # 멈춘 뒤 대기 - 카메라 영상 지연 때문에 0.4는 멈추기 전 화면으로 판단했음
NEW_FRAMES = 5           # 멈춘 뒤 새로 들어온 영상이 이만큼 쌓인 다음 판단
FRESH_SEC = 0.4          # 이보다 최근 영상의 공만 사용
ALIGN_DEADBAND = 0.2     # |offset|이 이보다 크면 제자리 회전으로 정렬 먼저
ALIGN_GAIN = 25          # 정렬 회전량 = |offset| x 이 값 (count) - 50은 넘쳐서 공을 놓침
ALIGN_COUNT_MIN = 6
ALIGN_COUNT_MAX = 25
R_NEAR = 9.0             # 이보다 크면(약 80cm 이내) 짧게 전진
APPROACH_STEP_FAR = 0.8  # 멀 때 1회 전진 시간(초)
APPROACH_STEP_NEAR = 0.4 # 가까울 때 1회 전진 시간(초)
SEARCH_PULSE = 1.0       # 탐색: 이만큼 돌고
SEARCH_PAUSE = 0.6       #       이만큼 멈춰서 카메라 확인 (흔들림 방지)
MAX_SEARCH_PULSES = 16
HOMING_TIMEOUT = 60      # 전체 제한 시간(초)
FINAL_UTURN = False      # 복귀 후 다음 안내 방향 정렬 U턴 (U턴 반경 문제로 일단 끔)

# ===== 상태별 센서 LED 색 (R, G, B) =====
LED_ENABLED = True
LED = {
    "idle":     (0, 255, 0),      # 초록: 안내 대기
    "moving":   (0, 80, 255),     # 파랑: 목적지로 이동 중
    "return":   (0, 220, 220),    # 하늘색: 출발지로 복귀 중
    "person":   (255, 200, 0),    # 노랑: 사람 감지, 비켜가길 대기
    "avoid":    (255, 60, 0),     # 주황: 장애물 회피 기동 중
    "arrived":  (180, 0, 255),    # 보라: 도착, 인사 및 터치 대기
    "stop":     (255, 0, 0),      # 빨강: 비상정지 / 회피 실패
}

# 복귀 경로용 좌우반전 (1강우<->5강좌, 2약우<->4약좌)
DIR_MIRROR = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1, 7: 7, 0: 0}


# ===== 비상정지 =====
stop_event = threading.Event()

try:
    import msvcrt  # Windows: Enter 없이 키 입력 감지
except ImportError:
    msvcrt = None


class EmergencyStop(Exception):
    pass


class EscortLost(Exception):
    """장애물 회피를 반복해도 실패 -> 에스코트 중단"""
    pass


ROBOT = None          # 터치 일시정지 확인용 (main에서 설정)
_pause_total = [0.0]  # 지금까지 일시정지로 멈춰 있던 누적 시간 (경로 시간 계산에서 제외)


def pause_total():
    return _pause_total[0]


def _key_q_pressed():
    if msvcrt is not None:
        while msvcrt.kbhit():
            if msvcrt.getwch().lower() == "q":
                print("\n[비상 정지] 'q' 입력 감지!")
                stop_event.set()
    return stop_event.is_set()


def wait_while_paused():
    """터치 일시정지 중이면 재개(다시 터치)될 때까지 대기. 1분 초과 시 EscortLost"""
    robot = ROBOT
    if robot is None or not robot.paused.is_set():
        return
    t0 = time.time()
    saved_led = getattr(robot, "_led_state", None)
    robot._led_state = "stop"  # 로봇이 스스로 빨간색으로 바꿈
    print(f"\n    [터치 일시정지] 로봇을 다시 만지면 이어서 갑니다 (최대 {PAUSE_TIMEOUT_SEC}초)")
    publish_status("moving", "방문자 요청으로 일시정지 (터치)", context=None)
    while robot.paused.is_set():
        if _key_q_pressed():
            raise EmergencyStop()
        if time.time() - t0 > PAUSE_TIMEOUT_SEC:
            robot.escort_mode(False)
            raise EscortLost(f"터치 일시정지 후 {PAUSE_TIMEOUT_SEC}초 동안 재개 없음")
        time.sleep(0.05)
    dt = time.time() - t0
    _pause_total[0] += dt
    print(f"    [터치 재개] {dt:.1f}초 멈춤 후 이어서 진행")
    robot._led_state = None
    if saved_led:
        robot.led(saved_led)


def check_emergency():
    wait_while_paused()
    if msvcrt is not None:
        while msvcrt.kbhit():
            if msvcrt.getwch().lower() == "q":
                print("\n[비상 정지] 'q' 입력 감지!")
                stop_event.set()
    if stop_event.is_set():
        raise EmergencyStop()


def interruptible_sleep(duration):
    end = time.time() + duration
    while True:
        p0 = pause_total()
        check_emergency()
        end += pause_total() - p0  # 일시정지로 멈춘 시간만큼 연장
        remaining = end - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


# ===== 로봇 통신 (UDP) =====
class RobotLink:
    def __init__(self, ip, port):
        self.addr = (ip, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.2)
        self.send_lock = threading.Lock()
        self.running = True
        self.current_dir = 0  # 0이 아니면 keepalive 스레드가 계속 재전송

        self.latest_distance = None
        self.latest_distance_time = 0.0
        self.latest_touch = 0
        self.battery = None
        self.distance_event = threading.Event()
        self.touch_event = threading.Event()
        self.battery_event = threading.Event()
        self.spin_done = threading.Event()
        self.paused = threading.Event()     # 로봇 터치 일시정지 상태 (CMD|14)
        self.escort_active = False

        threading.Thread(target=self._recv_loop, daemon=True).start()
        threading.Thread(target=self._keepalive_loop, daemon=True).start()

    def _send(self, cmd):
        with self.send_lock:
            try:
                self.sock.sendto(cmd.encode(), self.addr)
            except OSError as e:
                print(f"[전송 실패] {cmd}: {e}")

    def _recv_loop(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(256)
            except socket.timeout:
                continue
            except ConnectionResetError:
                continue  # Windows UDP 특성: 상대가 응답 못 하면 가끔 발생, 무시
            except OSError:
                if not self.running:
                    break
                continue

            parts = data.decode(errors="ignore").strip().split("|")
            if len(parts) < 3 or parts[0] != "CMD":
                continue
            code, value = parts[1], parts[2]
            try:
                if code == "4":
                    self.latest_distance = float(value) / 10.0  # 펌웨어가 cm*10 으로 보냄
                    self.latest_distance_time = time.time()
                    self.distance_event.set()
                elif code == "11":
                    self.latest_touch = int(value)
                    self.touch_event.set()
                elif code == "14":
                    if value == "1":
                        self.paused.set()
                    else:
                        self.paused.clear()
                    continue
                elif code == "9" and value == "DONE":
                    self.spin_done.set()
                    continue
                elif code == "6":
                    self.battery = float(value)
                    self.battery_event.set()
            except ValueError:
                pass

    def _keepalive_loop(self):
        last_poll = 0.0
        while self.running:
            d = self.current_dir
            if d != 0:
                self._send(f"CMD|3|{d}|$")  # 일시정지 중에는 로봇이 무시 -> 재개되면 자동으로 다시 걸음
            if self.escort_active and time.time() - last_poll > 1.0:
                self._send("CMD|14|$")      # 일시정지 상태 확인 (알림 패킷 유실 대비)
                last_poll = time.time()
            time.sleep(KEEPALIVE_INTERVAL)

    def escort_mode(self, on):
        """에스코트 중 표시: 켜져 있으면 로봇 터치 = 일시정지/재개"""
        self.escort_active = bool(on)
        if not on:
            self.paused.clear()
        for _ in range(2):
            self._send(f"CMD|15|{1 if on else 0}|$")
            time.sleep(0.03)

    def _query(self, cmd, event):
        event.clear()
        self._send(cmd)
        return event.wait(QUERY_TIMEOUT)

    # --- 공개 함수 ---
    def move(self, dir_code):
        check_emergency()
        self.current_dir = dir_code
        self._send(f"CMD|3|{dir_code}|$")

    def stop(self):
        """비상정지 여부와 상관없이 무조건 전송"""
        self.current_dir = 0
        for _ in range(STOP_RESEND):
            self._send("CMD|3|0|$")
            time.sleep(0.05)

    def request_distance(self):
        return self.latest_distance if self._query("CMD|4|1|$", self.distance_event) else None

    def poll_distance(self, max_age=0.8):
        """응답을 기다리지 않는 거리 조회: 조회만 보내고, 최근 응답값이 있으면 반환
        (카메라 추적 루프가 응답 대기로 멈추지 않게 하기 위함)"""
        self._send("CMD|4|1|$")
        if time.time() - self.latest_distance_time <= max_age:
            return self.latest_distance
        return None

    def request_touch(self):
        return self._query("CMD|11|1|$", self.touch_event) and self.latest_touch == 1

    def request_battery(self):
        return self.battery if self._query("CMD|6|$", self.battery_event) else None

    def rotate(self, deg, count):
        """제자리 회전 명령만 보냄 (완료를 기다리지 않음). 기다려야 하면 rotate_wait() 사용"""
        self.current_dir = 0
        self.spin_done.clear()
        self._send(f"CMD|9|{deg}|{count}|$")

    def rotate_wait(self, deg, count, settle=TURN_SETTLE_SEC):
        """제자리 회전 후 로봇의 완료 신호(CMD|9|DONE)를 받을 때까지 대기 + 안정화 시간.
        완료 신호가 없는 구버전 펌웨어면 계산 시간의 2배까지 기다림"""
        self.rotate(deg, count)
        end = time.time() + count * 0.05 * 2 + 2.0
        while time.time() < end and not self.spin_done.is_set():
            p0 = pause_total()
            check_emergency()               # 회전 중 터치 일시정지 -> 재개 후 로봇이 남은 회전을 마저 함
            end += pause_total() - p0
            time.sleep(0.02)
        if not self.spin_done.is_set():
            print("    (회전 완료 신호 없음 - 시간으로 대기함. 펌웨어 확인 필요)")
        time.sleep(settle)

    def clear_touch(self):
        """이전에 눌린 기록 비우기 (조회하면 로봇 쪽 플래그가 리셋됨)
        늦게 도착하는 응답이 다음 조회 결과로 오인되지 않도록 잠시 기다린 뒤 비움"""
        self._query("CMD|11|1|$", self.touch_event)
        time.sleep(0.5)
        self.touch_event.clear()
        self.latest_touch = 0

    def set_trim(self, trim, speed=None):
        """직진 쏠림 보정값(과 속도)을 로봇에 전송 (CMD|13|trim|speed|$)"""
        speed = CALIB.get("straight_speed", 120) if speed is None else speed
        for _ in range(2):
            self._send(f"CMD|13|{int(trim)}|{int(speed)}|$")
            time.sleep(0.05)

    def led(self, state):
        """센서 LED를 상태 색으로 변경 (같은 색이면 다시 보내지 않음)"""
        if not LED_ENABLED or state not in LED or getattr(self, "_led_state", None) == state:
            return
        self._led_state = state
        r, g, b = LED[state]
        for _ in range(2):  # UDP 유실 대비 2회
            self._send(f"CMD|4|3|{r}|{g}|{b}|$")

    def action(self, num):
        self._send(f"CMD|2|1|{num}|$")

    def close(self):
        self.stop()
        self.running = False
        time.sleep(0.3)
        self.sock.close()


# ===== MQTT =====
class MqttPub:
    def __init__(self):
        self.client = None
        try:
            import paho.mqtt.client as mqtt
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            c.connect(MQTT_BROKER, MQTT_PORT, 60)
            c.loop_start()
            self.client = c
            print("MQTT 연결됨")
        except Exception as e:
            print(f"[MQTT 연결 실패 - 발행 없이 이동만 진행] {e}")

    def publish(self, topic, message, retain=False):
        if self.client is None:
            return
        try:
            info = self.client.publish(topic, json.dumps(message, ensure_ascii=False), retain=retain)
            info.wait_for_publish(1.0)
        except Exception as e:
            print(f"[MQTT 발행 실패] {topic}: {e}")

    def close(self):
        if self.client is not None:
            self.client.loop_stop()
            self.client.disconnect()


mqtt_pub = None


def publish_status(status, detail="", context=None):
    """escort.status - status: idle / moving / arrived (팀 표준), context: outbound / reception"""
    message = {"robot_id": ROBOT_ID, "status": status, "detail": detail}
    if context:
        message["context"] = context
    mqtt_pub.publish("escort.status", message, retain=True)


def save_snapshot(tag):
    """현재 카메라 화면을 snapshots 폴더에 저장하고 경로 반환 (카메라 없으면 None)"""
    if tracker is None:
        return None
    frame = tracker.latest_frame(max_age=2.0)
    if frame is None:
        return None
    try:
        import cv2
        import os
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        path = os.path.join(SNAPSHOT_DIR, f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
        cv2.imwrite(path, frame)
        print(f"    [사진 저장] {path}")
        return path
    except Exception as e:
        print(f"    [사진 저장 실패] {e}")
        return None


def publish_alert(reason, detail=""):
    """alert.event 발행 + 그 순간 카메라 사진 저장 (alert_logs.snapshot_path)"""
    msg_id = f"mechdog_c-{int(time.time() * 1000)}"
    message = {"msg_id": msg_id, "src": "mechdog_c", "level": "WARNING", "reason": reason, "detail": detail}
    snap = save_snapshot(reason)
    if snap:
        message["snapshot_path"] = snap
    mqtt_pub.publish("alert.event", message)


# ===== 에스코트 로직 =====
def avoid_obstacle(robot, attempt):
    """자동 확장형 S자 회피 (BLE 버전과 동일한 수치)"""
    backup = 2.0 + 1.0 * (attempt - 1)
    turn = 0.6 + 0.2 * (attempt - 1)
    sidestep = 0.5 * attempt

    print(f"    회피 {attempt}차: ① 후진 {backup:.1f}초")
    robot.move(7); interruptible_sleep(backup)
    print(f"    회피 {attempt}차: ② 우측 전진 {turn:.1f}초")
    robot.move(1); interruptible_sleep(turn)
    print(f"    회피 {attempt}차: ③ 옆으로 비켜가기 {sidestep:.1f}초")
    robot.move(3); interruptible_sleep(sidestep)
    print(f"    회피 {attempt}차: ④ 좌측 전진(방향 복귀) {turn:.1f}초")
    robot.move(5); interruptible_sleep(turn)
    print("    회피: ⑤ 직진(라인 복귀)")
    robot.move(3); interruptible_sleep(0.5)
    robot.stop()
    interruptible_sleep(0.2)


def is_blocked(robot):
    dist = robot.request_distance()
    if dist is None:
        dist = robot.request_distance()  # 응답 유실 대비 1회 재시도
    return dist is not None and dist < OBSTACLE_THRESHOLD, dist


person_det = None  # person_detector.PersonDetector (main에서 시작)


def person_close():
    """이동 중 전방에 사람이 가까이(크게) 보이는지"""
    return bool(person_det and person_det.person_ahead(max_age=0.8, min_area=PERSON_STOP_AREA))


def person_recent():
    """최근 PERSON_RECENT_SEC 안에 전방에 사람이 보였는지 (거리센서 감지 시점에는 발만 보여 인식이 어려움)"""
    return bool(person_det and person_det.person_ahead(max_age=PERSON_RECENT_SEC, min_area=2.0))


def wait_for_person(robot, context, why):
    """사람이 비켜갈 때까지 정지 대기. 비키면 True, 시간 초과면 False"""
    robot.stop()
    robot.led("person")
    print(f"    [사람 감지] {why} - 비켜갈 때까지 대기 (최대 {PERSON_WAIT_SEC:.0f}초)")
    publish_status("moving", f"보행자 대기 중 ({why})", context=context)
    save_snapshot("person_wait")
    waited = 0.0
    clear_count = 0
    while waited < PERSON_WAIT_SEC:
        interruptible_sleep(0.5)
        waited += 0.5
        dist = robot.request_distance()
        path_clear = dist is None or dist >= OBSTACLE_THRESHOLD
        no_person = not (person_det and person_det.person_ahead(max_age=0.8, min_area=PERSON_STOP_AREA))
        clear_count = clear_count + 1 if (path_clear and no_person) else 0
        if clear_count >= 2:  # 1초 연속으로 비어 있으면 출발
            print(f"    [사람 감지] 길이 비었음 ({waited:.1f}초 대기) - 이동 재개")
            return True
    print("    [사람 감지] 대기 시간 초과 - 회피 기동으로 전환")
    return False


def execute_step(robot, dir_code, hold_sec, context, stop_if=None):
    """stop_if: 호출 시 True면 이 스텝을 즉시 끝내고 True 반환 (예: 복귀 중 공이 가까워짐)"""
    move_color = "return" if context == "reception" else "moving"
    robot.led(move_color)
    robot.move(dir_code)
    elapsed = 0.0
    last_publish = -1.0

    while elapsed < hold_sec:
        check_emergency()
        if elapsed - last_publish >= 1.0:
            publish_status("moving", "웨이포인트 이동 중", context=context)
            last_publish = elapsed

        if stop_if is not None and stop_if():
            robot.stop()
            return True

        t0 = time.time()

        # (1) 거리센서보다 먼저: 카메라로 전방에 사람이 가까이 보이면 미리 멈추고 대기
        if person_close():
            wait_for_person(robot, context, "전방에 사람 접근")
            robot.led(move_color)
            robot.move(dir_code)
            t0 = time.time()  # 대기 시간은 경로 진행 시간에서 제외

        blocked, dist = is_blocked(robot)
        # (2) 거리센서 감지: 방금 전 사람이 보였으면 사람으로 보고 대기 -> 비켜가면 그대로 진행
        if blocked and person_recent():
            robot.stop()
            print(f"    [장애물] {dist:.0f}cm - 사람으로 판단")
            wait_for_person(robot, context, f"{dist:.0f}cm 앞 사람")
            blocked, dist = is_blocked(robot)
            if not blocked:
                robot.led(move_color)
                robot.move(dir_code)
                t0 = time.time()
        # (3) 사람이 아니거나 계속 막혀 있으면 기존 자동 확장형 S자 회피
        if blocked:
            print(f"    [경고] 장애물 {dist:.0f}cm (물체) - 회피 시작")
            robot.led("avoid")
            publish_status("moving", f"{dist:.0f}cm 장애물 회피 기동", context=context)
            for attempt in range(1, MAX_AVOID_ATTEMPTS + 1):
                avoid_obstacle(robot, attempt)
                blocked, dist = is_blocked(robot)
                if not blocked:
                    print(f"    회피 성공 ({attempt}차), 이동 재개")
                    break
                print(f"    아직 막힘 ({attempt}/{MAX_AVOID_ATTEMPTS})")
            else:
                robot.stop()
                robot.led("stop")
                raise EscortLost(f"{MAX_AVOID_ATTEMPTS}회 회피 시도 후에도 막힘")
            robot.led(move_color)
            robot.move(dir_code)
            t0 = time.time()  # 회피 시간은 경로 진행 시간에서 제외

        p_before = pause_total()
        interruptible_sleep(max(0.0, CHECK_INTERVAL - (time.time() - t0)))
        paused_here = pause_total() - p_before
        elapsed += max(CHECK_INTERVAL, time.time() - t0 - paused_here)  # 일시정지 시간은 제외

    robot.stop()
    return False


def run_steps(robot, steps, context, label, stop_if=None):
    for i, step in enumerate(steps, 1):
        if "wait" in step:  # 경로 중간 대기 스텝 {"wait": 초}
            robot.stop()
            print(f"  [{label} {i}/{len(steps)}] {step['wait']}초 대기")
            interruptible_sleep(step["wait"])
            continue
        if "spin" in step and context == "outbound" and i > 1 and CORNER_PAUSE_SEC > 0:
            robot.stop()  # 모퉁이에서 방문자가 따라올 시간
            print(f"    (모퉁이: 방문자 대기 {CORNER_PAUSE_SEC}초)")
            interruptible_sleep(CORNER_PAUSE_SEC)
        if "spin" in step:  # 제자리 회전 스텝 (거리 설정 turn / 기록 모드 J,L)
            print(f"  [{label} {i}/{len(steps)}] {step.get('desc', '제자리 회전')} (spin={step['spin']}, count={step['count']})")
            robot.stop()
            interruptible_sleep(TURN_SETTLE_SEC)       # 직진 관성 멈춘 뒤 회전
            robot.rotate_wait(step["spin"], step["count"])  # 실제 회전 완료까지 대기
            continue
        print(f"  [{label} {i}/{len(steps)}] {step['desc']} (dir={step['dir']}, {step['hold_sec']}초)")
        if execute_step(robot, step["dir"], step["hold_sec"], context, stop_if):
            print(f"  [{label}] 조기 종료 (카메라 조건 충족)")
            return True
    return False


tracker = None  # ball_vision.BallTracker (main에서 시작)


def ball_close():
    det = tracker.get() if tracker else None
    return det is not None and det[1] >= R_STOP


def search_turn(robot, direction=1):
    """탐색용 1회 회전. direction: +1=좌, -1=우 (spin 모드만 방향 적용)"""
    if SEARCH_MODE == "spin":
        robot.rotate_wait(SPIN_DEG * direction, SPIN_COUNT, settle=0.2)
    else:
        robot.move(SEARCH_DIR)
        interruptible_sleep(SEARCH_PULSE)
        robot.stop()


def home_to_ball(robot):
    """멈추고-보고-움직이기 방식으로 초록 공에 접근. r >= R_STOP 에서 정지하면 True
    1) 정지 후 최신 영상 확인  2) 공이 옆이면 제자리 회전으로 정렬
    3) 정면이면 짧게 직진  4) 반복. 공이 안 보이면 제자리 회전 탐색"""
    if tracker is None or not tracker.connected:
        print("  [카메라 복귀] 카메라 미연결 - 건너뜀")
        return False

    print("  [카메라 복귀] 초록 공 탐색 시작")
    publish_status("moving", "출발지 표식(초록 공) 탐색", context="reception")
    start = time.time()
    pulses = 0
    search_dir = 1          # 공이 마지막으로 보인 쪽으로 탐색 (+1 좌, -1 우)
    last_align = None       # 직전 정렬 회전 (deg, count) - 넘쳐서 놓쳤을 때 되돌리기용

    while time.time() - start < HOMING_TIMEOUT:
        check_emergency()
        robot.stop()
        interruptible_sleep(SETTLE_SEC)            # 흔들림 + 영상 지연 가라앉힌 뒤
        tracker.wait_new_frames(NEW_FRAMES)        # 멈춘 뒤 찍힌 영상이 도착할 때까지
        det = tracker.get(max_age=FRESH_SEC)

        if det is None and tracker.has_candidate():  # 후보만 보이면 확인될 때까지 잠시 대기
            waited = 0.0
            while waited < CANDIDATE_WAIT and det is None:
                interruptible_sleep(0.1)
                waited += 0.1
                det = tracker.get(max_age=FRESH_SEC)

        if det is None:
            cand = tracker.candidate_offset()
            if cand is not None:  # 확인은 안 됐어도 후보가 보인 쪽으로 탐색
                search_dir = 1 if cand < 0 else -1
            if last_align is not None and cand is None:  # 정렬하다 넘어가서 놓친 것 -> 반대로 절반 되돌림
                deg, count = last_align
                back = max(ALIGN_COUNT_MIN, count // 2)
                print(f"    정렬 후 놓침 -> 반대로 {back} 되돌림")
                robot.rotate(-deg, back)
                interruptible_sleep(back * 0.05 + 0.2)
                search_dir = -1 if deg > 0 else 1
                last_align = None
                continue
            last_align = None
            if pulses >= MAX_SEARCH_PULSES:
                break
            pulses += 1
            print(f"    탐색 {pulses}/{MAX_SEARCH_PULSES} ({SEARCH_MODE}, {'좌' if search_dir > 0 else '우'})  [{tracker.diag()}]")
            search_turn(robot, search_dir)
            continue

        pulses = 0  # 공을 봤으면 탐색 횟수 초기화
        last_align = None
        offset, r = det
        search_dir = 1 if offset < 0 else -1  # 다음에 놓치면 공이 있던 쪽으로 탐색
        if r >= R_STOP:
            print(f"  [카메라 복귀] 도착 (r={r:.1f}px)")
            return True

        if abs(offset) > ALIGN_DEADBAND:
            # 제자리 회전으로 정렬 (offset이 클수록 많이 회전). offset<0(공이 왼쪽) -> 좌회전(+)
            count = int(min(ALIGN_COUNT_MAX, max(ALIGN_COUNT_MIN, abs(offset) * ALIGN_GAIN)))
            deg = SPIN_DEG if offset < 0 else -SPIN_DEG
            print(f"    정렬: offset={offset:+.2f} r={r:.1f}px -> {'좌' if deg > 0 else '우'}회전 {count}")
            robot.rotate(deg, count)
            interruptible_sleep(count * 0.05 + 0.2)
            last_align = (deg, count)
            continue

        dist = robot.poll_distance()
        if dist is not None and dist < OBSTACLE_THRESHOLD:
            print(f"  [카메라 복귀] 전방 {dist:.0f}cm 장애물 - 정지")
            return False
        step = APPROACH_STEP_NEAR if r >= R_NEAR else APPROACH_STEP_FAR
        print(f"    전진: offset={offset:+.2f} r={r:.1f}px -> {step}초")
        robot.move(3)
        interruptible_sleep(step)

    robot.stop()
    print("  [카메라 복귀] 초록 공을 찾지 못함")
    return False

def turn_around(robot, why, context="reception", stop_if=None):
    print(f"  U턴 ({why}, dir={UTURN_DIR}, {UTURN_SEC}초)")
    return execute_step(robot, UTURN_DIR, UTURN_SEC, context, stop_if)  # 장애물 감지/keepalive 적용


def build_return_steps(steps):
    """180도 돌아선 상태 기준: 역순 + 좌우반전"""
    return [{"dir": DIR_MIRROR.get(s["dir"], s["dir"]), "hold_sec": s["hold_sec"],
             "desc": f"복귀: {s['desc']}"} for s in reversed(steps)]


def goto_destination(robot, dest_id, destinations):
    dest = destinations[dest_id]
    name = dest["name"]
    print(f"\n===== '{name}'({dest_id})로 출발 =====")
    publish_status("moving", f"{name} 방향으로 출발", context="outbound")

    try:
        robot.escort_mode(True)  # 이동 중 터치 = 일시정지
        run_steps(robot, dest["steps"], "outbound", "이동")
        robot.stop()
        robot.escort_mode(False)  # 도착 후에는 터치 = '다음 안내'
        interruptible_sleep(0.5)  # [수정] 정지 명령 처리 후 인사 명령 전송 (바로 보내면 펌웨어에서 덮어써져 씹힘)
        robot.led("arrived")
        print(f"'{name}' 도착! 인사 동작 실행")
        robot.action(8)  # scrape_a_bow
        interruptible_sleep(BOW_WAIT_SECONDS)
        publish_status("arrived", f"{name} 도착, 인사동작 재생")

        robot.clear_touch()  # 도착 전에 눌린 기록 때문에 바로 복귀하는 것 방지
        print(f"터치센서를 누르면 바로 복귀합니다 (최대 {DWELL_SECONDS}초 대기)")
        touched = False
        for _ in range(int(DWELL_SECONDS / 0.5)):
            if robot.request_touch():
                touched = True
                print("터치 확인!")
                break
            interruptible_sleep(0.5)
        if not touched:
            print("터치 없음 - 시간 초과로 복귀")

        print("\n===== 출발지로 복귀 =====")
        publish_status("moving", f"{name}에서 출발지로 복귀 중", context="reception")
        robot.escort_mode(True)   # 복귀 중에도 터치로 멈출 수 있음
        stop_if = ball_close if HOMING_ENABLED else None
        if dest.get("return_steps"):  # 기록 모드로 직접 기록한 복귀 경로 (U턴 계산 불필요)
            run_steps(robot, dest["return_steps"], "reception", "복귀", stop_if=stop_if)
        else:                         # 기존 방식: U턴 + 역순/좌우반전 경로
            turn_around(robot, "출발지 방향으로", stop_if=stop_if)
            if not (HOMING_ENABLED and ball_close()):
                run_steps(robot, build_return_steps(dest["steps"]), "reception", "복귀", stop_if=stop_if)
        robot.stop()
        if HOMING_ENABLED and not home_to_ball(robot):
            raise EscortLost("카메라 복귀 실패 (초록 공 미발견/장애물)")
        if FINAL_UTURN:
            turn_around(robot, "다음 안내 방향으로 정렬")

        robot.escort_mode(False)
        robot.led("idle")
        publish_status("idle", "출발지 복귀 후 다음 방문자 안내 준비 완료")
        print("===== 복귀 완료, 다음 안내 준비 =====\n")

    except EscortLost as e:
        robot.escort_mode(False)
        robot.stop()
        robot.led("stop")
        print(f"\n[에스코트 중단] {e} - 로봇 위치를 수동으로 확인하세요 (출발지 아님)")
        publish_alert("escort_lost", str(e))
    except EmergencyStop:
        robot.escort_mode(False)
        robot.stop()
        robot.led("stop")
        print("\n[비상정지] 이동 중단 - 로봇 위치를 수동으로 확인하세요")
        publish_alert("emergency_stop", "사용자에 의해 이동 중단")


# ===== 거리 기반 경로 (demo_routes.json) =====
CALIB = {"speed_cm_per_s": 22.5, "back_speed_cm_per_s": 7.5, "spin_count_per_90": 130, "spin_count_per_180": None,
         "straight_trim": 0, "straight_speed": 120}
CORNER_PAUSE_SEC = 1.5  # 목적지로 갈 때 회전 직전에 멈춰서 방문자를 기다리는 시간 (0이면 안 멈춤)
TRIM_PER_CM = 0.3   # 1.5m 직진에서 옆으로 1cm 벗어날 때 trim 조정량 (추정값, 반복 보정으로 수렴)
TRIM_LIMIT = 15     # trim 최대 크기 (약회전이 25라 그보다 충분히 작게)


def spin_count_for(deg):
    """회전 각도 -> CMD|9 count. 회전량이 count에 비례하지 않으므로 90도/180도 실측값으로 보간"""
    a = abs(deg)
    c90 = CALIB["spin_count_per_90"]
    c180 = CALIB.get("spin_count_per_180") or c90 * 2
    if a <= 90:
        return max(1, round(c90 * a / 90))
    return round(c90 + (c180 - c90) * (a - 90) / 90)


def distance_to_steps(path):
    """[{"go":cm}, {"back":cm}, {"turn":deg}] -> 재생용 스텝"""
    steps = []
    for seg in path:
        if "go" in seg:
            sec = round(seg["go"] / CALIB["speed_cm_per_s"], 2)
            steps.append({"dir": 3, "hold_sec": sec, "desc": f"직진 {seg['go']}cm"})
        elif "back" in seg:
            sec = round(seg["back"] / CALIB["back_speed_cm_per_s"], 2)
            steps.append({"dir": 7, "hold_sec": sec, "desc": f"후진 {seg['back']}cm"})
        elif "wait" in seg:
            steps.append({"wait": seg["wait"], "desc": f"{seg['wait']}초 대기"})
        elif "turn" in seg:
            deg = seg["turn"]
            steps.append({"spin": SPIN_DEG if deg > 0 else -SPIN_DEG, "count": spin_count_for(deg),
                          "desc": f"{'좌' if deg > 0 else '우'}회전 {abs(deg)}도"})
    return steps


def auto_return_path(path):
    """180도 회전 -> 갔던 길 역순(회전 방향 반대) -> 180도 회전 (제자리 회전이라 U턴 반경 문제 없음)"""
    back = [{"turn": 180}]
    for seg in reversed(path):
        if "wait" in seg:
            continue  # 복귀는 혼자 가므로 대기 생략
        back.append({"turn": -seg["turn"]} if "turn" in seg else dict(seg))
    back.append({"turn": 180})
    return back


def load_distance_routes():
    import os
    if not os.path.exists(DISTANCE_FILE):
        return {}
    with open(DISTANCE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    CALIB.update({k: v for k, v in data.get("_calibration", {}).items() if not k.startswith("_")})
    routes = {}
    for code, r in data.items():
        if code.startswith("_"):
            continue
        ret = r.get("return") or auto_return_path(r["path"])
        routes[code] = {"name": r.get("name", code), "alias": r.get("alias", []),
                        "steps": distance_to_steps(r["path"]),
                        "return_steps": distance_to_steps(ret), "source": "거리 설정"}
    return routes


def load_all_routes():
    import os
    recorded = {}
    for f in (DEST_FILE, DEST_FILE_FALLBACK):
        if os.path.exists(f):
            with open(f, "r", encoding="utf-8") as fp:
                recorded = {k: dict(v, source=f) for k, v in json.load(fp).items() if not k.startswith("_")}
            break
    distance = load_distance_routes()
    routes = {**recorded, **distance} if ROUTE_PRIORITY == "distance" else {**distance, **recorded}
    print(f"경로: 거리 설정 {len(distance)}개, 기록 {len(recorded)}개 "
          f"(보정값: 속도 {CALIB['speed_cm_per_s']}cm/s, 90도={CALIB['spin_count_per_90']})")
    return routes


def save_calibration(key, value):
    """demo_routes.json 의 _calibration 값 갱신 (시연 장소 보정 결과 저장)"""
    import os
    import shutil
    if os.path.exists(DISTANCE_FILE):
        shutil.copy(DISTANCE_FILE, DISTANCE_FILE + ".bak")
        with open(DISTANCE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}
    data.setdefault("_calibration", {})[key] = value
    CALIB[key] = value
    with open(DISTANCE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  보정값 저장: {key} = {value}  ({DISTANCE_FILE}, 이전 파일 .bak)")


def ask_number(prompt):
    """숫자 입력 받기. '95cm', '87도', ' 92.5 ' 처럼 단위가 붙어도 숫자만 사용.
    Enter만 누르면 None(취소), 숫자가 없으면 다시 물어봄"""
    import re
    while True:
        text = input(prompt).strip()
        if not text:
            return None
        m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", "."))
        if m:
            return float(m.group())
        print("  숫자를 입력해주세요 (예: 95)")


def ask_side(prompt):
    """'왼쪽 10', '오른쪽 5cm', '10', '-5', '0' -> 왼쪽 +, 오른쪽 - (Enter: None)"""
    import re
    while True:
        text = input(prompt).strip()
        if not text:
            return None
        m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", "."))
        if not m:
            print("  예: 왼쪽 10 / 오른쪽 5 / 0")
            continue
        v = abs(float(m.group())) if ("왼" in text or "오른" in text) else float(m.group())
        return -v if "오른" in text else v


def calibrate(robot, kind, amount):
    """시연 장소 보정: 지정한 거리/각도로 움직인 뒤 실제 측정값을 입력하면 기준값 자동 수정"""
    if kind in ("go", "back"):
        key = "speed_cm_per_s" if kind == "go" else "back_speed_cm_per_s"
        sec = amount / CALIB[key]
        print(f"  {amount}cm {'직진' if kind == 'go' else '후진'} 시도 ({sec:.2f}초). 출발 위치를 표시하세요 - 3초 후 출발")
        time.sleep(3)
        execute_step(robot, 3 if kind == "go" else 7, sec, "outbound")
        actual = ask_number("  실제 이동 거리(cm) 입력 (취소: Enter): ")
        if actual is not None and actual > 0:
            save_calibration(key, round(CALIB[key] * actual / amount, 2))
    elif kind == "straight":
        dist = amount or 150
        trim = CALIB.get("straight_trim", 0)
        sec = dist / CALIB["speed_cm_per_s"]
        robot.set_trim(trim)
        print(f"  직진 쏠림 보정 (현재 trim={trim}). {dist:.0f}cm 직진 - 출발 위치와 방향을 표시하세요, 3초 후 출발")
        time.sleep(3)
        execute_step(robot, 3, sec, "outbound")
        side = ask_side("  옆으로 벗어난 거리 입력 (예: 왼쪽 10 / 오른쪽 5 / 0, 취소: Enter): ")
        if side is not None:
            new = int(round(trim - side * TRIM_PER_CM * 150 / dist))
            new = max(-TRIM_LIMIT, min(TRIM_LIMIT, new))
            save_calibration("straight_trim", new)
            robot.set_trim(new)
            if abs(side) > 3:
                print("  3cm 이상 벗어났으면 'calib straight'를 한 번 더 해서 확인하세요")
    elif kind == "turn":
        count = spin_count_for(amount)
        print(f"  {amount}도 회전 시도 (count {count}). 머리 방향을 표시하세요 - 3초 후 회전")
        time.sleep(3)
        robot.rotate_wait(SPIN_DEG if amount > 0 else -SPIN_DEG, count)  # 경로 실행과 같은 방식
        actual = ask_number("  실제 회전 각도(도) 입력 (취소: Enter): ")
        if actual is not None and actual > 0:
            key = "spin_count_per_90" if abs(amount) <= 90 else "spin_count_per_180"
            target = 90 if key == "spin_count_per_90" else 180
            new = round(count * abs(amount) / actual * target / abs(amount))
            save_calibration(key, new)


def print_destinations(destinations):
    print("목적지 (번호, 이름, 별명, 코드 중 아무거나 입력):")
    for i, (code, r) in enumerate(destinations.items(), 1):
        alias = r.get("alias") or []
        alias = [alias] if isinstance(alias, str) else alias
        extra = f" / 별명: {', '.join(alias)}" if alias else ""
        print(f"  {i}. {r['name']}{extra}   [{code}]")


def resolve_destination(text, destinations):
    """번호 / 표시 이름 / 별명 / 코드 -> 목적지 코드 (못 찾으면 None)"""
    t = text.strip()
    if not t:
        return None
    codes = list(destinations)
    if t.isdigit() and 1 <= int(t) <= len(codes):
        return codes[int(t) - 1]
    norm = lambda x: x.replace(" ", "").lower()
    for code, r in destinations.items():
        alias = r.get("alias") or []
        alias = [alias] if isinstance(alias, str) else alias
        if norm(t) in [norm(code), norm(r.get("name", ""))] + [norm(a) for a in alias]:
            return code
    return None


def main():
    global mqtt_pub, tracker, person_det

    destinations = load_all_routes()

    global ROBOT
    robot = RobotLink(ROBOT_IP, ROBOT_PORT)
    ROBOT = robot
    mqtt_pub = MqttPub()

    if CAMERA_ENABLED or HOMING_ENABLED:
        from ball_vision import BallTracker
        tracker = BallTracker()
        tracker.start()
        for _ in range(150):  # 카메라 설정 시도 포함 최대 15초 대기
            if tracker.connected:
                break
            time.sleep(0.1)
        print("카메라 연결됨" if tracker.connected else "[경고] 카메라 연결 안 됨 - 브라우저 스트림 창을 닫았는지 확인")
        if PERSON_DETECT:
            from person_detector import PersonDetector
            person_det = PersonDetector(tracker)
            person_det.start()
            for _ in range(200):  # 모델 로딩 대기 (최대 20초)
                if person_det.ready or not person_det.is_alive():
                    break
                time.sleep(0.1)

    try:
        battery = robot.request_battery()
        if battery is None:
            print(f"[경고] 로봇({ROBOT_IP}) 응답 없음 - IP/핫스팟 연결을 확인하세요.")
            print("       (로봇이 명령은 받는데 응답만 못 오는 경우도 있음 -> 거리센서 판단 불가, 주의)")
        else:
            print(f"로봇 연결 확인 (배터리: {battery})")
        robot.set_trim(CALIB.get("straight_trim", 0))  # 직진 쏠림 보정값 적용

        robot.led("idle")
        publish_status("idle", "안내 요청 대기 중")
        print_destinations(destinations)
        print("[안내] 이동 중 'q' 키(Enter 불필요) 또는 Ctrl+C로 즉시 정지\n")

        while not stop_event.is_set():
            dest_id = input("목적지 입력 (번호/이름/별명, 종료: q): ").strip()
            if dest_id.lower() == "q":
                break
            if dest_id.lower().startswith("calib"):  # 시연 장소 보정: calib go 100 / calib back 50 / calib turn 90 / calib turn 180
                parts = dest_id.split()
                if len(parts) == 2 and parts[1] == "straight":
                    parts.append("150")
                if len(parts) == 3 and parts[1] in ("go", "back", "turn", "straight"):
                    try:
                        calibrate(robot, parts[1], float(parts[2]))
                        destinations = load_all_routes()  # 새 보정값으로 경로 다시 계산
                    except (EmergencyStop, EscortLost) as e:
                        robot.stop()
                        print(f"중단: {e}")
                        stop_event.clear()
                else:
                    print("사용법: calib go 100 | calib straight | calib turn 90 | calib turn 180 | calib back 50")
                continue
            if dest_id.lower() == "led":  # 상태별 LED 색 확인
                for st in LED:
                    print(f"  {st}: {LED[st]}")
                    robot.led(st)
                    time.sleep(1.5)
                robot.led("idle")
                continue
            if dest_id.lower().startswith("speed"):  # 직진 속도 시험: speed 150
                parts = dest_id.split()
                if len(parts) == 2 and parts[1].isdigit():
                    new_speed = max(60, min(200, int(parts[1])))
                    old_speed = CALIB.get("straight_speed", 120)
                    robot.set_trim(CALIB.get("straight_trim", 0), new_speed)
                    print(f"  속도 {old_speed} -> {new_speed} 로 3초간 직진합니다. 걸음이 안정적인지 보세요")
                    time.sleep(1)
                    try:
                        execute_step(robot, 3, 3.0, "outbound")
                    except (EmergencyStop, EscortLost) as ex:
                        robot.stop()
                        print(f"중단: {ex}")
                        stop_event.clear()
                    if input("  이 속도로 저장할까요? (y/n): ").strip().lower() == "y":
                        save_calibration("straight_speed", new_speed)
                        # 거리 기준을 속도 비율로 추정 갱신 -> calib straight, calib go 100 으로 다시 맞추기
                        save_calibration("speed_cm_per_s", round(CALIB["speed_cm_per_s"] * new_speed / old_speed, 2))
                        print("  저장 완료. 이제 'calib straight' -> 'calib go 100' 순서로 다시 보정하세요")
                        destinations = load_all_routes()
                    else:
                        robot.set_trim(CALIB.get("straight_trim", 0), old_speed)
                        print(f"  속도 {old_speed} 유지")
                else:
                    print("사용법: speed 150 (60~200)")
                continue
            if dest_id.lower() == "routes":  # 현재 경로 확인
                for code, r in destinations.items():
                    print(f"  {code} ({r['name']}, {r.get('source', '')}): "
                          + " -> ".join(st.get("desc", "") for st in r["steps"]))
                continue
            if dest_id.lower() == "touch":  # 터치센서 오감지 진단 (이동 없음)
                print("20초간 터치센서 값 확인 - 처음 10초는 만지지 말고, 이후 10초는 몇 번 눌러보세요")
                robot.clear_touch()
                for i in range(40):
                    t = robot.request_touch()
                    phase = "만지지 않음" if i < 20 else "눌러보기"
                    print(f"  {i * 0.5:4.1f}초 [{phase}] {'눌림' if t else '-'}")
                    time.sleep(0.5)
                continue
            if dest_id.lower() == "bow":  # 인사 동작 중 진동으로 터치 오감지 되는지 진단
                robot.clear_touch()
                print("인사 동작 실행 - 만지지 마세요")
                robot.action(8)
                time.sleep(BOW_WAIT_SECONDS)
                print("인사 후 터치 기록:", "눌림 (진동 오감지 의심)" if robot.request_touch() else "없음 (정상)")
                continue
            if dest_id.lower() == "spin":  # 제자리 회전(CMD|9) 동작 확인 - 3회
                for i in range(3):
                    print(f"  제자리 회전 {i + 1}/3 (deg={SPIN_DEG}, count={SPIN_COUNT})")
                    robot.rotate(SPIN_DEG, SPIN_COUNT)
                    time.sleep(SPIN_COUNT * 0.05 + 0.8)
                print("  로봇이 앞으로 거의 안 가고 돌기만 했다면 SEARCH_MODE = \"spin\" 사용 가능")
                continue
            if dest_id.lower() == "home":  # 카메라 복귀만 단독 테스트
                try:
                    home_to_ball(robot)
                except (EmergencyStop, EscortLost) as e:
                    robot.stop()
                    print(f"중단: {e}")
                    stop_event.clear()
                continue
            if dest_id.lower() == "uturn":  # U턴 보정용 단독 테스트
                try:
                    turn_around(robot, "단독 테스트")
                except (EmergencyStop, EscortLost) as e:
                    robot.stop()
                    print(f"중단: {e}")
                    stop_event.clear()
                continue
            code = resolve_destination(dest_id, destinations)
            if code is None:
                if dest_id:
                    print(f"알 수 없는 목적지: {dest_id}")
                print_destinations(destinations)
                continue
            goto_destination(robot, code, destinations)

    except KeyboardInterrupt:
        print("\nCtrl+C - 정지합니다")
    finally:
        robot.close()
        mqtt_pub.close()
        if person_det:
            person_det.stop()
        if tracker:
            tracker.stop()
        print("종료")


if __name__ == "__main__":
    main()
