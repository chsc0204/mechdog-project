# Hiwonder MechDog
# MicroPython - WiFi 전용 버전 (블루투스 코드 완전 제거)
# ===== 2026-09-21 실측 기반으로 재작성 =====
#
# 실측 확인된 사실:
#   - wifi.port = 9027 (고정값으로 보임)
#   - UDP 방식 (listen() 호출 시 EOPNOTSUPP 에러 -> TCP 아님)
#   - wifi.connect_wifi() 호출 후, wifi.wait_connect()를 반복 호출해야
#     "Socket setup success"까지 완료됨 (한 번 호출로는 안 됨)
#   - wifi.listenSocket은 표준 소켓: recvfrom(), sendto() 등 사용 가능
#   - wifi.send_data() 같은 전용 함수는 없음 - listenSocket.sendto() 직접 사용
#   - recvfrom()은 즉시 반환형 - 데이터 없으면 OSError(EAGAIN) 발생 -> 반복 확인 필요
#
# ⚠️ 아직 미검증: read_uart_cmd/parse_uart_cmd가 UDP raw bytes를 그대로 받는지
#    (안 되면 직접 문자열 파싱 코드로 대체 필요 - 아래 주석 참고)

import Hiwonder
import time
import Hiwonder_IIC
from Hiwonder_WIFI import WIFI_CL
from HW_MechDog import MechDog
import machine

# WiFi 설정 - 실제 값을 넣어둬도 됨.
# GitHub에는 깃 클린 필터(wifi_clean_filter.py, .gitattributes)가 커밋할 때 자동으로 자리표시자로 바꿔서 올림
WIFI_NAME = "YOUR_WIFI_NAME"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

_COMMAND = 0
_SEND_DATA = ""
_DATA = 0
_distance = 0
_SONER_DISTANCE = 0
_RUN_STEP = 0
_ACTION_TYPE = 0
_ACTION_NUM = 0
_RUN_DIR = 0
_REC_PARSE_VALUE = []
_ROTATE_DEG = 0
_ROTATE_COUNT = 40
_STRAIGHT_TRIM = 0   # [추가] 직진 쏠림 보정 (음수=오른쪽으로 살짝). CMD|13|trim|speed|$ 으로 노트북에서 설정
_STRAIGHT_SPEED = 120  # [추가] 직진 속도 (기본 120). 노트북에서 조절
_LAST_ADDR = None      # [추가] 마지막으로 명령을 보낸 노트북 주소 (회전 완료 신호 회신용)
_TOUCH_FLAG = 0
_ESCORT_ACTIVE = 0   # [추가] 1이면 에스코트 중 -> 터치 = 일시정지/재개 (0이면 기존처럼 '다음 안내' 터치)
_PAUSED = 0          # [추가] 터치 일시정지 상태
_PAUSE_NOTIFIED = -1 # [추가] 노트북에 마지막으로 알린 일시정지 상태
_SPIN_REMAIN = 0     # [추가] 회전 중 일시정지되면 남은 회전량 (재개 시 마저 회전)
_LAST_TOUCH_MS = 0   # [추가] 터치 중복 입력 방지
_LAST_CMD_TIME = time.ticks_ms()  # [수정] time.time()은 MicroPython에서 1초 단위 정수라 부정확 -> ms 단위로 변경
COMM_TIMEOUT_MS = 1000             # [수정] 이동 중 1초간 명령(keepalive) 없으면 자동정지
TIER1_STOP_DISTANCE = 10

mechdog = MechDog()
i2c1 = Hiwonder_IIC.IIC(1)
i2csonar = Hiwonder_IIC.I2CSonar(i2c1)

try:
    mp3 = Hiwonder_IIC.MP3(i2c1)
    mp3.volume(25)
except Exception:
    mp3 = None
    print("MP3 모듈 초기화 실패 - MP3 기능 없이 계속 진행합니다.")

button3 = Hiwonder.Button(3)

def _on_touch3():
    # [수정] 에스코트 중이면 터치 = 일시정지/재개, 아니면 기존처럼 '다음 안내' 플래그
    global _TOUCH_FLAG, _PAUSED, _RUN_DIR, _LAST_TOUCH_MS
    now = time.ticks_ms()
    if time.ticks_diff(now, _LAST_TOUCH_MS) < 400:  # 한 번 누름이 여러 번 잡히는 것 방지
        return
    _LAST_TOUCH_MS = now
    if _ESCORT_ACTIVE:
        _PAUSED = 0 if _PAUSED else 1
        if _PAUSED:
            _RUN_DIR = 0  # 즉시 정지 (이동 루프가 정지 분기로 빠짐)
    else:
        _TOUCH_FLAG = 1

button3.Clicked(_on_touch3)

# ===== WiFi 연결 확립 (실측된 순서 그대로) =====
print("WiFi 연결 시도 중...")
wifi = WIFI_CL(WIFI_NAME, WIFI_PASSWORD)
wifi.connect_wifi()
_connected = False
for _ in range(40):
    if wifi.wait_connect():
        _connected = True
        break
    time.sleep(0.5)

if _connected:
    print("WiFi 연결 및 소켓 준비 완료:", wifi.wlan.ifconfig())
else:
    print("WiFi 연결 실패 - 계속 진행하지만 명령을 못 받을 수 있음")

time.sleep(1)


def parse_cmd_manual(text):
    """CMD|N|a|b|$ 형식을 직접 파싱 (read_uart_cmd/parse_uart_cmd 미검증 대비 백업)"""
    text = text.strip()
    if not text.startswith("CMD|") or not text.endswith("|$"):
        return None
    body = text[4:-2]  # "CMD|"와 "|$" 제거
    return body.split("|")


def start_main():
    global _COMMAND, _SEND_DATA, _DATA, _SONER_DISTANCE, _RUN_STEP
    global _STRAIGHT_TRIM, _STRAIGHT_SPEED, _LAST_ADDR, _ESCORT_ACTIVE, _PAUSED, _PAUSE_NOTIFIED
    global _ACTION_TYPE, _ACTION_NUM, _RUN_DIR, _REC_PARSE_VALUE
    global _ROTATE_DEG, _ROTATE_COUNT, _TOUCH_FLAG, _LAST_CMD_TIME
    global mechdog, wifi

    dir_flag = 1
    while True:
        try:
            raw, addr = wifi.listenSocket.recvfrom(256)
            _LAST_ADDR = addr
        except OSError:
            time.sleep(0.03)
            continue

        if not raw:
            continue

        text = raw.decode()
        if "CMD" not in text:
            continue

        _LAST_CMD_TIME = time.ticks_ms()  # [수정]
        print("[수신]", text)  # [진단용] 실제로 명령이 도착하는지 눈으로 확인

        # ⚠️ 우선 자체 파싱 함수 사용 (wifi.parse_uart_cmd 미검증이라 안전하게)
        _REC_PARSE_VALUE = parse_cmd_manual(text)
        if not _REC_PARSE_VALUE:
            continue

        _COMMAND = int(_REC_PARSE_VALUE[0])

        if (_COMMAND == 6):
            _SEND_DATA = "CMD|6|{}|$".format(Hiwonder.Battery_power())
            wifi.listenSocket.sendto(_SEND_DATA.encode(), addr)
            continue

        if (_COMMAND == 4):
            _DATA = int(_REC_PARSE_VALUE[1])
            if (_DATA == 1):
                _distance = round((_SONER_DISTANCE * 10))
                if (_distance > 5000):
                    _distance = 5000
                _SEND_DATA = "CMD|4|{}|$".format(_distance)
                wifi.listenSocket.sendto(_SEND_DATA.encode(), addr)
            elif (_DATA == 3 and len(_REC_PARSE_VALUE) >= 5):
                # [추가] CMD|4|3|R|G|B|$ : 초음파 센서 LED 색 변경 (BLE 버전에 있던 기능 복구)
                try:
                    i2csonar.setRGB(0, int(_REC_PARSE_VALUE[2]), int(_REC_PARSE_VALUE[3]), int(_REC_PARSE_VALUE[4]))
                except Exception:
                    pass
            continue

        if (_COMMAND == 15):
            # [추가] CMD|15|1|$ 에스코트 모드 시작 / CMD|15|0|$ 종료 (종료 시 일시정지 해제)
            _ESCORT_ACTIVE = 1 if int(_REC_PARSE_VALUE[1]) else 0
            if not _ESCORT_ACTIVE:
                _PAUSED = 0
                _PAUSE_NOTIFIED = 0
            continue

        if (_COMMAND == 14):
            # [추가] CMD|14|$ 일시정지 상태 조회 -> CMD|14|0또는1|$
            wifi.listenSocket.sendto("CMD|14|{}|$".format(_PAUSED).encode(), addr)
            continue

        if _PAUSED and _COMMAND in (2, 9):
            continue  # [추가] 일시정지 중에는 동작/회전 명령 무시
        if _PAUSED and _COMMAND == 3 and int(_REC_PARSE_VALUE[1]) != 0:
            continue  # [추가] 일시정지 중에는 이동 명령 무시 (정지 명령만 허용)

        if (_COMMAND == 2):
            _RUN_STEP = 2
            _ACTION_TYPE = int(_REC_PARSE_VALUE[1])
            _ACTION_NUM = int(_REC_PARSE_VALUE[2])

        if (_COMMAND == 3):
            _RUN_STEP = 3
            _RUN_DIR = int(_REC_PARSE_VALUE[1])
            if _RUN_DIR < 6:
                if dir_flag != 1:
                    dir_flag = 1
                    mechdog.transform([10, 0, 0], [0, 0, 0], 100)
            else:
                if dir_flag != -1:
                    dir_flag = -1
                    mechdog.transform([-10, 0, 0], [0, 0, 0], 100)

        if (_COMMAND == 9):
            _RUN_STEP = 9
            _ROTATE_DEG = int(_REC_PARSE_VALUE[1])
            _ROTATE_COUNT = int(_REC_PARSE_VALUE[2]) if len(_REC_PARSE_VALUE) > 2 else 40

        if (_COMMAND == 11):
            _SEND_DATA = "CMD|11|{}|$".format(_TOUCH_FLAG)
            wifi.listenSocket.sendto(_SEND_DATA.encode(), addr)
            _TOUCH_FLAG = 0
            continue

        if (_COMMAND == 13):
            # [추가] CMD|13|trim|$ : 직진 쏠림 보정값 설정 (재업로드 없이 노트북에서 조절)
            try:
                _STRAIGHT_TRIM = int(_REC_PARSE_VALUE[1])
                if len(_REC_PARSE_VALUE) > 2:  # [추가] 속도도 함께 설정 (60~200 범위로 제한)
                    _STRAIGHT_SPEED = max(60, min(200, int(_REC_PARSE_VALUE[2])))
                print("[보정] 직진 trim =", _STRAIGHT_TRIM, "speed =", _STRAIGHT_SPEED)
            except Exception:
                pass
            continue

        if (_COMMAND == 12):
            _DATA = int(_REC_PARSE_VALUE[1])
            if mp3 is not None:
                try:
                    mp3.play(_DATA)
                    mp3.play()
                except Exception:
                    pass
            continue


# [추가] 내장 동작 번호 -> (동작 이름, 재생 대기시간) (BLE 버전 dong_zuo_zu_yun_xing과 동일)
BUILTIN_ACTIONS = {
    1: ("left_foot_kick", 3), 2: ("right_foot_kick", 3), 3: ("stand_four_legs", 2),
    4: ("sit_dowm", 2), 5: ("go_prone", 2), 6: ("stand_two_legs", 4),
    7: ("handshake", 4), 8: ("scrape_a_bow", 4), 9: ("nodding_motion", 2),
    10: ("boxing", 2), 11: ("stretch_oneself", 2), 12: ("pee", 2),
    13: ("press_up", 2), 14: ("rotation_pitch", 2), 15: ("rotation_roll", 2),
}


def run_builtin_action(num):
    if num in BUILTIN_ACTIONS:
        name, wait_sec = BUILTIN_ACTIONS[num]
        mechdog.action_run(name)
        time.sleep(wait_sec)


def start_main1():
    global _SONER_DISTANCE, _RUN_STEP, mechdog, _ACTION_TYPE, _ACTION_NUM, _STRAIGHT_TRIM, _STRAIGHT_SPEED, _LAST_ADDR
    global _PAUSED, _SPIN_REMAIN
    global _RUN_DIR, i2csonar, _ROTATE_DEG, _ROTATE_COUNT

    step = 0
    while True:
        if (step == 0):
            step = _RUN_STEP
            _RUN_STEP = 0
            time.sleep(0.05)
        else:
            if (step == 2):
                mechdog.set_default_pose(duration=500)
                time.sleep(1)
                # [수정] 기존: action_run("8")처럼 번호를 그대로 넘겨서 내장 동작이 실행 안 됨
                # BLE 버전과 동일하게 type=1이면 번호 -> 동작 이름으로 변환해서 실행
                if _ACTION_TYPE == 1:
                    run_builtin_action(_ACTION_NUM)
                else:
                    mechdog.action_run(str(_ACTION_NUM))
            if (step == 3):
                while True:
                    if (_RUN_DIR == 0):
                        mechdog.move(0, 0)
                        break
                    elif (_RUN_DIR == 1):
                        mechdog.move(80, -40)
                        time.sleep(0.01)  # [추가] WiFi 수신 루프에 CPU 양보
                        continue
                    elif (_RUN_DIR == 2):
                        mechdog.move(90, -25)
                        time.sleep(0.01)  # [추가]
                        continue
                    elif (_RUN_DIR == 3):
                        mechdog.move(_STRAIGHT_SPEED, _STRAIGHT_TRIM)  # [수정] 직진 속도/쏠림 보정값 적용
                        time.sleep(0.01)  # [추가]
                        continue
                    elif (_RUN_DIR == 4):
                        mechdog.move(90, 25)
                        time.sleep(0.01)  # [추가]
                        continue
                    elif (_RUN_DIR == 5):
                        mechdog.move(80, 40)
                        time.sleep(0.01)  # [추가]
                        continue
                    elif (_RUN_DIR == 6):
                        mechdog.move(-40, -20)
                        time.sleep(0.01)  # [추가]
                        continue
                    elif (_RUN_DIR == 7):
                        mechdog.move(-40, 0)
                        time.sleep(0.01)  # [추가]
                        continue
                    elif (_RUN_DIR == 8):
                        mechdog.move(-40, 20)
                        time.sleep(0.01)  # [추가]
                        continue
            if (step == 9):
                _done = 0
                while _done < _ROTATE_COUNT:
                    if _PAUSED:  # [추가] 터치 일시정지 -> 회전 중단, 남은 양 기억
                        break
                    mechdog.move(1, _ROTATE_DEG)
                    time.sleep(0.05)
                    _done += 1
                mechdog.move(0, 0)
                if _PAUSED and _done < _ROTATE_COUNT:
                    _SPIN_REMAIN = _ROTATE_COUNT - _done
                    step = 0
                    continue  # 완료 신호 보내지 않음 (재개 후 남은 회전을 마치면 보냄)
                # [추가] 실제로 회전이 끝난 시점을 노트북에 알림 (BLE 버전의 CMD|9|DONE 복구)
                if _LAST_ADDR is not None:
                    try:
                        wifi.listenSocket.sendto(b"CMD|9|DONE|$", _LAST_ADDR)
                    except Exception:
                        pass
            step = 0


def start_main2():
    # [수정] Tier1 안전장치
    # 기존: mechdog.move(0,0)만 호출 -> start_main1의 이동 루프가 _RUN_DIR을 보고
    #       바로 다시 move(120,0)을 호출해서 정지가 실제로는 안 먹혔음.
    # 변경: _RUN_DIR = 0 으로 바꿔서 이동 루프 자체가 정지 분기로 빠지게 함.
    global _SONER_DISTANCE, i2csonar, mechdog, _LAST_CMD_TIME, _RUN_DIR
    global _PAUSED, _PAUSE_NOTIFIED, _SPIN_REMAIN, _RUN_STEP, _ROTATE_COUNT
    time.sleep(2)
    while True:
        _SONER_DISTANCE = i2csonar.getDistance()

        # (0) [추가] 터치 일시정지 상태가 바뀌면 노트북에 알림 + LED
        if _PAUSED != _PAUSE_NOTIFIED:
            _PAUSE_NOTIFIED = _PAUSED
            if _PAUSED:
                _RUN_DIR = 0
                try:
                    i2csonar.setRGB(0, 0xff, 0x00, 0x00)  # 일시정지 = 빨간색
                except Exception:
                    pass
                print("[터치] 일시정지")
            else:
                print("[터치] 재개")
                if _SPIN_REMAIN > 0:  # 회전 도중 멈췄으면 남은 회전 마저
                    _ROTATE_COUNT = _SPIN_REMAIN
                    _SPIN_REMAIN = 0
                    _RUN_STEP = 9
            if _LAST_ADDR is not None:
                for _ in range(2):
                    try:
                        wifi.listenSocket.sendto("CMD|14|{}|$".format(_PAUSED).encode(), _LAST_ADDR)
                    except Exception:
                        pass
        if _PAUSED:
            _RUN_DIR = 0  # 일시정지 중에는 계속 정지 유지

        # (1) 근접 즉시정지: 전진 계열(1~5)일 때만. 후진(6~8)은 장애물에서 벗어나는 동작이라 막지 않음
        if 1 <= _RUN_DIR <= 5 and _SONER_DISTANCE < TIER1_STOP_DISTANCE:
            _RUN_DIR = 0
            print("[Tier1] 근접 {}cm - 정지".format(_SONER_DISTANCE))
            try:
                i2csonar.setRGB(0, 0xff, 0x00, 0x00)  # [추가] 로봇 자체 정지 시 빨간색
            except Exception:
                pass

        # (2) 통신두절 자동정지: 이동 중일 때만 검사 (대기/인사동작 중에는 간섭하지 않음)
        if _RUN_DIR != 0 and time.ticks_diff(time.ticks_ms(), _LAST_CMD_TIME) > COMM_TIMEOUT_MS:
            _RUN_DIR = 0
            print("[Tier1] 통신두절 - 정지")
            try:
                i2csonar.setRGB(0, 0xff, 0x00, 0x00)  # [추가] 통신두절 정지 시 빨간색
            except Exception:
                pass

        time.sleep(0.08)


Hiwonder.startMain(start_main)
Hiwonder.startMain(start_main1)
Hiwonder.startMain(start_main2)

