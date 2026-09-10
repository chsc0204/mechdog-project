"""
제자리 회전(CMD|9) 명령 테스트 v3
- 각도(deg)와 지속시간(count)을 실행할 때마다 직접 입력해서 실험 가능
- 로봇이 "완료" 신호를 보낼 때까지 기다려서 실제 소요시간을 측정

count 참고: 0.05초 * count = 지속시간
  예) count=40  -> 약 2초
      count=100 -> 약 5초
      count=200 -> 약 10초
"""

import asyncio
import time
from bleak import BleakScanner, BleakClient

WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"

done_event = asyncio.Event()
start_time = None


def on_notify(sender, data):
    text = data.decode(errors="ignore")
    print(f"[로봇 응답] {text}")
    if "DONE" in text:
        elapsed = time.time() - start_time
        print(f"\n>>> 회전 완료! 실제 소요시간: {elapsed:.2f}초 <<<\n")
        done_event.set()


async def main():
    global start_time

    deg = input("회전 각도(세기)를 입력하세요 (예: 40): ").strip()
    count = input("지속시간 count를 입력하세요 (예: 100, 0.05초*count=지속시간): ").strip()

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

        print(f"\n3초 후 회전 명령을 보냅니다 (각도={deg}, count={count}). 로봇을 계속 지켜봐주세요!")
        await asyncio.sleep(3)

        cmd = f"CMD|9|{deg}|{count}|$".encode()
        start_time = time.time()
        print(f">>> 전송: {cmd}")
        await client.write_gatt_char(WRITE_UUID, cmd)

        try:
            await asyncio.wait_for(done_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            print("\n30초가 지나도 완료 신호가 안 옴.")

        await client.stop_notify(NOTIFY_UUID)
        print("테스트 종료.")


asyncio.run(main())
