"""
웨이포인트 기반 에스코트 이동
- destinations.json에서 목적지별 이동 시퀀스를 읽어와 순서대로 실행
- 매 스텝 사이에 장애물(거리센서) 확인
- 목적지 도착 시 인사 동작 + MQTT 상태 발행
- 언제든 터미널에 'q'+Enter로 비상정지 가능 (오늘 만든 것과 동일한 방식)

주의: 로봇이 연결된 상태에서만 실제로 동작합니다.
      로봇 없이 로직만 확인하려면 파일 맨 아래 DRY_RUN = True 로 설정하세요.
"""

import time
import json
import threading

# ===== 실행 모드 설정 =====
DRY_RUN = True   # True: 로봇 없이 로직만 확인(가짜 시리얼), False: 실제 로봇 연결

COM_PORT = "COM4"
BAUD_RATE = 115200
OBSTACLE_THRESHOLD = 15
MQTT_BROKER = "localhost"
MQTT_PORT = 1883

# ===== 비상 정지 시스템 (기존과 동일) =====
stop_event = threading.Event()

class EmergencyStop(Exception):
    pass

def keyboard_listener():
    while not stop_event.is_set():
        try:
            user_input = input()
        except (EOFError, RuntimeError):
            break
        if user_input.strip().lower() == 'q':
            print("\n[비상 정지] 'q' 입력 감지! 즉시 정지합니다.\n")
            stop_event.set()
            break

def interruptible_sleep(duration, on_interrupt=None):
    end_time = time.time() + duration
    while time.time() < end_time:
        if stop_event.is_set():
            if on_interrupt:
                on_interrupt()
            raise EmergencyStop()
        time.sleep(min(0.05, max(0, end_time - time.time())))


# ===== 가짜 시리얼 (DRY_RUN용, 로봇 없이 로직만 확인할 때) =====
class DummySerial:
    def __init__(self, *a, **kw):
        print(f"[DRY RUN] 가짜 시리얼 연결 (실제 로봇 연결 아님)")
    def write(self, data):
        print(f"[DRY RUN 전송] {data.decode(errors='ignore').strip()}")
    def read(self, n):
        return b""
    def reset_input_buffer(self):
        pass
    def setDTR(self, v):
        pass
    def setRTS(self, v):
        pass
    def close(self):
        pass
    @property
    def in_waiting(self):
        return 0


# ===== 시리얼 연결 =====
if DRY_RUN:
    ser = DummySerial()
else:
    import serial
    print(f"{COM_PORT} 연결 시도 중...")
    ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
    ser.setDTR(False)
    ser.setRTS(False)
    time.sleep(2)

print(f"{'[DRY RUN 모드]' if DRY_RUN else ''} 초기 연결 완료")
# 참고: 비상정지 감시 스레드는 목적지 입력을 받은 후에 시작합니다
# (동시에 시작하면 터미널 입력을 두 곳에서 기다리다 충돌할 수 있음)


def send_command(cmd, wait=0.05, verbose=False):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())

    def emergency_stop_now():
        try:
            ser.write(b"mechdog.move(0,0)\r\n")
        except Exception:
            pass

    interruptible_sleep(wait, on_interrupt=emergency_stop_now)
    response = ser.read(ser.in_waiting or 1).decode(errors="ignore") if not DRY_RUN else ""
    if verbose:
        print(f"  >>> {cmd}")
        if response.strip():
            print(f"  [로봇 응답] {response.strip()}")


def get_distance():
    """거리센서 값 읽기 (DRY_RUN이면 항상 안전 거리 반환)"""
    if DRY_RUN:
        return 999.0
    ser.reset_input_buffer()
    ser.write(b"print(i2csonar.getDistance())\r\n")
    time.sleep(0.15)
    raw = ser.read(ser.in_waiting or 1).decode(errors="ignore")
    lines = [l.strip() for l in raw.replace("\r", "").split("\n")]
    for line in lines:
        if line.startswith(">>>") or "getDistance" in line or not line:
            continue
        try:
            return float(line)
        except ValueError:
            continue
    return None


def publish_status(event_type, detail):
    """MQTT로 상태 발행 (escort/status 토픽, 기존 DB 브릿지와 동일 형식)"""
    message = {"robot_id": 3, "event_type": event_type, "detail": detail}
    if DRY_RUN:
        print(f"[DRY RUN MQTT] escort/status <- {json.dumps(message, ensure_ascii=False)}")
        return
    try:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.publish("escort/status", json.dumps(message, ensure_ascii=False))
        client.disconnect()
    except Exception as e:
        print(f"[MQTT 발행 실패] {e}")


def initialize_robot():
    """로봇 초기화 (실제 로봇 연결 시에만 의미 있음)"""
    print("로봇 초기화 중...")
    send_command("import Hiwonder", wait=0.8, verbose=True)
    send_command("import time", wait=0.3, verbose=True)
    send_command("import Hiwonder_IIC", wait=0.5, verbose=True)
    send_command("from HW_MechDog import MechDog", wait=1.0, verbose=True)
    send_command("mechdog = MechDog()", wait=1.0, verbose=True)
    send_command("mechdog.set_default_pose()", wait=1.0, verbose=True)
    send_command("iic1 = Hiwonder_IIC.IIC(1)", wait=0.5, verbose=True)
    send_command("i2csonar = Hiwonder_IIC.I2CSonar(iic1)", wait=0.5, verbose=True)
    print("로봇 초기화 완료!\n")


def goto_destination(dest_id, destinations):
    """목적지 ID에 해당하는 웨이포인트 시퀀스를 순서대로 실행"""
    if dest_id not in destinations:
        print(f"알 수 없는 목적지 ID: {dest_id}")
        return

    dest = destinations[dest_id]
    print(f"\n===== 목적지 '{dest['name']}'({dest_id})로 이동 시작 =====")
    publish_status("이동시작", f"{dest['name']} 방향으로 출발")

    try:
        for i, step in enumerate(dest["steps"], 1):
            # 스텝 실행 전 장애물 확인
            dist = get_distance()
            if dist is not None and dist < OBSTACLE_THRESHOLD:
                print(f"  [경고] 장애물 감지({dist:.0f}cm), 이동 대기 중...")
                send_command("mechdog.move(0,0)")
                publish_status("장애물감지", f"{dist:.0f}cm 거리에서 대기")
                # 장애물 사라질 때까지 대기 (간단 버전: 최대 5초 재시도)
                for _ in range(50):
                    interruptible_sleep(0.1)
                    dist = get_distance()
                    if dist is None or dist >= OBSTACLE_THRESHOLD:
                        break

            print(f"  [{i}/{len(dest['steps'])}] {step['desc']} "
                  f"(move={step['move_mm']}, angle={step['angle_deg']})")
            send_command(f"mechdog.move({step['move_mm']},{step['angle_deg']})")
            interruptible_sleep(step["hold_sec"])

        # 도착 처리
        send_command("mechdog.move(0,0)")
        print(f"목적지 '{dest['name']}' 도착!")
        send_command("mechdog.action_run('scrape_a_bow')", wait=3.0, verbose=True)
        publish_status("도착완료", f"{dest['name']} 도착, 인사동작 재생")
        print("===== 이동 완료 =====\n")

    except EmergencyStop:
        print("비상정지로 인해 이동을 중단합니다.")
        publish_status("비상정지", "사용자에 의해 이동 중단")


if __name__ == "__main__":
    with open("destinations.json", "r", encoding="utf-8") as f:
        destinations = json.load(f)

    if not DRY_RUN:
        initialize_robot()

    print("사용 가능한 목적지:", list(destinations.keys()))
    dest_id = input("이동할 목적지 ID를 입력하세요: ").strip()

    # 목적지 입력이 끝난 지금부터 비상정지 감시 시작 (이제 이 스레드만 stdin을 사용함)
    listener_thread = threading.Thread(target=keyboard_listener, daemon=True)
    listener_thread.start()
    print("[안내] 언제든 터미널에 'q' 입력 후 Enter를 누르면 즉시 정지합니다.")

    goto_destination(dest_id, destinations)

    if not DRY_RUN:
        ser.close()