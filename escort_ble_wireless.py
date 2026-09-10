"""
완전 무선 에스코트 (BLE 버전, USB 불필요)
- USB 시리얼 대신 블루투스(BLE)로 로봇에 이동/동작 명령 전송
- ArUco 마커(ID 3, 방문자 태그) 추적 + 거리센서 장애물 회피 + 도착 인사 + 재추적 순환
- 카메라는 여전히 WiFi(HW_ESP32S3CAM)로 별도 연결 필요

명령 프로토콜 (Hiwonder 공식 앱 코드에서 확인):
  CMD|3|{dir}|$   이동 (dir: 0=정지,1=강우회전,2=약우회전,3=직진,4=약좌회전,5=강좌회전,7=후진) *실측 기준(공식문서와 일치)
  CMD|2|1|8|$     도착 인사 동작 (scrape_a_bow)
  CMD|4|1|$       거리센서 조회 -> 응답 "CMD|4|{distance}|$"
  CMD|6|$         배터리 조회 -> 응답 "CMD|6|{mV}|$"

주의: 로봇을 안전한 공간에 놓고 테스트하세요.
      터미널에 'q' + Enter로 언제든 비상정지.
"""

import asyncio
import threading
import time
import json
import cv2
import numpy as np
import requests
from bleak import BleakScanner, BleakClient

# ===== 설정값 =====
STREAM_URL = "http://192.168.5.1:81/stream"
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
TARGET_MARKER_ID = 3

WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"

# 이동 방향 코드 (오프셋 판단 결과 -> BLE dir 코드)
DIR_STOP = 0
DIR_LEFT = 4       # 실측 결과 dir=4가 실제로 좌회전 (양수 각도 = 좌회전, 공식 문서와 일치)
DIR_STRAIGHT = 3   # 직진
DIR_RIGHT = 2      # 실측 결과 dir=2가 실제로 우회전 (음수 각도 = 우회전, 공식 문서와 일치)

# ===== 비상 정지 시스템 =====
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
            print("\n[비상 정지] 'q' 입력 감지!\n")
            stop_event.set()
            break

# ===== BLE 통신 래퍼 =====
latest_distance = None  # 거리센서 응답 캐시

def on_notify(sender, data):
    global latest_distance
    text = data.decode(errors="ignore")
    if text.startswith("CMD|4|"):
        try:
            latest_distance = float(text.split("|")[2]) / 10.0  # mm -> cm 추정, 실측 후 보정 필요
        except (IndexError, ValueError):
            pass

async def ble_send(client, cmd: str):
    if stop_event.is_set():
        raise EmergencyStop()
    await client.write_gatt_char(WRITE_UUID, cmd.encode())

async def move(client, dir_code):
    await ble_send(client, f"CMD|3|{dir_code}|$")

async def stop(client):
    await ble_send(client, "CMD|3|0|$")

async def request_distance(client):
    await ble_send(client, "CMD|4|1|$")
    await asyncio.sleep(0.15)  # 응답 대기 (notify 콜백에서 latest_distance 갱신됨)
    return latest_distance

async def play_arrival_sequence(client):
    print("\n===== 목적지 도착! =====")
    print("[안내 멘트] 목적지에 도착했습니다. 안내를 종료합니다. 감사합니다!")
    await ble_send(client, "CMD|2|1|8|$")  # scrape_a_bow
    await asyncio.sleep(4)  # 동작 재생 시간 대기
    publish_status("도착완료", "BLE 무선 - 마커 기반 도착, 인사동작 재생")
    print("임무 완료. 마커가 사라졌다가 다시 나타나면 추적을 재개합니다.")
    print("========================\n")

def publish_status(event_type, detail):
    message = {"robot_id": 3, "event_type": event_type, "detail": detail}
    try:
        import paho.mqtt.client as mqtt
        mc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        mc.connect(MQTT_BROKER, MQTT_PORT, 60)
        mc.publish("escort/status", json.dumps(message, ensure_ascii=False))
        mc.disconnect()
    except Exception as e:
        print(f"[MQTT 발행 실패] {e}")

# ===== ArUco 마커 인식 =====
ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
ARUCO_PARAMS = cv2.aruco.DetectorParameters()
ARUCO_PARAMS.adaptiveThreshWinSizeMin = 3
ARUCO_PARAMS.adaptiveThreshWinSizeMax = 53
ARUCO_PARAMS.adaptiveThreshWinSizeStep = 4
ARUCO_PARAMS.minMarkerPerimeterRate = 0.01
detector = cv2.aruco.ArucoDetector(ARUCO_DICT, ARUCO_PARAMS)

PROCESS_WIDTH = 160

def find_target_marker(frame, target_id):
    scale = PROCESS_WIDTH / frame.shape[1]
    small = cv2.resize(frame, (PROCESS_WIDTH, int(frame.shape[0] * scale)))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is not None:
        for i, mid in enumerate(ids.flatten()):
            if mid == target_id:
                c = corners[i][0]
                area = cv2.contourArea(c.astype(np.float32))
                return c, area, small.shape, scale
    return None, 0, small.shape, scale

# ===== 메인 루프 =====
OBSTACLE_THRESHOLD = 15
DISTANCE_CHECK_INTERVAL = 0.6   # 0.3 -> 0.6초로 늘림 (BLE 통신 빈도 감소, WiFi에 여유 주기)
ARRIVAL_AREA_THRESHOLD = 1200
ARRIVAL_HOLD_TIME = 1.5
RESET_AREA_THRESHOLD = 600
COMMAND_INTERVAL = 0.3  # 0.15 -> 0.3초로 늘림 (같은 이유)

async def main():
    print("로봇 검색 중...")
    devices = await BleakScanner.discover(timeout=8.0)
    target = None
    for d in devices:
        if "mechdog" in (d.name or "").lower():
            target = d
            break
    if target is None:
        print("로봇을 못 찾았어요.")
        return

    print(f"연결 시도: {target.name}")
    async with BleakClient(target.address) as client:
        print(f"연결됨: {client.is_connected}")
        await client.start_notify(NOTIFY_UUID, on_notify)

        listener_thread = threading.Thread(target=keyboard_listener, daemon=True)
        listener_thread.start()
        print("[안내] 터미널에 'q' + Enter로 언제든 즉시 정지. 영상 창에서도 q/Q 가능.")

        print("카메라 스트림 연결 중...")
        stream = None
        for attempt in range(1, 6):
            try:
                stream = requests.get(STREAM_URL, stream=True, timeout=5)
                break
            except requests.exceptions.RequestException as e:
                print(f"  시도 {attempt}/5 실패: {type(e).__name__}, 2초 후 재시도...")
                await asyncio.sleep(2)
        if stream is None:
            print("카메라 스트림 연결에 계속 실패했습니다. WiFi 연결(HW_ESP32S3CAM)을 확인해주세요.")
            return
        byte_buffer = bytes()

        print(f"연결 성공! ID {TARGET_MARKER_ID}번 마커를 카메라 앞에 놓아주세요.")

        last_command_time = 0
        last_distance_check_time = 0
        cached_distance = 999
        arrival_close_since = None
        arrived_played = False
        mission_complete = False
        marker_left_since_complete = False

        try:
            for chunk in stream.iter_content(chunk_size=1024):
                if stop_event.is_set():
                    break

                byte_buffer += chunk
                start = byte_buffer.find(b'\xff\xd8')
                end = byte_buffer.find(b'\xff\xd9')
                if start == -1 or end == -1 or end <= start:
                    continue

                jpg = byte_buffer[start:end + 2]
                byte_buffer = byte_buffer[end + 2:]
                frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    continue

                now = time.time()
                status = "찾는 중..."

                # ---- 임무 완료 대기 상태 ----
                if mission_complete:
                    _, area, _, _ = find_target_marker(frame, TARGET_MARKER_ID)
                    if area == 0:
                        marker_left_since_complete = True
                        status = "대기 중 (마커 사라짐 확인)"
                    elif marker_left_since_complete:
                        mission_complete = False
                        arrived_played = False
                        arrival_close_since = None
                        marker_left_since_complete = False
                        status = "마커 재발견 - 추적 재개!"
                        print("\n>>> 마커 재발견, 추적 재개 <<<\n")
                    else:
                        status = "대기 중 (마커 아직 보임)"

                # ---- 장애물 확인 ----
                elif (now - last_distance_check_time) > DISTANCE_CHECK_INTERVAL:
                    dist = await request_distance(client)
                    if dist is not None:
                        cached_distance = dist
                    last_distance_check_time = now

                if not mission_complete:
                    if cached_distance < OBSTACLE_THRESHOLD:
                        await stop(client)
                        status = f"장애물 감지({cached_distance:.0f}cm) - 정지"
                        print(status)
                    else:
                        corners, area, shape, scale = find_target_marker(frame, TARGET_MARKER_ID)
                        h, w = shape[0], shape[1]
                        if corners is not None and (now - last_command_time) > COMMAND_INTERVAL:
                            cx = int(corners[:, 0].mean())
                            center_x = w // 2
                            offset = cx - center_x

                            if area > ARRIVAL_AREA_THRESHOLD:
                                await stop(client)
                                if arrival_close_since is None:
                                    arrival_close_since = now
                                if not arrived_played and (now - arrival_close_since) > ARRIVAL_HOLD_TIME:
                                    await play_arrival_sequence(client)
                                    arrived_played = True
                                    mission_complete = True
                                    marker_left_since_complete = False
                                    status = "도착 - 인사 완료, 대기 중"
                                else:
                                    status = "너무 가까움 - 정지"
                            elif offset < -20:
                                await move(client, DIR_LEFT)
                                status = "왼쪽으로 회전"
                            elif offset > 20:
                                await move(client, DIR_RIGHT)
                                status = "오른쪽으로 회전"
                            else:
                                await move(client, DIR_STRAIGHT)
                                status = "직진 (추적 중)"

                            last_command_time = now
                            print(f"마커 offset={offset}, area={area:.0f} -> {status}")

                cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                cv2.imshow("BLE Wireless Escort", frame)

                key = cv2.waitKey(1) & 0xFF
                if key == ord('q') or key == ord('Q'):
                    stop_event.set()
                    break
        except EmergencyStop:
            print("비상정지로 인해 종료합니다.")
        finally:
            try:
                await stop(client)
            except Exception:
                pass
            cv2.destroyAllWindows()
            await client.stop_notify(NOTIFY_UUID)
            print("정지 명령 전송, 종료합니다.")


asyncio.run(main())
