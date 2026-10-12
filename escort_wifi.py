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
ROBOT_IP = "192.168.45.194"
ROBOT_PORT = 9027
DISTANCE_FILE = "demo_routes.json"      # 거리/각도로 적은 시연 경로 (시연 장소에서 숫자만 수정)
DEST_FILE = "destinations.json"          # record_route.py 로 기록한 경로 (팀 목적지 코드 기준)
ROUTE_PRIORITY = "distance"              # 같은 목적지가 두 파일에 다 있으면: "distance" 또는 "recorded" 우선
DEST_FILE_FALLBACK = "destinations_ble.json"  # 기록 파일이 없으면 기존 BLE 웨이포인트 사용

MQTT_BROKER = "localhost"   # 우리 Docker 브로커 (우리 DB 브릿지용)
MQTT_PORT = 1883

# ===== 팀 공용 MQTT 브로커 (TEAM_HANDOFF.md 기준) =====
TEAM_MQTT_ENABLED = True
TEAM_MQTT_HOST = "192.168.45.222"      # 분석 PC 주소 (네트워크 바뀌면 담당자에게 새 주소 받기)
TEAM_MQTT_PORT = 11883
TEAM_MQTT_USER = "mechdog_c"           # MechDog C 전용 계정 (B 안내 기준, 다른 팀 계정 사용 금지)
TEAM_CRED_FILE = "credentials.env"     # 비밀번호 파일 (깃 제외) - 이 중 아래 키만 사용
TEAM_CRED_KEY = "MECHDOG_C_PASS"
TEAM_SUBSCRIBE = []   # A 판정(gatekeeper/access/decision)은 C 동작에 쓰지 않아 구독 안 함 (필요 시 추가)
# ===== 자동 출발 트리거 (B파트 목적지 확정 -> C 자동 출발) =====
TRIGGER_ENABLED = True
# B->C 인계 계약 (MECHDOG_B_TO_C_DIALOG_HANDOFF.md)
TEAM_TRIGGER_TOPIC = "mechdog/v1/dialog/result"   # 팀 브로커, 발행자 mechdog_b, QoS 1
LOCAL_TRIGGER_TOPIC = "mechdog/v1/dialog/result"  # 우리 브로커에서 같은 형식으로 시험 (send_trigger.py)
TRIGGER_SRC = "mechdog_b"
ALLOWED_DESTINATIONS = {"reception", "meeting_room_1", "safety_training_room", "inbound_dock",
                        "outbound_dock", "inspection_area", "elevator_hall", "exit_gate"}
OPS_LOG = "escort_ops.log"          # 운영 로그 (이동 중 수신한 요청 등 - 버리지 말고 기록)
TEAM_ENVELOPE = True                # 팀 브로커 발행 시 공통 봉투(ver/msg_id/ts/src/session_id/payload) 사용
# 우리 토픽 -> 팀 브로커 토픽 이름 (팀 토픽 규약 contracts/mqtt_topics.md 확인 후 필요하면 수정)
TEAM_TOPIC_MAP = {
    "alert.event": "mechdog/v1/escort/alert",      # 경고 (escort_lost, emergency_stop, invalid_destination)
}
# 에스코트 상태는 아래 하트비트 스레드가 1초마다 발행 (B: 3초 넘게 안 오면 '안내 중'으로 판단)
TEAM_STATUS_TOPIC = "mechdog/v1/escort/status"
STATUS_HEARTBEAT_SEC = 1.0
# 우리 Docker 브로커(DB 저장용)에는 기존 이름(escort.status, alert.event) 그대로 발행
ROBOT_ID = 3

OBSTACLE_THRESHOLD = 15     # cm 이내면 장애물
DWELL_SECONDS = 8           # 도착 후 터치 대기 시간
BOW_WAIT_SECONDS = 5.5      # 인사 동작 대기 (펌웨어: 기본자세 1초 + 동작 4초)
KEEPALIVE_INTERVAL = 0.3    # 펌웨어 타임아웃 1초보다 충분히 짧게
QUERY_TIMEOUT = 1.0         # 로봇 응답 대기 (현장 공유기 왕복 0.1~0.7초 실측 -> 0.4에서 상향)
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
CAMERA_ENABLED = False   # 카메라 스트림 사용 (사람 인식, 경고 사진 저장)
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
import queue
trigger_queue = queue.Queue()   # 목적지 확정 트리거 대기열
escort_busy = [False]           # 안내 중이면 True (이 동안 들어온 트리거는 무시)
CURRENT_SESSION = [None]        # 트리거로 출발한 방문 세션 ID (상태 메시지에 포함)


ESCORT_STATE = {"state": "idle", "detail": "안내 요청 대기 중", "context": None}  # B에 1초마다 보내는 현재 상태


def ops_log(text):
    """운영 로그: 화면 출력 + escort_ops.log 파일에 시각과 함께 기록"""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}"
    print(f"\n{text}")
    try:
        with open(OPS_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_team_password():
    """credentials.env 에서 우리 계정 비밀번호 하나만 읽음 (값은 화면에 출력하지 않음)"""
    import os
    import re
    if not os.path.exists(TEAM_CRED_FILE):
        return None
    with open(TEAM_CRED_FILE, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*(?:export\s+)?" + re.escape(TEAM_CRED_KEY) + r"\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip().strip("'\"")
    return None


class MqttPub:
    """우리 브로커(DB 저장용)와 팀 브로커(타 파트 연동) 양쪽에 발행. 팀 브로커는 판정 구독도 함"""

    def __init__(self):
        self.client = None       # 우리 Docker 브로커
        self.team = None         # 팀 공용 브로커 (MQTT 5, 인증)
        self.team_ok = False
        self._seen_events = set()
        self.last_decision = None
        self._denied = set()
        self._started_sessions = set()   # 이미 출발 처리한 session_id (같은 세션 재출발 방지)
        self._seen_msg_ids = set()        # 이미 받은 msg_id (QoS 1 중복 거르기)
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            print("[MQTT] paho-mqtt 미설치 - 발행 없이 이동만 진행 (pip install paho-mqtt)")
            return

        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            c.on_message = lambda cl, ud, msg: self._on_trigger(msg, "우리 브로커")
            c.connect(MQTT_BROKER, MQTT_PORT, 60)
            if TRIGGER_ENABLED:
                c.subscribe(LOCAL_TRIGGER_TOPIC, qos=1)
            c.loop_start()
            self.client = c
            print("MQTT 연결됨 (우리 브로커)")
        except Exception as e:
            print(f"[MQTT] 우리 브로커 연결 실패 - DB 저장 없이 진행 ({e})")

        if TEAM_MQTT_ENABLED:
            self._connect_team(mqtt)
            threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    # ---------- 팀 브로커 ----------
    def _connect_team(self, mqtt):
        pw = load_team_password()
        if not pw:
            print(f"[팀 MQTT] {TEAM_CRED_FILE} 에서 {TEAM_CRED_KEY} 를 찾지 못함 - 팀 연동 생략")
            return
        try:
            t = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{TEAM_MQTT_USER}-escort",
                            protocol=mqtt.MQTTv5)
            t.username_pw_set(TEAM_MQTT_USER, pw)
            t.on_connect = self._on_team_connect
            t.on_disconnect = self._on_team_disconnect
            t.on_message = self._on_team_message
            t.on_subscribe = self._on_team_subscribe
            t.on_publish = self._on_team_publish
            t.reconnect_delay_set(1, 10)
            t.connect_async(TEAM_MQTT_HOST, TEAM_MQTT_PORT, 30)
            t.loop_start()
            self.team = t
            for _ in range(50):  # 최대 5초 연결 대기
                if self.team_ok:
                    break
                time.sleep(0.1)
            if not self.team_ok:
                print(f"[팀 MQTT] {TEAM_MQTT_HOST}:{TEAM_MQTT_PORT} 아직 연결 안 됨 - 백그라운드에서 재시도")
        except Exception as e:
            print(f"[팀 MQTT] 연결 실패 ({e}) - 팀 연동 없이 진행")

    def _on_team_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            if not getattr(self, "_refused_shown", False):  # 재접속마다 반복 출력하지 않음
                self._refused_shown = True
                print(f"[팀 MQTT] 접속 거부: {reason_code} (계정/비밀번호 확인)")
            return
        self.team_ok = True
        print(f"[팀 MQTT] 연결됨 ({TEAM_MQTT_HOST}:{TEAM_MQTT_PORT}, 계정 {TEAM_MQTT_USER})")
        for topic in TEAM_SUBSCRIBE:
            client.subscribe(topic, qos=1)
        if TRIGGER_ENABLED:
            client.subscribe(TEAM_TRIGGER_TOPIC, qos=1)

    def _on_team_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.team_ok and not getattr(self, "_closing", False):
            print(f"[팀 MQTT] 연결 끊김 ({reason_code}) - 자동 재접속 시도")
        self.team_ok = False

    def _on_team_subscribe(self, client, userdata, mid, reason_codes, properties):
        for rc in reason_codes:
            if rc.is_failure:
                print(f"[팀 MQTT] 구독 거부: {rc} (계정 권한 확인)")

    def _on_team_publish(self, client, userdata, mid, reason_code, properties):
        # QoS 1 발행 결과. 권한(ACL) 없는 토픽이면 실패 코드가 옴
        if reason_code.is_failure and str(reason_code) not in self._denied:
            self._denied.add(str(reason_code))
            print(f"[팀 MQTT] 발행 거부: {reason_code} - 이 토픽에 발행 권한이 없을 수 있음 (토픽 규약 확인 필요)")

    def _on_trigger(self, msg, source):
        """B->C 목적지 인계 (계약: 봉투 ver=1, src=mechdog_b, payload.msg_type=dialog.result).
        유효한 dialog.result 수신 자체가 목적지 확정 (outcome 필드 없음)"""
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            ops_log(f"[트리거 거부] JSON 형식 오류 ({source})")
            return
        payload = data.get("payload") or {}
        sid = data.get("session_id")
        dest = payload.get("destination")
        # 1) 계약 검증 - 다르면 이동하지 않음
        problems = []
        if data.get("ver") != 1:
            problems.append(f"ver={data.get('ver')}")
        if data.get("src") != TRIGGER_SRC:
            problems.append(f"src={data.get('src')}")
        if payload.get("msg_type") != "dialog.result":
            problems.append(f"msg_type={payload.get('msg_type')}")
        if not sid:
            problems.append("session_id 없음")
        if problems:
            ops_log(f"[트리거 거부] 계약 불일치 ({source}): {', '.join(problems)}")
            return
        # 2) msg_id 로 QoS 1 중복 거르기 (조용히 무시), 같은 세션 재출발도 막음
        mid = data.get("msg_id")
        if mid and mid in self._seen_msg_ids:
            return
        if mid:
            self._seen_msg_ids.add(mid)
            if len(self._seen_msg_ids) > 1000:
                self._seen_msg_ids.clear()
        if sid in self._started_sessions:
            ops_log(f"[트리거 중복] 이미 처리한 세션 {sid} - 재출발 안 함")
            return
        # 4) 허용되지 않은 목적지 -> 이동 안 함 + 경고
        if dest not in ALLOWED_DESTINATIONS:
            ops_log(f"[트리거 거부] 허용되지 않은 목적지 '{dest}' (세션 {sid})")
            self._started_sessions.add(sid)
            trigger_queue.put({"invalid": True, "destination": dest, "session_id": sid, "source": source})
            return
        # 3) idle 일 때만 출발. 이동 중이면 버리지 않고 운영 로그에 기록
        if escort_busy[0] or ESCORT_STATE["state"] != "idle":
            ops_log(f"[트리거 보류] 상태 {ESCORT_STATE['state']} 라서 출발 불가 - 세션 {sid}, 목적지 {dest}, 목적 '{payload.get('purpose', '')}'")
            return
        self._started_sessions.add(sid)
        ops_log(f"[트리거 수신] {source}: 세션 {sid} -> {dest} (목적 '{payload.get('purpose', '')}', "
                f"신뢰도 {payload.get('confidence')}, 재시도 {payload.get('retry_count', 0)})")
        trigger_queue.put({"destination": dest, "session_id": sid, "source": source,
                           "purpose": payload.get("purpose")})

    def _heartbeat_loop(self):
        """B파트 규약: 에스코트 상태를 1초마다 발행 (idle 중에도). 끊기면 B가 '안내 중'으로 판단"""
        while not getattr(self, "_closing", False):
            if self.team is not None and self.team_ok:
                self.publish_team_status()
            time.sleep(STATUS_HEARTBEAT_SEC)

    def publish_team_status(self):
        import uuid
        from datetime import datetime, timezone, timedelta
        st = dict(ESCORT_STATE)
        body = {"msg_type": "escort.status", "robot_id": ROBOT_ID, "state": st["state"], "detail": st["detail"]}
        if st.get("context"):
            body["context"] = st["context"]
        env = {"ver": 1, "msg_id": uuid.uuid4().hex,
               "ts": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="milliseconds"),
               "src": "mechdog_c", "session_id": CURRENT_SESSION[0], "payload": body}
        try:
            self.team.publish(TEAM_STATUS_TOPIC, json.dumps(env, ensure_ascii=False), qos=1, retain=False)
        except Exception:
            pass

    def _on_team_message(self, client, userdata, msg):
        """A파트 출입 판정 수신 (QoS 1이라 중복 가능 -> event_id 로 한 번만 처리)"""
        if msg.topic == TEAM_TRIGGER_TOPIC:
            self._on_trigger(msg, "팀 브로커")
            return
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        eid = data.get("event_id")
        if eid in self._seen_events:
            return
        if eid:
            self._seen_events.add(eid)
            if len(self._seen_events) > 500:
                self._seen_events.clear()
        self.last_decision = (time.time(), data)
        print(f"\n[A 판정 수신] {data.get('decision')} reasons={data.get('reasons')} event_id={eid}")

    # ---------- 공통 ----------
    def publish(self, topic, message, retain=False):
        payload = json.dumps(message, ensure_ascii=False)
        if self.client is not None:
            try:
                info = self.client.publish(topic, payload, retain=retain)
                info.wait_for_publish(1.0)
            except Exception as e:
                print(f"[MQTT 발행 실패] {topic}: {e}")
        if self.team is not None and self.team_ok and topic in TEAM_TOPIC_MAP:
            try:
                team_payload = payload
                if TEAM_ENVELOPE:  # 팀 공통 봉투: 원래 메시지는 payload 안에, msg_type 으로 종류 표시
                    import uuid
                    from datetime import datetime, timezone, timedelta
                    body = dict(message)
                    sid = body.pop("session_id", None)
                    body["msg_type"] = topic  # "escort.status" / "alert.event"
                    env = {"ver": 1, "msg_id": str(uuid.uuid4()),
                           "ts": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="milliseconds"),
                           "src": "mechdog_c", "session_id": sid, "payload": body}
                    team_payload = json.dumps(env, ensure_ascii=False)
                self.team.publish(TEAM_TOPIC_MAP.get(topic, topic), team_payload, qos=1, retain=retain)
            except Exception as e:
                print(f"[팀 MQTT 발행 실패] {topic}: {e}")

    def close(self):
        self._closing = True
        for c in (self.client, self.team):
            if c is not None:
                try:
                    c.loop_stop()
                    c.disconnect()
                except Exception:
                    pass


mqtt_pub = None


def publish_status(status, detail="", context=None):
    """escort.status - status: idle / moving / arrived (팀 표준), context: outbound / reception"""
    message = {"robot_id": ROBOT_ID, "status": status, "detail": detail}
    if context:
        message["context"] = context
    if CURRENT_SESSION[0]:
        message["session_id"] = CURRENT_SESSION[0]  # 팀 공용 세션 원장과 연결
    changed = ESCORT_STATE["state"] != status
    ESCORT_STATE.update({"state": status, "detail": detail, "context": context})
    mqtt_pub.publish("escort.status", message, retain=True)  # 우리 DB용 (기존 형식)
    if changed and mqtt_pub.team is not None and mqtt_pub.team_ok:
        mqtt_pub.publish_team_status()  # 상태가 바뀌면 1초 기다리지 않고 바로 알림


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
    if CURRENT_SESSION[0]:
        message["session_id"] = CURRENT_SESSION[0]  # 팀 세션 원장과 연결 (alert_logs.session_id)
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
        if "trim" in step:
            robot.set_trim(step["trim"])          # 이 구간만 다른 쏠림 보정
        done_early = execute_step(robot, step["dir"], step["hold_sec"], context, stop_if)
        if "trim" in step:
            robot.set_trim(CALIB.get("straight_trim", 0))  # 기본값으로 복귀
        if done_early:
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
        publish_status("aborted", f"에스코트 중단: {e} - 로봇을 출발지에 놓고 'idle' 입력")
        print("  상태: aborted (B파트에 '안내 중'으로 표시됨). 로봇을 출발지에 놓은 뒤 'idle' 을 입력하세요")
        print(f"\n[에스코트 중단] {e} - 로봇 위치를 수동으로 확인하세요 (출발지 아님)")
        publish_alert("escort_lost", str(e))
    except EmergencyStop:
        robot.escort_mode(False)
        robot.stop()
        robot.led("stop")
        publish_status("aborted", "비상정지 - 로봇을 출발지에 놓고 'idle' 입력")
        stop_event.clear()  # 프로그램은 계속 실행 (aborted 상태를 B에 계속 알리고, 'idle' 입력 대기)
        print("  상태: aborted (B파트에 '안내 중'으로 표시됨). 로봇을 출발지에 놓은 뒤 'idle' 을 입력하세요")
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
            st = {"dir": 3, "hold_sec": sec, "desc": f"직진 {seg['go']}cm"}
            if "trim" in seg:  # 구간별 쏠림 보정 (바닥 상태가 다른 구간용)
                st["trim"] = seg["trim"]
                st["desc"] += f" (쏠림보정 {seg['trim']})"
            steps.append(st)
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
        if "turn" in seg:
            back.append({"turn": -seg["turn"]})
            continue
        b = {k: v for k, v in seg.items() if k not in ("trim", "return_trim")}
        if "return_trim" in seg:  # 같은 바닥도 반대 방향으로 걸으면 쏠림이 달라질 수 있어 따로 지정
            b["trim"] = seg["return_trim"]
        back.append(b)
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


TEAM_DEST_NAMES = {
    "reception": "안내데스크", "meeting_room_1": "회의실 1", "safety_training_room": "안전교육장",
    "inbound_dock": "입고 도크", "outbound_dock": "출고 도크", "inspection_area": "검수장",
    "elevator_hall": "엘리베이터", "exit_gate": "나가는 곳",
}


def parse_route_tokens(tokens):
    """'R90 F200@-6 L90 F170@4,2 W2' -> path 리스트
    R/L=오른쪽/왼쪽 회전(도), F/B=직진/후진(cm), W=대기(초)
    F200@-6 = 이 구간 쏠림 보정 -6 / F200@-6,3 = 갈 때 -6, 복귀할 때 3"""
    import re
    path = []
    for tok in tokens:
        m = re.fullmatch(r"([RLFBWrlfbw])(\d+(?:\.\d+)?)(?:@(-?\d+)(?:,(-?\d+))?)?", tok)
        if not m:
            raise ValueError(f"알 수 없는 구간 '{tok}' (예: R90 L90 F200 B50 W2, 보정은 F200@-6)")
        kind, num, trim, rtrim = m.group(1).upper(), float(m.group(2)), m.group(3), m.group(4)
        num = int(num) if num.is_integer() else num
        if kind in "RL":
            seg = {"turn": -num if kind == "R" else num}
        elif kind == "F":
            seg = {"go": num}
        elif kind == "B":
            seg = {"back": num}
        else:
            seg = {"wait": num}
        if trim is not None:
            if kind not in "F":
                raise ValueError(f"쏠림 보정(@)은 직진(F)에만 쓸 수 있어요: '{tok}'")
            seg["trim"] = int(trim)
            if rtrim is not None:
                seg["return_trim"] = int(rtrim)
        path.append(seg)
    return path


def save_route(code, path, name=None):
    """demo_routes.json 의 한 목적지 경로만 바꿔서 저장 (보정값과 다른 목적지는 그대로, 형식 자동)"""
    import os
    import shutil
    data = {}
    if os.path.exists(DISTANCE_FILE):
        with open(DISTANCE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        shutil.copy(DISTANCE_FILE, DISTANCE_FILE + ".bak")
    entry = data.get(code, {})
    entry["name"] = name or entry.get("name") or TEAM_DEST_NAMES.get(code, code)
    entry.setdefault("alias", [])
    entry["path"] = path
    entry.pop("return", None)  # 복귀는 자동 생성
    data[code] = entry
    with open(DISTANCE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def describe_path(path):
    out = []
    for seg in path:
        if "turn" in seg:
            out.append(f"{'좌' if seg['turn'] > 0 else '우'}회전 {abs(seg['turn'])}도")
        elif "go" in seg:
            t = f" (보정 {seg['trim']}" + (f", 복귀 {seg['return_trim']}" if "return_trim" in seg else "") + ")" if "trim" in seg else ""
            out.append(f"직진 {seg['go']}cm{t}")
        elif "back" in seg:
            out.append(f"후진 {seg['back']}cm")
        elif "wait" in seg:
            out.append(f"{seg['wait']}초 대기")
    return " -> ".join(out)


TEAM_DEST_NAMES = {"reception": "안내데스크", "meeting_room_1": "회의실 1", "safety_training_room": "안전교육장",
                   "inbound_dock": "입고 도크", "outbound_dock": "출고 도크", "inspection_area": "검수장",
                   "elevator_hall": "엘리베이터", "exit_gate": "나가는 곳"}

ROUTE_HELP = """사용법: route <목적지> <구간들>
  R90 = 오른쪽 90도 회전   L90 = 왼쪽 90도 회전
  F200 = 200cm 직진        B50 = 50cm 후진        W2 = 2초 대기
  F200:-6 = 200cm 직진, 이 구간만 쏠림보정 -6 (왼쪽으로 쏠리면 더 작게(-), 오른쪽으로 쏠리면 더 크게(+))
  F200:-6:3 = 갈 때 -6, 이 구간을 복귀할 때 3
  예) route 출고 R90 F200:-6 L90 F170:4"""


def parse_route_tokens(tokens):
    """R90 L90 F200 F200:-6 B50 W2 -> demo_routes.json 의 path 형식"""
    import re
    path = []
    for tok in tokens:
        m = re.fullmatch(r"([RLFBWrlfbw])(\d+(?:\.\d+)?)(?::(-?\d+))?(?::(-?\d+))?", tok)
        if not m:
            raise ValueError(f"알 수 없는 구간 '{tok}'")
        kind, val, trim, rtrim = m.group(1).upper(), float(m.group(2)), m.group(3), m.group(4)
        val = int(val) if val.is_integer() else val
        if kind == "R":
            path.append({"turn": -val})
        elif kind == "L":
            path.append({"turn": val})
        elif kind == "F":
            seg = {"go": val}
            if trim is not None:
                seg["trim"] = int(trim)
            if rtrim is not None:
                seg["return_trim"] = int(rtrim)  # 같은 구간을 복귀할 때의 쏠림보정
            path.append(seg)
        elif kind == "B":
            path.append({"back": val})
        elif kind == "W":
            path.append({"wait": val})
        if trim is not None and kind != "F":
            raise ValueError(f"쏠림보정(:숫자)은 직진(F)에만 붙일 수 있음: '{tok}'")
    if not path:
        raise ValueError("구간이 없음")
    return path


def save_route(code, path, name=None):
    """demo_routes.json 에서 해당 목적지 경로만 교체 저장 (보정값·다른 목적지는 그대로, 이전 파일은 .bak)"""
    import os
    import shutil
    data = {}
    if os.path.exists(DISTANCE_FILE):
        with open(DISTANCE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        shutil.copy(DISTANCE_FILE, DISTANCE_FILE + ".bak")
    entry = data.get(code, {})
    entry["name"] = entry.get("name") or name or TEAM_DEST_NAMES.get(code, code)
    entry["path"] = path
    entry.pop("return", None)  # 복귀는 자동 생성
    data[code] = entry
    with open(DISTANCE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_command(prompt):
    """키보드 입력과 트리거를 동시에 기다림. (입력 문자열, 트리거) 중 하나를 반환
    Windows: 글자를 한 자씩 받으면서 0.05초마다 트리거 확인 / 그 외: 일반 input()"""
    if msvcrt is None:
        return input(prompt).strip(), None
    print(prompt, end="", flush=True)
    buf = ""
    while True:
        try:
            trig = trigger_queue.get_nowait()
            if buf:
                print(f"\n(입력 중이던 '{buf}' 취소)")
            else:
                print()
            return "", trig
        except queue.Empty:
            pass
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                print()
                return buf.strip(), None
            if ch == "\x08":  # Backspace
                if buf:
                    buf = buf[:-1]
                    print("\b \b", end="", flush=True)
                continue
            if ch == "\x03":  # Ctrl+C
                raise KeyboardInterrupt
            if ch in ("\x00", "\xe0"):  # 방향키 등 특수키 무시
                msvcrt.getwch()
                continue
            buf += ch
            print(ch, end="", flush=True)
        else:
            time.sleep(0.05)


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
        battery = None
        for _ in range(3):  # 혼잡한 공유기에서는 첫 응답이 늦을 수 있어 3번까지 시도
            battery = robot.request_battery()
            if battery is not None:
                break
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

        if TRIGGER_ENABLED:
            print("[자동 출발] 목적지 확정 신호를 기다리는 중 (키보드로 직접 입력도 가능)")
        while not stop_event.is_set():
            dest_id, trig = read_command("목적지 입력 (번호/이름/별명, 종료: q): ")
            if trig is not None:  # B파트 목적지 확정 -> 자동 출발
                code = None if trig.get("invalid") else resolve_destination(trig["destination"], destinations)
                if code is None:
                    reason = "허용되지 않은 목적지" if trig.get("invalid") else "경로 파일에 없는 목적지"
                    print(f"[트리거] {reason} '{trig['destination']}' - 출발 안 하고 경고 발행")
                    CURRENT_SESSION[0] = trig.get("session_id")
                    publish_alert("invalid_destination", f"{reason}: {trig['destination']}")
                    CURRENT_SESSION[0] = None
                    continue
                print(f"[트리거] {trig['source']}에서 목적지 확정 수신: {destinations[code]['name']} -> 자동 출발")
                CURRENT_SESSION[0] = trig.get("session_id")
                escort_busy[0] = True
                try:
                    goto_destination(robot, code, destinations)
                finally:
                    escort_busy[0] = False
                    CURRENT_SESSION[0] = None
                continue
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
            if dest_id.lower() == "idle":  # 중단(aborted) 후 로봇을 출발지에 되돌려 놓고 대기 상태로 복귀
                robot.stop()
                robot.led("idle")
                publish_status("idle", "수동 복귀 - 안내 요청 대기 중")
                stop_event.clear()
                print("  대기(idle) 상태로 전환. 이제 B파트 안내 요청을 받을 수 있습니다")
                continue
            if dest_id.lower() == "mqtt":  # 팀 브로커 연결 상태
                print(f"  우리 브로커: {'연결됨' if mqtt_pub.client else '연결 안 됨'}")
                print(f"  팀 브로커 : {'연결됨' if mqtt_pub.team_ok else '연결 안 됨'} ({TEAM_MQTT_HOST}:{TEAM_MQTT_PORT}, {TEAM_MQTT_USER})")
                print(f"  현재 상태 : {ESCORT_STATE['state']} ({ESCORT_STATE['detail']}) - {TEAM_STATUS_TOPIC} 로 1초마다 발행 중")
                ld = mqtt_pub.last_decision
                print(f"  마지막 A 판정: {ld[1].get('decision')} ({time.time() - ld[0]:.0f}초 전)" if ld else "  마지막 A 판정: 없음")
                continue
            if dest_id.lower().startswith("route"):  # 경로 변경: route 출고 R90 F200@-6 L90 F170@4
                parts = dest_id.split()
                if len(parts) < 3:
                    print("사용법: route <목적지> <구간들>")
                    print("  예) route 출고 R90 F200 L90 F170")
                    print("  R/L=오른쪽/왼쪽 회전(도), F/B=직진/후진(cm), W=대기(초)")
                    print("  쏠림 보정: F200@-6 (음수=오른쪽으로 보정, 양수=왼쪽으로 보정)")
                    print("  복귀 구간 보정 따로: F200@-6,3 (갈 때 -6, 돌아올 때 3)")
                    continue
                target = resolve_destination(parts[1], destinations) or (parts[1] if parts[1] in ALLOWED_DESTINATIONS else None)
                if target is None:
                    print(f"  알 수 없는 목적지 '{parts[1]}'. 팀 목적지 코드: {', '.join(sorted(ALLOWED_DESTINATIONS))}")
                    continue
                try:
                    new_path = parse_route_tokens(parts[2:])
                except ValueError as ex:
                    print(f"  {ex}")
                    continue
                save_route(target, new_path)
                destinations = load_all_routes()
                print(f"  저장 완료 [{target}] {describe_path(new_path)}")
                print(f"  복귀(자동): {describe_path(auto_return_path(new_path))}")
                continue
            if dest_id.lower().startswith("route ") or dest_id.lower() == "route":  # 경로 수정 (JSON 직접 편집 불필요)
                parts = dest_id.split()
                if len(parts) < 3:
                    print(ROUTE_HELP)
                    continue
                code = resolve_destination(parts[1], destinations) or (parts[1] if parts[1] in TEAM_DEST_NAMES else None)
                if code is None:
                    print(f"  알 수 없는 목적지 '{parts[1]}'. 팀 목적지 코드: {', '.join(TEAM_DEST_NAMES)}")
                    continue
                try:
                    path = parse_route_tokens(parts[2:])
                except ValueError as ex:
                    print(f"  {ex}\n{ROUTE_HELP}")
                    continue
                save_route(code, path)
                destinations = load_all_routes()
                r = destinations[code]
                print(f"  저장 완료 ({DISTANCE_FILE}, 이전 파일은 .bak)")
                print(f"  {r['name']} 가는 길 : " + " -> ".join(st.get("desc", "") for st in r["steps"]))
                print(f"  {r['name']} 돌아오기: " + " -> ".join(st.get("desc", "") for st in r["return_steps"]))
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
            escort_busy[0] = True
            try:
                goto_destination(robot, code, destinations)
            finally:
                escort_busy[0] = False

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
