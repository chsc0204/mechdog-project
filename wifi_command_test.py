"""
WiFi 기반 로봇 통신 테스트 (UDP 방식)
2026-09-21 실측 확인:
  - 로봇 IP=192.168.5.2, 포트=9027
  - listen() 호출 시 EOPNOTSUPP 에러 -> TCP가 아니라 UDP 소켓으로 확인됨

사전 준비:
1. 노트북 WiFi를 "HW_ESP32S3CAM"(비밀번호 없음)에 연결
2. 로봇 쪽에서 미리 wifi.listenSocket.recvfrom(1024) 를 호출해서 대기 중이어야 함
"""

import socket
import time

ROBOT_IP = "192.168.5.3"  # 2026-09-21 재부팅 후 재확인된 IP (매 재부팅마다 바뀔 수 있음, 주의)
ROBOT_PORT = 9027
TIMEOUT = 3.0


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # UDP
    sock.settimeout(TIMEOUT)

    print(f"UDP로 {ROBOT_IP}:{ROBOT_PORT}에 명령 전송 (CMD|6|$)")
    try:
        sock.sendto(b"CMD|6|$", (ROBOT_IP, ROBOT_PORT))
        print("전송 완료. 응답 대기 중...")
        response, addr = sock.recvfrom(1024)
        print(f"응답: {response} (보낸 주소: {addr})")
    except socket.timeout:
        print("응답 시간 초과 - 로봇이 recvfrom()으로 받긴 했는지, 응답을 보내는 방법이 있는지 확인 필요")
    except Exception as e:
        print(f"통신 실패: {e}")
    finally:
        sock.close()


main()


