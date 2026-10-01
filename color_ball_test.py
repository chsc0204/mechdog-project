"""
로봇 카메라 색깔 공 인식 테스트 (로봇은 움직이지 않음)

목적: 출발지 표식용 색깔 공(MechDog 구성품)을 몇 m까지 안정적으로 찾는지 확인
방식: HSV 색 범위로 공 후보를 찾고, 원형도(동그란 정도)로 한 번 더 걸러냄
      -> 같은 색의 네모난 물건(바구니 등)은 제외됨

사전 준비: pip install opencv-python requests numpy (YOLO 테스트 때 이미 설치됨)

사용법:
  1. 노트북을 카메라와 같은 핫스팟에 연결, 브라우저 스트리밍 창은 닫기
  2. 실행: python color_ball_test.py  (초록/파랑/빨강 공을 동시에 인식)
  3. 공이 잘 안 잡히면 'Tuning' 창에서 조절
     - 'Edit 0G 1B 2R' 슬라이더로 조절할 색 선택 (0=초록, 1=파랑, 2=빨강)
     - Mask 창에는 선택한 색만 표시됨. 그 공만 하얗게 보이면 잘 맞춘 것
  4. 공을 50cm, 1m, 1.5m, 2m 에 두고 인식 여부 확인

키:
  q : 종료
  s : 화면 저장 (snapshots 폴더)
  p : 세 색의 현재 설정값 출력 (코드의 PRESETS에 복사해서 고정)
"""

import os
import time

import cv2
import numpy as np
import requests

STREAM_URL = "http://172.20.10.7:81/stream"
FLIP_MODE = 0          # yolo_stream_test.py와 동일 (0=상하반전)
DISPLAY_SCALE = 2

# 인식할 공 색깔 (MechDog 구성품: 초록/파랑/빨강(분홍빛)) - 동시에 모두 인식
BALL_COLORS = ["green", "blue", "red"]

# 색깔별 기본 HSV 범위 (H: 0~179, S/V: 0~255). 조명에 따라 슬라이더로 조절
PRESETS = {
    "green": [40, 80, 60, 255, 45, 255],    # ball_vision.py GREEN_HSV와 동일 (클릭 실측 기준)
    "blue":  [95, 130, 100, 255, 50, 255],
    "red":   [160, 179, 70, 255, 70, 255],  # 분홍빛 빨강. 0~8 구간도 자동 포함
}
DRAW_COLOR = {"green": (0, 220, 0), "blue": (255, 120, 0), "red": (80, 80, 255)}  # BGR

MIN_AREA_PX = 15        # 이보다 작은 덩어리는 노이즈로 무시 (240x240 기준 픽셀 수)
MIN_CIRCULARITY = 0.65  # 윤곽 기준 원형도 (1.0 = 완벽한 원)
MIN_FILL = 0.70         # 외접원 채움 비율: 공 ~0.8~1.0, 네모 ~0.6 -> 네모난 물건 제외의 핵심 기준


# ball_vision.py 와 동일한 밝기 보정 (에스코트와 같은 조건에서 튜닝하기 위함)
SOFT_BRIGHTNESS = True
TARGET_MEAN_V = 110
MAX_GAIN = 2.5


def brighten(frame):
    if not SOFT_BRIGHTNESS:
        return frame, 1.0
    mean_v = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 2].mean()
    gain = min(MAX_GAIN, TARGET_MEAN_V / max(mean_v, 1.0))
    if gain <= 1.05:
        return frame, 1.0
    return cv2.convertScaleAbs(frame, alpha=gain, beta=0), gain


# 클릭한 지점의 HSV 확인 (공과 벽/바닥을 각각 클릭해서 비교)
_click = {"frame": None}


def on_click(event, x, y, flags, param):
    if event != cv2.EVENT_LBUTTONDOWN or _click["frame"] is None:
        return
    f = _click["frame"]
    fx, fy = min(x // DISPLAY_SCALE, f.shape[1] - 1), min(y // DISPLAY_SCALE, f.shape[0] - 1)
    patch = f[max(0, fy - 2):fy + 3, max(0, fx - 2):fx + 3]   # 5x5 평균
    h, s, v = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3).mean(axis=0)
    print(f"[클릭] ({fx},{fy})  H={h:.0f}  S={s:.0f}  V={v:.0f}")


def mjpeg_frames(url):
    while True:
        try:
            print(f"스트림 접속 중: {url}")
            stream = requests.get(url, stream=True, timeout=5)
            buf = b""
            print("스트림 연결 성공")
            for chunk in stream.iter_content(chunk_size=4096):
                buf += chunk
                start = buf.find(b"\xff\xd8")
                end = buf.find(b"\xff\xd9", start + 2) if start != -1 else -1
                if start != -1 and end != -1:
                    jpg = buf[start:end + 2]
                    buf = buf[end + 2:]
                    frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        yield frame
                elif len(buf) > 2_000_000:
                    buf = b""
        except requests.exceptions.RequestException as e:
            print(f"스트림 끊김 ({type(e).__name__}) - 2초 후 재접속")
            time.sleep(2)


TB_NAMES = ["H min", "H max", "S min", "S max", "V min", "V max"]


def make_trackbars():
    cv2.namedWindow("Tuning")
    cv2.createTrackbar("Edit 0G 1B 2R", "Tuning", 0, len(BALL_COLORS) - 1, lambda x: None)
    for name, val, mx in zip(TB_NAMES, PRESETS[BALL_COLORS[0]], [179, 179, 255, 255, 255, 255]):
        cv2.createTrackbar(name, "Tuning", val, mx, lambda x: None)
    cv2.createTrackbar("Fill x100", "Tuning", int(MIN_FILL * 100), 100, lambda x: None)


_editing = [0]


def sync_trackbars():
    """선택한 색의 값을 슬라이더와 동기화. 반환: (편집 중인 색, 채움 기준)"""
    idx = cv2.getTrackbarPos("Edit 0G 1B 2R", "Tuning")
    color = BALL_COLORS[idx]
    if idx != _editing[0]:  # 편집 대상 색이 바뀌면 그 색의 저장값을 슬라이더에 표시
        _editing[0] = idx
        for name, val in zip(TB_NAMES, PRESETS[color]):
            cv2.setTrackbarPos(name, "Tuning", val)
    else:                   # 슬라이더 값을 현재 색에 저장
        PRESETS[color] = [cv2.getTrackbarPos(n, "Tuning") for n in TB_NAMES]
    return color, cv2.getTrackbarPos("Fill x100", "Tuning") / 100.0


def find_ball(frame, color, hmin, hmax, smin, smax, vmin, vmax, min_fill):
    """가장 그럴듯한 공 1개 반환: (cx, cy, radius, area_pct, fill) 또는 None"""
    hsv = cv2.cvtColor(cv2.GaussianBlur(frame, (5, 5), 0), cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (hmin, smin, vmin), (hmax, smax, vmax))
    if color == "red":  # 빨강은 색상환 양 끝에 걸쳐 있음
        mask |= cv2.inRange(hsv, (0, smin, vmin), (8, smax, vmax))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = frame.shape[:2]
    best = None
    for c in contours:
        if cv2.contourArea(c) < MIN_AREA_PX:
            continue
        # 반사광 때문에 공이 초승달 모양으로 잡혀도, 바깥을 감싼 볼록 껍질은 원이 됨
        c = cv2.convexHull(c)
        area = cv2.contourArea(c)
        perim = cv2.arcLength(c, True)
        circ = 4 * np.pi * area / (perim * perim) if perim > 0 else 0
        if circ < MIN_CIRCULARITY:
            continue
        (cx, cy), r = cv2.minEnclosingCircle(c)
        fill = area / (np.pi * r * r) if r > 0 else 0
        if fill < min_fill:
            continue
        cand = (cx, cy, r, area / (w * h) * 100, fill)
        if best is None or cand[3] > best[3]:
            best = cand
    return best, mask


def main():
    make_trackbars()
    cv2.namedWindow("Ball Test")
    cv2.setMouseCallback("Ball Test", on_click)
    os.makedirs("snapshots", exist_ok=True)
    fps, last_t, last_log = 0.0, time.time(), 0.0

    for frame in mjpeg_frames(STREAM_URL):
        if FLIP_MODE is not None:
            frame = cv2.flip(frame, FLIP_MODE)
        frame, gain = brighten(frame)
        _click["frame"] = frame
        h, w = frame.shape[:2]
        editing, min_fill = sync_trackbars()

        s = DISPLAY_SCALE
        view = cv2.resize(frame, (w * s, h * s))
        cv2.line(view, (w * s // 2, 0), (w * s // 2, h * s), (0, 255, 255), 1)

        now = time.time()
        fps = 0.9 * fps + 0.1 * (1.0 / max(now - last_t, 1e-6))
        last_t = now

        lines = [f"FPS {fps:.1f} x{gain:.1f} | editing: {editing} | click = HSV"]
        edit_mask = None
        for color in BALL_COLORS:
            ball, mask = find_ball(frame, color, *PRESETS[color], min_fill)
            if color == editing:
                edit_mask = mask
            if ball:
                cx, cy, r, area_pct, fill = ball
                offset = (cx - w / 2) / (w / 2)
                cv2.circle(view, (int(cx * s), int(cy * s)), int(r * s), DRAW_COLOR[color], 2)
                cv2.putText(view, color, (int(cx * s) - 20, int((cy - r) * s) - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, DRAW_COLOR[color], 2)
                lines.append(f"{color}: offset={offset:+.2f} r={r:.1f}px fill={fill:.2f}")
            else:
                lines.append(f"{color}: not found")

        for i, text in enumerate(lines):
            cv2.putText(view, text, (8, 20 + i * 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 2)

        if now - last_log >= 1.0:
            print(" | ".join(lines))
            last_log = now

        cv2.imshow("Ball Test", view)
        cv2.imshow("Mask", cv2.resize(edit_mask, (w, h)))
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("s"):
            path = f"snapshots/ball_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
            cv2.imwrite(path, view)
            print(f"저장: {path}")
        if key == ord("p"):
            print("현재 설정 (코드의 PRESETS에 복사해서 고정):")
            for c in BALL_COLORS:
                print(f'    "{c}": {PRESETS[c]},')
            print(f"MIN_FILL = {min_fill}")

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
