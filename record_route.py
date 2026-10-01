"""
record_route.py - 경로 기록 모드 (키보드로 로봇을 직접 몰아서 경로 저장)

목적: 시연 공간이 정해지면 목적지마다 "가는 길"과 "돌아오는 길"을 직접 몰아서 기록
      -> destinations.json 에 저장 -> escort_wifi.py 가 그대로 재생
      (거리/각도 계산, U턴 보정 불필요)

조작 키 (Enter 불필요, 한 번 누르면 다른 키를 누를 때까지 계속 그 동작):
  W : 직진            S : 후진
  A : 약하게 좌회전    D : 약하게 우회전   (걸으면서 회전)
  Q : 강하게 좌회전    E : 강하게 우회전   (걸으면서 회전)
  J : 제자리 좌회전    L : 제자리 우회전   (1회 누를 때마다 한 번씩)
  Space : 정지 (정지 시간은 기록 안 됨)
  Enter : 기록 끝내고 저장    X : 취소

사용법:
  python record_route.py
  1. 목적지 코드 선택 (팀 기준 코드)
  2. "가는 길" 기록: 로봇을 출발지에 두고 목적지까지 몰기 -> Enter
  3. "돌아오는 길" 기록: 목적지에서 출발지까지 몰기 -> Enter
     (마지막에 출발 때와 같은 방향을 보도록 돌려놓기)
  4. 저장 -> escort_wifi.py 실행해서 목적지 코드 입력으로 재생 확인

주의: Windows 전용 (msvcrt 키 입력). 로봇 IP 등은 escort_wifi.py 설정을 그대로 사용
"""

import json
import os
import shutil
import time

try:
    import msvcrt
except ImportError:
    raise SystemExit("Windows에서만 실행 가능합니다 (msvcrt 필요)")

import escort_wifi as ew

ROUTE_FILE = ew.DEST_FILE   # destinations.json

# 팀 목적지 코드 (B파트 dialog_sessions.destination 허용값 기준)
TEAM_DESTINATIONS = {
    "meeting_room_1": "회의실 1",
    "safety_training_room": "안전교육실",
    "inbound_dock": "입고장",
    "outbound_dock": "출고장",
    "inspection_area": "검수구역",
    "elevator_hall": "엘리베이터 홀",
    "exit_gate": "출구",
}

KEY_DIR = {"w": 3, "s": 7, "a": 4, "d": 2, "q": 5, "e": 1}
DIR_NAME = {3: "직진", 7: "후진", 4: "약좌회전", 2: "약우회전", 5: "강좌회전", 1: "강우회전"}
SPIN_KEYS = {"j": 1, "l": -1}   # 제자리 회전 방향 (+좌 / -우)
MIN_SEGMENT = 0.15              # 이보다 짧은 동작은 기록 안 함 (키 실수 방지)


def record(robot, label):
    """키 입력으로 로봇을 몰면서 스텝 기록. 반환: 스텝 리스트 또는 None(취소)"""
    print(f"\n===== [{label}] 기록 시작 =====")
    print("W직진 S후진 A/D약회전 Q/E강회전 J/L제자리회전 Space정지 | Enter저장 X취소")
    steps = []
    cur_dir, seg_start = 0, None

    def close_segment():
        nonlocal cur_dir, seg_start
        if cur_dir != 0 and seg_start is not None:
            dur = round(time.time() - seg_start, 2)
            if dur >= MIN_SEGMENT:
                steps.append({"dir": cur_dir, "hold_sec": dur, "desc": DIR_NAME[cur_dir]})
                print(f"    기록: {DIR_NAME[cur_dir]} {dur}초")
        cur_dir, seg_start = 0, None

    while True:
        if not msvcrt.kbhit():
            time.sleep(0.02)
            continue
        key = msvcrt.getwch().lower()

        if key in KEY_DIR:
            d = KEY_DIR[key]
            if d == cur_dir:
                continue  # 같은 키 반복은 무시 (계속 진행 중)
            close_segment()
            robot.move(d)
            cur_dir, seg_start = d, time.time()
            print(f"  > {DIR_NAME[d]}")
        elif key == " ":
            close_segment()
            robot.stop()
            print("  > 정지")
        elif key in SPIN_KEYS:
            close_segment()
            robot.stop()
            deg = ew.SPIN_DEG * SPIN_KEYS[key]
            robot.rotate(deg, ew.SPIN_COUNT)
            steps.append({"spin": deg, "count": ew.SPIN_COUNT,
                          "desc": "제자리 " + ("좌" if deg > 0 else "우") + "회전"})
            print(f"  > 제자리 {'좌' if deg > 0 else '우'}회전 (기록됨)")
            time.sleep(ew.SPIN_COUNT * 0.05 + 0.3)
        elif key == "\r":
            close_segment()
            robot.stop()
            print(f"===== [{label}] 기록 완료: {len(steps)}개 스텝 =====")
            return steps
        elif key == "x":
            robot.stop()
            print(f"===== [{label}] 취소 =====")
            return None


def load_routes():
    if os.path.exists(ROUTE_FILE):
        with open(ROUTE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"_note": "record_route.py 로 기록한 경로. 키=팀 목적지 코드"}


def save_routes(routes):
    if os.path.exists(ROUTE_FILE):
        shutil.copy(ROUTE_FILE, ROUTE_FILE + ".bak")  # 덮어쓰기 전 백업
    with open(ROUTE_FILE, "w", encoding="utf-8") as f:
        json.dump(routes, f, ensure_ascii=False, indent=2)
    print(f"저장 완료: {ROUTE_FILE} (이전 파일은 {ROUTE_FILE}.bak)")


def print_steps(title, steps):
    total = sum(s.get("hold_sec", s.get("count", 0) * 0.05) for s in steps)
    print(f"  {title}: {len(steps)}개 스텝, 약 {total:.1f}초")
    for s in steps:
        if "spin" in s:
            print(f"    - {s['desc']} (count {s['count']})")
        else:
            print(f"    - {s['desc']} {s['hold_sec']}초")


def main():
    robot = ew.RobotLink(ew.ROBOT_IP, ew.ROBOT_PORT)
    try:
        if robot.request_battery() is None:
            print(f"[경고] 로봇({ew.ROBOT_IP}) 응답 없음 - 연결 확인 후 다시 실행하세요")
            return
        print("로봇 연결 확인")

        routes = load_routes()
        codes = list(TEAM_DESTINATIONS)
        while True:
            print("\n목적지 코드 (기록됨 = ✔):")
            for i, c in enumerate(codes, 1):
                mark = "✔" if c in routes else " "
                print(f"  {i}. [{mark}] {c} ({TEAM_DESTINATIONS[c]})")
            sel = input("번호 또는 코드 입력 (종료: q): ").strip()
            if sel.lower() == "q":
                break
            code = codes[int(sel) - 1] if sel.isdigit() and 1 <= int(sel) <= len(codes) else sel
            if not code:
                continue
            name = TEAM_DESTINATIONS.get(code) or input(f"'{code}' 표시 이름: ").strip() or code

            input(f"\n로봇을 출발지에 놓고 Enter (→ '{name}' 가는 길 기록)")
            out = record(robot, f"{name} 가는 길")
            if not out:
                continue
            input(f"\n로봇이 '{name}'에 있는 상태에서 Enter (→ 돌아오는 길 기록)")
            back = record(robot, f"{name} 돌아오는 길")
            if back is None:
                continue

            print(f"\n[{code}] {name}")
            print_steps("가는 길", out)
            print_steps("돌아오는 길", back)
            if input("저장할까요? (y/n): ").strip().lower() == "y":
                routes[code] = {"name": name, "steps": out, "return_steps": back,
                                "recorded_at": time.strftime("%Y-%m-%d %H:%M")}
                save_routes(routes)
    finally:
        robot.close()


if __name__ == "__main__":
    main()
