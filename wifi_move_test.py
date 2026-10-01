"""
WiFi 이동 명령 테스트 (카메라 동시 작동 확인용)

사용법:
1. 노트북을 로봇과 같은 WiFi(폰 핫스팟)에 연결
2. 브라우저에서 http://172.20.10.7 접속 -> Start Stream
3. 이 스크립트 실행 -> 로봇이 MOVE_SECONDS 동안 직진 후 정지, REPEAT 회 반복
4. 그 동안 카메라 영상이 끊기거나 카메라가 리셋되지 않는지 확인

[중요] keepalive
  펌웨어(main_wifi.py)의 통신두절 자동정지 때문에, 이동 중에는 1초 안에
  명령이 계속 와야 합니다. 그래서 이동 중 KEEPALIVE_INTERVAL마다 같은 이동
  명령을 다시 보냅니다. (에스코트 WiFi 버전에도 같은 방식이 필요)

  WATCHDOG_TEST = True 로 두면 keepalive 없이 한 번만 보내서
  로봇이 약 1초 후 스스로 멈추는지(안전장치 동작) 확인할 수 있습니다.
"""

import socket
import time

ROBOT_IP = "172.20.10.6"  # 폰 핫스팟 연결 시 메인보드 IP (2026-09-23 확인)
ROBOT_PORT = 9027

MOVE_SECONDS = 5.0         # 이동 지속 시간
REPEAT = 3                 # 반복 횟수
REST_SECONDS = 3.0         # 반복 사이 휴식
KEEPALIVE_INTERVAL = 0.3   # 이동 명령 재전송 주기 (펌웨어 타임아웃 1초보다 충분히 짧게)
STOP_RESEND = 3            # UDP 유실 대비 정지 명령 반복 전송
WATCHDOG_TEST = False      # True: keepalive 끄고 통신두절 자동정지 확인


def send_cmd(sock, cmd: str, verbose=True):
    sock.sendto(cmd.encode(), (ROBOT_IP, ROBOT_PORT))
    if verbose:
        print(f"  전송: {cmd}")


def stop(sock):
    for _ in range(STOP_RESEND):
        send_cmd(sock, "CMD|3|0|$")
        time.sleep(0.05)


def move_for(sock, dir_code, seconds):
    cmd = f"CMD|3|{dir_code}|$"
    send_cmd(sock, cmd)
    end = time.time() + seconds
    sent = 1
    while time.time() < end:
        time.sleep(KEEPALIVE_INTERVAL)
        if not WATCHDOG_TEST:
            send_cmd(sock, cmd, verbose=False)
            sent += 1
    print(f"  (이동 명령 총 {sent}회 전송)")


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    mode = "통신두절 안전장치 테스트 (약 1초 후 자동정지가 정상)" if WATCHDOG_TEST else "keepalive 이동"
    print("카메라 영상이 브라우저에서 잘 나오고 있는지 먼저 확인해주세요!")
    print(f"모드: {mode}")
    print(f"설정: {MOVE_SECONDS}초 직진 x {REPEAT}회 (휴식 {REST_SECONDS}초)")
    print("3초 후 시작합니다...")
    time.sleep(3)

    try:
        for i in range(1, REPEAT + 1):
            print(f"\n[{i}/{REPEAT}] 직진 시작")
            t0 = time.time()
            move_for(sock, 3, MOVE_SECONDS)
            stop(sock)
            print(f"[{i}/{REPEAT}] 정지 ({time.time() - t0:.1f}초 경과)")
            if i < REPEAT:
                time.sleep(REST_SECONDS)
        print("\n테스트 완료. 카메라 영상이 끊기거나 리셋되지 않았는지 확인해주세요!")
    except KeyboardInterrupt:
        print("\nCtrl+C 감지 -> 정지 명령 전송")
    finally:
        stop(sock)
        sock.close()


if __name__ == "__main__":
    main()
