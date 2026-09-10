"""
웨이포인트 실측 보정용 스크립트
- 방향(dir)과 지속시간(초)을 입력받아 그만큼만 이동 후 정지
- 이동 전후 거리를 자로 재거나, 회전각도를 눈대중/각도기로 측정해서
  destinations_ble.json 값을 실제에 맞게 보정하는 데 사용

방향 코드 (오늘까지 확인된 것):
  0=정지, 1=강우회전+전진, 2=약우회전+전진, 3=직진,
  4=약좌회전+전진, 5=강좌회전+전진, 7=후진
  (2026-09-10 실측 확인: 음수각도=우회전, 양수각도=좌회전 - 공식문서와 일치)

측정 팁:
- 직진(3): 로봇 코 위치에 테이프로 시작선 표시 -> 실행 후 끝난 위치까지 거리(cm) 측정
- 회전(1,2,4,5): 로봇이 보는 방향을 시작 시점에 표시해두고, 끝난 후 각도 변화를 각도기로 측정
- 반복측정 권장 (최소 3회) - 걸음마다 오차가 있을 수 있음

터미널에 'q' + Enter로 언제든 비상정지
"""

import asyncio
import threading
import time
from bleak import BleakScanner, BleakClient

WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"

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


def on_notify(sender, data):
    text = data.decode(errors="ignore")
    if "DONE" not in text:
        print(f"[로봇 응답] {text}")


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
        print(f"연결됨: {client.is_connected}\n")
        await client.start_notify(NOTIFY_UUID, on_notify)

        listener_thread = threading.Thread(target=keyboard_listener, daemon=True)
        listener_thread.start()
        print("[안내] 터미널에 'q' + Enter로 언제든 즉시 정지.\n")

        while not stop_event.is_set():
            try:
                dir_code = input("방향 코드 입력 (0=정지,1=강좌,2=약좌,3=직진,4=약우,5=강우,7=후진 / 종료: q): ").strip()
            except EOFError:
                break
            if dir_code.lower() == 'q':
                break

            try:
                duration = float(input("지속시간(초) 입력: ").strip())
            except ValueError:
                print("숫자로 입력해주세요.")
                continue

            print(f"\n측정 시작 위치를 표시하세요! 3초 후 이동합니다...")
            await asyncio.sleep(3)

            try:
                await client.write_gatt_char(WRITE_UUID, f"CMD|3|{dir_code}|$".encode())
                await interruptible_sleep(duration)
                await client.write_gatt_char(WRITE_UUID, b"CMD|3|0|$")
            except EmergencyStop:
                print("비상정지로 중단합니다.")
                break

            print("이동 완료! 실제 이동거리/회전각도를 측정해서 기록해두세요.\n")

        try:
            await client.write_gatt_char(WRITE_UUID, b"CMD|3|0|$")
        except Exception:
            pass
        await client.stop_notify(NOTIFY_UUID)
        print("종료합니다.")


asyncio.run(main())
