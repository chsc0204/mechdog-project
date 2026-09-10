"""
BLE 명령 전송 테스트
- 발견한 UUID(0xFFE1 쓰기, 0xFFE2 알림)로 실제 명령 전송
- 안전한 테스트: 배터리 잔량 조회 (로봇이 움직이지 않는 명령)
"""

import asyncio
from bleak import BleakScanner, BleakClient

WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"

received = []

def on_notify(sender, data):
    text = data.decode(errors="ignore")
    print(f"[로봇 응답 수신] {data} -> 텍스트: {text}")
    received.append(text)


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

    print(f"연결 시도: {target.name} ({target.address})")

    async with BleakClient(target.address) as client:
        print(f"연결됨: {client.is_connected}")

        # 알림(응답 수신) 구독
        await client.start_notify(NOTIFY_UUID, on_notify)
        print("알림 구독 완료. 명령 전송합니다...")

        # 안전한 테스트 명령: 배터리 잔량 조회
        command = b"CMD|6|$"
        await client.write_gatt_char(WRITE_UUID, command)
        print(f"전송함: {command}")

        # 응답 기다리기
        await asyncio.sleep(2.0)

        if received:
            print("\n=== 성공! 로봇이 응답했습니다 ===")
        else:
            print("\n=== 응답을 못 받았어요. 명령 형식이 다를 수 있어요 ===")

        await client.stop_notify(NOTIFY_UUID)


asyncio.run(main())
