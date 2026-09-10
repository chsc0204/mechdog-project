"""
카메라 없는 완전 무선 에스코트 (BLE 웨이포인트 방식)
- 카메라/WiFi 전혀 사용 안 함 (오늘 계속 불안정했던 부분 완전 배제)
- 목적지ID -> 방향코드(dir) 시퀀스로 이동
- 이동 중 거리센서로 장애물 확인 (BLE로 조회)
- 도착 시 인사 동작 실행
- 인사 후 정해진 시간(DWELL_SECONDS) 대기했다가 자동으로 "다음 안내 준비" 상태 복귀

명령 프로토콜 (Hiwonder 공식 앱 코드에서 확인):
  CMD|3|{dir}|$   이동 (0=정지,1=강좌,2=약좌,3=직진,4=약우,5=강우,7=후진)
  CMD|2|1|8|$     도착 인사 동작 (scrape_a_bow)
  CMD|4|1|$       거리센서 조회 -> 응답 "CMD|4|{distance}|$"

주의: 로봇을 안전한 공간에 놓고 테스트하세요.
      터미널에 'q' + Enter로 언제든 비상정지.
"""

import asyncio
import threading
import time
import json
from bleak import BleakScanner, BleakClient

WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"

MQTT_BROKER = "localhost"
MQTT_PORT = 1883

OBSTACLE_THRESHOLD = 15     # cm 이내면 장애물로 판단
DWELL_SECONDS = 8           # 도착 인사 후 대기 시간 (이 시간 지나면 다음 안내 준비 상태로)

# ===== 비상 정지 =====
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

async def interruptible_sleep(duration):
    end_time = time.time() + duration
    while time.time() < end_time:
        if stop_event.is_set():
            raise EmergencyStop()
        await asyncio.sleep(min(0.1, end_time - time.time()))

# ===== BLE 통신 =====
latest_distance = None

def on_notify(sender, data):
    global latest_distance
    text = data.decode(errors="ignore")
    if text.startswith("CMD|4|"):
        try:
            latest_distance = float(text.split("|")[2]) / 10.0  # 실측 후 스케일 보정 필요
        except (IndexError, ValueError):
            pass

async def ble_send(client, cmd: str):
    if stop_event.is_set():
        raise EmergencyStop()
    await client.write_gatt_char(WRITE_UUID, cmd.encode())

async def ble_send_raw(client, cmd: str):
    """비상정지 체크를 우회하는 전송 - 정지 명령처럼 '무조건 보내야 하는' 경우 전용"""
    try:
        await client.write_gatt_char(WRITE_UUID, cmd.encode())
    except Exception as e:
        print(f"[정지 명령 전송 실패] {e}")

async def move(client, dir_code):
    await ble_send(client, f"CMD|3|{dir_code}|$")

async def rotate_in_place(client, deg, count=60):
    """CMD|9 회전 명령 (전진 최소화 방식). deg: 양수=좌회전, 음수=우회전 (실측 기준)
    count*0.05초가 대략적인 소요시간. 로봇 쪽에서 회전 완료 후 자동 정지함."""
    await ble_send(client, f"CMD|9|{deg}|{count}|$")
    await interruptible_sleep(count * 0.05 + 0.3)  # 여유시간 약간 추가

async def avoid_obstacle(client, attempt=1):
    """정면 장애물 회피 기동 (자동 확장형 S자 경로)
    후진 -> 오른쪽으로 전진 -> 직진(옆이동) -> 왼쪽으로 전진(방향 복귀) -> 직진(원래 라인 복귀)
    (정면 거리센서 하나뿐이라 좌우 상황은 모름 -> 항상 오른쪽으로 회피 시도하는 단순 방식)

    시도횟수(attempt)가 늘어날수록 후진·회전·옆이동 시간이 전부 비례해서 커짐
    -> 좁은 장애물은 빠르게, 넓은 장애물(긴 벽 등)은 점점 크게 우회 시도"""
    backup_duration = 2.0 + 1 * (attempt - 1)   # 2.0, 3.0, 4.0, 5.0초
    turn_duration = 0.6 + 0.2 * (attempt - 1)     # 0.6, 0.8, 1.0, 1.2초
    sidestep_duration = 0.5 * attempt             # 0.5, 1.0, 1.5, 2.0초 (옆이동 폭, 가장 크게 늘어남)

    print(f"    회피 기동 ({attempt}차 시도) ① 후진 ({backup_duration:.1f}초)")
    await move(client, 7)  # 직진 후진
    await interruptible_sleep(backup_duration)

    print(f"    회피 기동: ② 오른쪽으로 전진 ({turn_duration:.1f}초)")
    await move(client, 1)  # 강하게 우회전(실측) + 전진
    await interruptible_sleep(turn_duration)

    print(f"    회피 기동: ③ 직진 - 옆으로 비켜가기 ({sidestep_duration:.1f}초)")
    await move(client, 3)  # 직진
    await interruptible_sleep(sidestep_duration)

    print(f"    회피 기동: ④ 왼쪽으로 전진 - 방향 복귀 ({turn_duration:.1f}초)")
    await move(client, 5)  # 강하게 좌회전(실측) + 전진 (②를 상쇄해 원래 방향으로)
    await interruptible_sleep(turn_duration)

    print("    회피 기동: ⑤ 직진 - 원래 라인으로 복귀")
    await move(client, 3)  # 직진 (원래 진행 방향/라인으로 복귀 확인)
    await interruptible_sleep(0.5)

    await stop(client)
    await interruptible_sleep(0.2)


async def execute_step_with_obstacle_check(client, dir_code, hold_sec):
    """이동 명령을 보내고, 그 시간(hold_sec) 내내 계속 장애물을 확인.
    장애물 감지되면 회피 기동(후진+방향틀기) 후 원래 방향으로 이동 재개.
    회피를 여러 번 반복해도 계속 막히면 포기하고 정지 상태로 남김."""
    await move(client, dir_code)

    elapsed = 0.0
    check_interval = 0.25
    MAX_AVOID_ATTEMPTS = 4

    while elapsed < hold_sec:
        if stop_event.is_set():
            raise EmergencyStop()

        dist = await request_distance(client)
        if dist is not None and dist < OBSTACLE_THRESHOLD:
            print(f"    [경고] 이동 중 장애물 감지({dist:.0f}cm) - 회피 기동 시작")
            publish_status("장애물감지", f"이동 중 {dist:.0f}cm 거리에서 회피 기동 시작")

            avoided = False
            for attempt in range(1, MAX_AVOID_ATTEMPTS + 1):
                await avoid_obstacle(client, attempt=attempt)
                dist = await request_distance(client)
                if dist is None or dist >= OBSTACLE_THRESHOLD:
                    print(f"    회피 성공 ({attempt}번째 시도), 원래 방향으로 이동 재개")
                    avoided = True
                    break
                print(f"    아직 막혀있음 ({attempt}/{MAX_AVOID_ATTEMPTS}), 다시 회피 시도")

            if not avoided:
                print("    회피 실패, 정지 상태로 대기 (수동 확인 필요)")
                publish_status("회피실패", "여러 번 회피 시도했으나 계속 막힘")
                await stop(client)
                return  # 이 스텝은 포기하고 정지 상태로 함수 종료

            await move(client, dir_code)  # 원래 이동 방향으로 재개

        await interruptible_sleep(check_interval)
        elapsed += check_interval

    await stop(client)

async def stop(client):
    await ble_send_raw(client, "CMD|3|0|$")

async def request_distance(client):
    await ble_send(client, "CMD|4|1|$")
    await asyncio.sleep(0.2)
    return latest_distance

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

async def goto_destination(client, dest_id, destinations):
    if dest_id not in destinations:
        print(f"알 수 없는 목적지 ID: {dest_id}")
        return

    dest = destinations[dest_id]
    print(f"\n===== 목적지 '{dest['name']}'({dest_id})로 이동 시작 =====")
    publish_status("이동시작", f"{dest['name']} 방향으로 출발")

    try:
        for i, step in enumerate(dest["steps"], 1):
            print(f"  [{i}/{len(dest['steps'])}] {step['desc']} (dir={step['dir']})")
            await execute_step_with_obstacle_check(client, step["dir"], step["hold_sec"])

        await stop(client)
        print(f"목적지 '{dest['name']}' 도착!")

        print("\n===== 도착 인사 =====")
        print("[안내 멘트] 목적지에 도착했습니다. 안내를 종료합니다. 감사합니다!")
        await ble_send(client, "CMD|2|1|8|$")  # scrape_a_bow
        await interruptible_sleep(4)  # 동작 재생 대기
        publish_status("도착완료", f"{dest['name']} 도착, 인사동작 재생")

        print(f"\n{DWELL_SECONDS}초간 대기 후 다음 안내 준비 상태로 전환합니다...")
        await interruptible_sleep(DWELL_SECONDS)
        publish_status("대기준비완료", "다음 방문자 안내 준비 완료")
        print("===== 다음 안내 준비 완료 =====\n")

    except EmergencyStop:
        print("비상정지로 인해 이동을 중단합니다.")
        await stop(client)
        publish_status("비상정지", "사용자에 의해 이동 중단")


async def main():
    with open("destinations_ble.json", "r", encoding="utf-8") as f:
        destinations = json.load(f)

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
        print("[안내] 터미널에 'q' + Enter로 언제든 즉시 정지.\n")

        dest_keys = [k for k in destinations.keys() if not k.startswith("_")]
        print("사용 가능한 목적지:", dest_keys)
        while not stop_event.is_set():
            try:
                dest_id = input("이동할 목적지 ID를 입력하세요 (종료: q): ").strip()
            except EOFError:
                break
            if dest_id.lower() == 'q':
                break
            await goto_destination(client, dest_id, destinations)

        try:
            await stop(client)
        except Exception:
            pass
        await client.stop_notify(NOTIFY_UUID)
        print("종료합니다.")


asyncio.run(main())
