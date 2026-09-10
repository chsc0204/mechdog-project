"""
BLE 이동 명령 테스트
- CMD|3|3|$ = 직진 시작
- 1.5초 후 CMD|3|0|$ = 정지
- 로봇을 바닥에 놓고, 앞에 충분한 공간을 확보한 후 실행하세요!
"""

import asyncio
from bleak import BleakScanner, BleakClient

WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"


def on_notify(sender, data):
    print(f"[로봇 응답] {data.decode(errors='ignore')}")


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

        print("\n3초 후 직진합니다. 로봇 앞에 공간이 충분한지 확인하세요!")
        await asyncio.sleep(3)

        print(">>> 직진 명령 전송 (CMD|3|3|$)")
        await client.write_gatt_char(WRITE_UUID, b"CMD|3|3|$")

        await asyncio.sleep(1.5)

        print(">>> 정지 명령 전송 (CMD|3|0|$)")
        await client.write_gatt_char(WRITE_UUID, b"CMD|3|0|$")

        await asyncio.sleep(1)
        await client.stop_notify(NOTIFY_UUID)
        print("\n테스트 완료.")


asyncio.run(main())
