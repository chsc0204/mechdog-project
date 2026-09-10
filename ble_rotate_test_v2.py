"""
제자리 회전(CMD|9) 명령 테스트 v2
- 로봇이 "완료" 신호를 보낼 때까지 기다려서 실제 소요시간을 측정
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
        print(f"\n>>> 로봇이 회전 완료를 알려옴! 실제 소요시간: {elapsed:.2f}초 <<<\n")
        done_event.set()


async def main():
    global start_time

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

        print("\n3초 후 왼쪽으로 30도 회전 명령을 보냅니다. 로봇을 계속 지켜봐주세요!")
        await asyncio.sleep(3)

        start_time = time.time()
        print(">>> 회전 명령 전송 (CMD|9|30|$)")
        await client.write_gatt_char(WRITE_UUID, b"CMD|9|30|$")

        # 최대 20초까지 "완료" 신호를 기다림
        try:
            await asyncio.wait_for(done_event.wait(), timeout=20.0)
        except asyncio.TimeoutError:
            print("\n20초가 지나도 완료 신호가 안 옴. 뭔가 다른 문제가 있는 것 같아요.")

        await client.stop_notify(NOTIFY_UUID)
        print("테스트 종료.")


asyncio.run(main())
