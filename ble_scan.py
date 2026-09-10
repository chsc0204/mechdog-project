"""
BLE 진단 스크립트
- 주변의 "MechDog_XX" 이름을 가진 블루투스 기기를 찾아서
- 그 기기가 어떤 서비스(Service)/특성(Characteristic) UUID를 갖고 있는지 전부 출력
- 이 결과로 나중에 실제 이동 명령을 보낼 수 있는지 판단 가능

사전 준비:
  pip install bleak

주의: 실행 전에 Hiwonder Python Editor는 꺼두세요 (USB와는 별개지만, 안전하게)
"""

import asyncio
from bleak import BleakScanner, BleakClient

# Nordic UART Service 표준 UUID (많은 임베디드 기기가 이 방식을 씀)
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # 쓰기(명령 전송)용
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # 알림(응답 수신)용


async def main():
    print("주변 블루투스 기기 스캔 중... (10초)")
    devices = await BleakScanner.discover(timeout=10.0)

    target = None
    for d in devices:
        name = d.name or ""
        print(f"  발견: {name}  ({d.address})")
        if "mechdog" in name.lower():
            target = d

    if target is None:
        print("\n'MechDog_XX' 이름의 기기를 못 찾았어요.")
        print("로봇 전원이 켜져 있는지, 이미 다른 기기(폰 앱 등)와 연결되어 있진 않은지 확인해주세요.")
        return

    print(f"\n로봇 발견: {target.name} ({target.address})")
    print("연결 시도 중...")

    async with BleakClient(target.address) as client:
        print(f"연결됨: {client.is_connected}\n")
        print("=== 서비스 및 특성 목록 ===")
        for service in client.services:
            print(f"\n[Service] {service.uuid}")
            for char in service.characteristics:
                props = ",".join(char.properties)
                print(f"   [Characteristic] {char.uuid}  (속성: {props})")

        # NUS 표준 UUID가 있는지 확인
        found_nus = False
        for service in client.services:
            if service.uuid.lower() == NUS_SERVICE_UUID.lower():
                found_nus = True

        print("\n=== 결과 ===")
        if found_nus:
            print("Nordic UART Service(NUS) 표준 UUID 발견! 예상대로 UART 방식입니다.")
            print("바로 이 UUID로 명령 전송 코드를 만들 수 있어요.")
        else:
            print("표준 NUS는 안 보이지만, 위 목록에서 '쓰기(write)' 속성을 가진")
            print("특성(Characteristic)을 찾으면 그게 명령 전송용일 가능성이 높아요.")
            print("그 UUID를 알려주시면 다음 단계 코드를 만들어드릴게요.")


asyncio.run(main())