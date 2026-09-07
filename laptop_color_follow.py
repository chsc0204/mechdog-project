"""
노트북 기반 색상 물체 추적 (빨간 젓가락 등)
1. WiFi로 로봇 카메라 영상 수신 (MJPEG 스트림 직접 파싱 방식)
2. OpenCV로 빨간색 물체 위치 분석
3. USB 시리얼로 로봇에 실시간 이동 명령 전송

주의:
- Hiwonder Python Editor는 반드시 꺼두세요 (포트 충돌 방지)
- COM_PORT 번호를 본인 환경에 맞게 수정하세요
- 'q' 키로 안전하게 종료 (자동으로 정지 명령 전송)
"""

import cv2
import serial
import time
import numpy as np
import requests
import threading

# ===== 비상 정지 시스템 =====
# 터미널에 'q' + Enter를 입력하면 로봇이 무엇을 하든 즉시 정지합니다.
# (영상 창에 포커스가 없어도, 로봇이 동작 실행 중이라 바쁜 상태여도 작동합니다)
stop_event = threading.Event()

class EmergencyStop(Exception):
    """비상 정지 신호로 인한 중단"""
    pass

def keyboard_listener():
    """백그라운드에서 터미널 입력을 계속 감시하는 스레드"""
    while not stop_event.is_set():
        try:
            user_input = input()
        except (EOFError, RuntimeError):
            break
        if user_input.strip().lower() == 'q':
            print("\n[비상 정지] 'q' 입력 감지! 즉시 정지합니다.\n")
            stop_event.set()
            break

def interruptible_sleep(duration):
    """대기 중에도 비상정지 신호를 계속 확인하는 sleep 함수"""
    end_time = time.time() + duration
    while time.time() < end_time:
        if stop_event.is_set():
            # 즉시 로봇에 정지 명령을 직접(raw) 전송 (재귀 호출 방지)
            try:
                ser.write(b"mechdog.move(0,0)\r\n")
            except Exception:
                pass
            raise EmergencyStop()
        time.sleep(min(0.05, end_time - time.time()))

# ===== 설정값 (필요시 수정) =====
COM_PORT = "COM4"
BAUD_RATE = 115200
STREAM_URL = "http://192.168.5.1:81/stream"

# ===== 시리얼 연결 =====
print(f"{COM_PORT} 연결 시도 중...")
ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)

# DTR/RTS 신호를 해제 (이 신호가 켜져 있으면 칩이 리셋 상태에 갇힐 수 있음)
ser.setDTR(False)
ser.setRTS(False)
time.sleep(2)

# 비상정지 감시 스레드 시작 (이 시점부터 언제든 터미널에 'q'+Enter로 즉시 정지 가능)
listener_thread = threading.Thread(target=keyboard_listener, daemon=True)
listener_thread.start()
print("[안내] 언제든 터미널에 'q' 입력 후 Enter를 누르면 즉시 정지합니다.")

# 로봇이 다른 프로그램 실행 중일 수 있으니, 강제로 중단하고 REPL 대기 상태로 복귀시키기
print("로봇에 중단 신호(Ctrl+C) 전송 중...")
ser.write(b'\r\n')
time.sleep(0.2)
ser.write(b'\x03')  # Ctrl+C
time.sleep(0.3)
ser.write(b'\x03')  # 한 번 더
time.sleep(0.3)
ser.reset_input_buffer()
ser.write(b'\r\n')
time.sleep(0.3)
raw = ser.read(ser.in_waiting or 1)
print(f"중단 신호 응답(원본 바이트): {raw}")

def send_command(cmd, wait=0.05, verbose=False):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    interruptible_sleep(wait)
    response = ser.read(ser.in_waiting or 1).decode(errors="ignore")
    if verbose:
        print(f"  >>> {cmd}")
        print(f"  [로봇 응답] {response.strip() if response.strip() else '(없음)'}")

print("로봇 초기화 중...")
send_command("print('COMM_TEST_OK')", wait=0.5, verbose=True)
send_command("import Hiwonder", wait=0.8, verbose=True)
send_command("import time", wait=0.3, verbose=True)
send_command("import Hiwonder_IIC", wait=0.5, verbose=True)
send_command("from HW_MechDog import MechDog", wait=1.0, verbose=True)
send_command("mechdog = MechDog()", wait=1.0, verbose=True)
send_command("mechdog.set_default_pose()", wait=1.0, verbose=True)
send_command("iic1 = Hiwonder_IIC.IIC(1)", wait=0.5, verbose=True)
send_command("i2csonar = Hiwonder_IIC.I2CSonar(iic1)", wait=0.5, verbose=True)
time.sleep(2)
print("로봇 초기화 완료!")

# ===== 거리센서 값 읽기 함수 =====
def get_distance():
    """로봇의 거리센서 값을 읽어옴 (단위: cm로 추정). 실패 시 None 반환"""
    ser.reset_input_buffer()
    ser.write(b"print(i2csonar.getDistance())\r\n")
    time.sleep(0.15)
    raw = ser.read(ser.in_waiting or 1).decode(errors="ignore")

    # 응답에서 숫자만 추출 (에코된 명령어, 프롬프트 등 제외)
    lines = [l.strip() for l in raw.replace("\r", "").split("\n")]
    for line in lines:
        if line.startswith(">>>") or "getDistance" in line or not line:
            continue
        try:
            return float(line)
        except ValueError:
            continue
    return None

# ===== MJPEG 스트림 직접 연결 =====
print("카메라 스트림 연결 중...")
stream = requests.get(STREAM_URL, stream=True, timeout=5)
byte_buffer = bytes()

print("연결 성공! 빨간 물체(젓가락 등)를 카메라 앞에 놓아주세요. 'q' 키로 종료.")

RED_LOWER1 = np.array([0, 100, 100])
RED_UPPER1 = np.array([10, 255, 255])
RED_LOWER2 = np.array([160, 100, 100])
RED_UPPER2 = np.array([180, 255, 255])

last_command_time = 0
COMMAND_INTERVAL = 0.08
PROCESS_WIDTH = 160

# ===== 장애물 회피 설정 =====
OBSTACLE_THRESHOLD = 15       # 이 거리(cm) 이내면 장애물로 판단, 무조건 정지
DISTANCE_CHECK_INTERVAL = 0.3  # 거리센서는 0.3초마다 한 번씩만 확인 (너무 자주 확인하면 느려짐)
last_distance_check_time = 0
cached_distance = 999  # 초기값은 "장애물 없음"으로 안전하게 시작

# ===== 도착 모션 설정 =====
# 임시 방식: 물체가 화면에 크게(가깝게) 잡힌 상태가 일정 시간 유지되면 "도착"으로 판단
# (실제로는 팀 계획대로 웨이포인트/좌표 기반 도착 판정으로 교체 예정)
ARRIVAL_AREA_THRESHOLD = 1500   # 이 크기 이상이면 "가까이 도달" 상태로 간주
ARRIVAL_HOLD_TIME = 1.5         # 이 상태가 몇 초 유지되면 진짜 도착으로 확정
RESET_AREA_THRESHOLD = 800      # 이 크기 밑으로 떨어지면 다시 "출발 전" 상태로 리셋

arrival_close_since = None   # 가까워지기 시작한 시각
arrived_played = False       # 이미 도착 모션을 실행했는지 여부
mission_complete = False     # 도착 후 완전히 대기 상태로 전환됐는지 여부
object_left_since_complete = False  # 도착 후 물체가 시야에서 한 번이라도 사라졌는지 여부

def play_arrival_sequence():
    """도착 모션(인사 동작) + 안내 멘트 출력"""
    print("\n===== 목적지 도착! =====")
    print("[안내 멘트] 목적지에 도착했습니다. 안내를 종료합니다. 감사합니다!")
    send_command("mechdog.action_run('scrape_a_bow')", wait=3.0, verbose=True)
    print("임무 완료. 물체가 사라졌다가 다시 나타나면 추적을 재개합니다.")
    print("========================\n")

def detect_red(frame):
    """빨간 물체 감지 (마스크, 컨투어, 가장 큰 물체 정보 반환)"""
    scale = PROCESS_WIDTH / frame.shape[1]
    small = cv2.resize(frame, (PROCESS_WIDTH, int(frame.shape[0] * scale)))

    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask1 = cv2.inRange(hsv, RED_LOWER1, RED_UPPER1)
    mask2 = cv2.inRange(hsv, RED_LOWER2, RED_UPPER2)
    mask = cv2.bitwise_or(mask1, mask2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    largest = None
    area = 0
    if contours:
        largest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(largest)
        if area <= 30:  # 노이즈 취급
            largest = None
            area = 0

    return largest, area, mask.shape, scale

def process_frame(frame):
    global last_command_time, last_distance_check_time, cached_distance
    global arrival_close_since, arrived_played, mission_complete, object_left_since_complete

    now = time.time()

    # ---- 0. 임무 완료 상태 - 물체가 사라졌다가 다시 나타나는지만 확인, 이동 없음 ----
    if mission_complete:
        largest, area, _, _ = detect_red(frame)

        if largest is None:
            # 물체가 시야에서 사라짐 - "재개 가능" 상태로 표시
            object_left_since_complete = True
            status = "대기 중 (물체 사라짐 확인)"
        elif object_left_since_complete:
            # 물체가 사라졌다가 다시 나타남 -> 추적 재개!
            mission_complete = False
            arrived_played = False
            arrival_close_since = None
            object_left_since_complete = False
            status = "물체 재발견 - 추적 재개!"
            print("\n>>> 물체 재발견, 추적을 재개합니다 <<<\n")
        else:
            # 물체가 아직 그 자리에 있음 (한 번도 안 사라짐) - 계속 대기
            status = "대기 중 (물체 아직 보임)"

        cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 200, 0), 2)
        return frame

    # ---- 1. 장애물(거리센서) 우선 확인 ----
    if (now - last_distance_check_time) > DISTANCE_CHECK_INTERVAL:
        dist = get_distance()
        if dist is not None:
            cached_distance = dist
        last_distance_check_time = now

    if cached_distance < OBSTACLE_THRESHOLD:
        # 장애물 감지! 색상 추적 무시하고 무조건 정지
        send_command("mechdog.move(0,0)")
        status = f"장애물 감지({cached_distance:.0f}cm) - 정지"
        cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        print(status)
        return frame

    # ---- 2. 장애물 없으면 기존 색상 추적 로직 진행 ----
    largest, area, (h, w), scale = detect_red(frame)
    status = "찾는 중..."

    if largest is not None and (now - last_command_time) > COMMAND_INTERVAL:
        M = cv2.moments(largest)
        cx = int(M["m10"] / M["m00"])
        cy = int(M["m01"] / M["m00"])

        center_x = w // 2
        offset = cx - center_x

        if area > ARRIVAL_AREA_THRESHOLD:
            send_command("mechdog.move(0,0)")

            # 가까운 상태 유지 시간 추적
            if arrival_close_since is None:
                arrival_close_since = now

            if not arrived_played and (now - arrival_close_since) > ARRIVAL_HOLD_TIME:
                play_arrival_sequence()
                arrived_played = True
                mission_complete = True   # 인사 후 대기 상태로 전환
                object_left_since_complete = False
                status = "도착 - 인사 완료, 대기 중"
            else:
                status = "너무 가까움 - 정지"
        elif offset < -20:
            send_command("mechdog.move(40,25)")
            status = "왼쪽으로 회전"
        elif offset > 20:
            send_command("mechdog.move(40,-25)")
            status = "오른쪽으로 회전"
        else:
            send_command("mechdog.move(40,0)")
            status = "직진 (추적 중)"

        last_command_time = now
        cv2.circle(frame, (int(cx/scale), int(cy/scale)), 10, (0, 255, 0), -1)
        print(f"물체 offset={offset}, area={area} -> {status}")

    cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    return frame

try:
    for chunk in stream.iter_content(chunk_size=1024):
        if stop_event.is_set():
            break

        byte_buffer += chunk
        start = byte_buffer.find(b'\xff\xd8')
        end = byte_buffer.find(b'\xff\xd9')

        if start != -1 and end != -1 and end > start:
            jpg = byte_buffer[start:end+2]
            byte_buffer = byte_buffer[end+2:]

            frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue

            try:
                frame = process_frame(frame)
            except EmergencyStop:
                print("비상정지로 인해 종료합니다.")
                break

            cv2.imshow("Color Follow (Laptop-side)", frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                stop_event.set()
                break
finally:
    try:
        ser.write(b"mechdog.move(0,0)\r\n")
    except Exception:
        pass
    print("정지 명령 전송, 종료합니다.")
    cv2.destroyAllWindows()
    ser.close()