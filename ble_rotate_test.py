"""
제자리 회전(CMD|9) 명령 테스트
- 로봇을 왼쪽으로 30도 회전시켜봅니다
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

        print("\n3초 후 왼쪽으로 30도 제자리 회전합니다. 주변 공간 확인하세요!")
        await asyncio.sleep(3)

        print(">>> 회전 명령 전송 (CMD|9|30|$)")
        await client.write_gatt_char(WRITE_UUID, b"CMD|9|30|$")

        await asyncio.sleep(2)
        await client.stop_notify(NOTIFY_UUID)
        print("\n테스트 완료. 로봇이 실제로 전진 없이 그 자리에서만 돌았는지 확인해주세요.")


asyncio.run(main())
