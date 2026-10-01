"""
ball_vision.py - 로봇 카메라로 출발지 표식(초록 공)을 백그라운드 추적

escort_wifi.py 가 import 해서 사용. 값은 color_ball_test.py 실측(2026-09-28) 기준:
  초록 공 r(반지름 px): 50cm=13.7 / 1m=7.6 / 1.5m=5.9  (r x 거리(m) ~ 7~9)

단독 실행하면 추적 결과만 출력 (로봇 명령 없음): python ball_vision.py
"""

import threading
import time
from collections import deque

import cv2
import numpy as np
import requests

STREAM_URL = "http://172.20.10.7:81/stream"
CONTROL_URL = "http://172.20.10.7/control"   # 카메라 설정 (브라우저 설정 화면과 같은 기능)

# 시작 시 카메라에 자동 적용할 설정 (카메라가 재부팅되면 전부 꺼진 상태로 시작하기 때문)
CAMERA_SETTINGS = {
    "aec": 1,       # 자동 노출 (AEC SENSOR)
    "aec2": 1,      # 자동 노출 DSP (AEC DSP)
    "agc": 1,       # 자동 밝기/게인 (AGC)
    "awb": 1,       # 자동 화이트밸런스 (AWB)
    "awb_gain": 1,  # AWB Gain
    "ae_level": 1,  # 노출 보정 (-2~2, 어두운 실내라 +1)
}
FLIP_MODE = 0            # 0=상하반전 (yolo/color 테스트와 동일)

# 실측(2026-09-28 클릭 측정): 공 H 43~83(중심 61~72) S 71~146 / 벽타일 H 99~119 S 39~91 / 바닥 H 10~11
# -> 색상(H)으로 구분: 상한 80으로 타일과 여유 확보, S 60 이상으로 흐린 타일 제외
GREEN_HSV = (40, 80, 60, 255, 45, 255)

# 소프트웨어 밝기 보정: 이 카메라 센서는 자동 노출 설정을 거부함(HTTP 500) -> 노트북에서 보정
SOFT_BRIGHTNESS = True
TARGET_MEAN_V = 110      # 화면 평균 밝기가 이보다 낮으면 끌어올림
MAX_GAIN = 2.5           # 최대 밝기 배율 (너무 올리면 잡음도 커짐)
MIN_AREA_PX = 20         # 이보다 작은 점은 잡음 (r 약 2.5px 미만)
MIN_CIRCULARITY = 0.65
MIN_FILL = 0.70
R_MIN = 4.5              # 이보다 작은 원은 잡음 (1m 공 ~7.6px, 1.5m ~5.9px) - 3~4px 잡음 제외
R_MAX = 35.0             # 이보다 큰 원은 공이 아님 (공이 코앞이어도 r~30px) - 매트 등 오인식 방지
CONFIRM_WINDOW = 5       # 최근 N프레임 중
CONFIRM_FRAMES = 3       # M프레임 이상 비슷한 위치에서 보이면 공으로 인정 (먼 공은 깜빡이므로 연속 조건 X)
CONFIRM_SPREAD = 0.4     # 현재 위치에서 offset 차이가 이 이내인 프레임만 같은 공으로 셈

SHOW_VIEW = True         # 추적 화면 창 표시 (문제 생기면 False)
DISPLAY_SCALE = 2


def brighten(frame):
    """어두운 영상의 밝기를 목표 평균까지 끌어올림. (보정된 영상, 배율) 반환"""
    if not SOFT_BRIGHTNESS:
        return frame, 1.0
    mean_v = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 2].mean()
    gain = min(MAX_GAIN, TARGET_MEAN_V / max(mean_v, 1.0))
    if gain <= 1.05:
        return frame, 1.0
    return cv2.convertScaleAbs(frame, alpha=gain, beta=0), gain


def find_ball(frame, hsv_range=GREEN_HSV, min_fill=MIN_FILL):
    """가장 큰 공 1개: (cx, cy, r, fill) 또는 None  (frame은 brighten() 거친 영상 권장)"""
    hmin, hmax, smin, smax, vmin, vmax = hsv_range
    hsv = cv2.cvtColor(cv2.GaussianBlur(frame, (5, 5), 0), cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (hmin, smin, vmin), (hmax, smax, vmax))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    for c in contours:
        if cv2.contourArea(c) < MIN_AREA_PX:
            continue
        c = cv2.convexHull(c)  # 반사광으로 초승달처럼 잡혀도 바깥 모양으로 판단
        area = cv2.contourArea(c)
        perim = cv2.arcLength(c, True)
        circ = 4 * np.pi * area / (perim * perim) if perim > 0 else 0
        if circ < MIN_CIRCULARITY:
            continue
        (cx, cy), r = cv2.minEnclosingCircle(c)
        fill = area / (np.pi * r * r) if r > 0 else 0
        if fill < min_fill or r > R_MAX or r < R_MIN:
            continue
        if best is None or r > best[2]:  # 가장 큰 것 (매트 같은 큰 오인식은 R_MAX로 이미 제외)
            best = (cx, cy, r, fill)
    return best


class BallTracker(threading.Thread):
    """스트림을 계속 읽으며 최신 공 위치를 갱신. get()으로 최신값 조회"""

    def __init__(self, url=STREAM_URL):
        super().__init__(daemon=True)
        self.url = url
        self.running = True
        self.connected = False
        self.fps = 0.0
        self._lock = threading.Lock()
        self._latest = None       # (offset, r, 시각) - CONFIRM_FRAMES 연속 확인된 것만
        self._streak = 0
        self._recent = deque(maxlen=CONFIRM_WINDOW)  # 최근 프레임별 (offset, r) 또는 None
        self._raw = None          # [진단] 확인 전 후보 (offset, r, 시각)
        self._show = SHOW_VIEW
        self.gain = 1.0
        self.frame_count = 0      # 처리한 프레임 수 (멈춘 뒤 새 영상 기다리기용)
        self._jpg = None
        self._jpg_seq = 0
        self._jpg_lock = threading.Lock()
        self._frame = None        # (보정된 최신 프레임, 시각) - 사람 인식/사진 저장용
        self.overlay = None       # (시각, [(box, label), ...]) - 사람 인식 결과를 화면에 표시

    def get(self, max_age=0.6):
        """max_age초 이내에 본 공이면 (offset, r), 아니면 None
        offset: -1(화면 왼쪽 끝) ~ +1(오른쪽 끝), r: 반지름 px (클수록 가까움)"""
        with self._lock:
            if self._latest and time.time() - self._latest[2] <= max_age:
                return self._latest[0], self._latest[1]
        return None

    def latest_frame(self, max_age=1.0):
        """가장 최근 프레임의 복사본 (없거나 오래됐으면 None)"""
        f = self._frame
        if f is None or time.time() - f[1] > max_age:
            return None
        return f[0].copy()

    def wait_new_frames(self, n=5, timeout=2.0):
        """지금부터 새 프레임 n장이 처리될 때까지 대기 (영상 지연 대비: 멈추기 전 화면으로 판단하지 않기)"""
        target = self.frame_count + n
        end = time.time() + timeout
        while self.frame_count < target and time.time() < end:
            time.sleep(0.02)

    def candidate_offset(self, max_age=0.5):
        """확인 전 후보의 offset (없으면 None)"""
        raw = self._raw
        return raw[0] if raw is not None and time.time() - raw[2] <= max_age else None

    def has_candidate(self, max_age=0.5):
        """확인 전이라도 최근에 공 후보가 보였는지"""
        raw = self._raw
        return raw is not None and time.time() - raw[2] <= max_age

    def diag(self):
        """[진단] 최근 1초 내 후보 상태를 문자열로"""
        raw = self._raw
        if raw is None or time.time() - raw[2] > 1.0:
            return f"카메라: 후보 없음 (FPS {self.fps:.0f})"
        return (f"카메라: 후보 r={raw[1]:.1f}px offset={raw[0]:+.2f}, "
                f"최근{CONFIRM_WINDOW}중 {self._streak}회 (FPS {self.fps:.0f})")

    def stop(self):
        self.running = False

    def _reader(self):
        """[수신 전용 스레드] 스트림을 쉬지 않고 읽어서 가장 최신 JPEG 한 장만 보관
        (처리가 느려도 옛날 영상이 줄 서서 쌓이지 않음 -> 지연 감소)"""
        while self.running:
            try:
                stream = requests.get(self.url, stream=True, timeout=5)
                self.connected = True
                buf = b""
                for chunk in stream.iter_content(chunk_size=4096):
                    if not self.running:
                        return
                    buf += chunk
                    latest = None
                    while True:  # 버퍼 안의 완성된 프레임을 모두 꺼내고 마지막 것만 남김
                        start = buf.find(b"\xff\xd8")
                        end = buf.find(b"\xff\xd9", start + 2) if start != -1 else -1
                        if start == -1 or end == -1:
                            break
                        latest = buf[start:end + 2]
                        buf = buf[end + 2:]
                    if latest is not None:
                        with self._jpg_lock:
                            self._jpg = latest
                            self._jpg_seq += 1
                    if len(buf) > 2_000_000:
                        buf = b""
            except requests.exceptions.RequestException as e:
                self.connected = False
                print(f"[카메라] 스트림 끊김 ({type(e).__name__}) - 재접속 시도")
                time.sleep(2)

    def _frames(self):
        """[처리 스레드] 항상 가장 최신 프레임만 꺼냄 (처리 중에 들어온 옛 프레임은 건너뜀)"""
        last_seq = 0
        while self.running:
            with self._jpg_lock:
                jpg, seq = self._jpg, self._jpg_seq
            if jpg is None or seq == last_seq:
                time.sleep(0.005)
                continue
            last_seq = seq
            frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                yield frame

    def setup_camera(self, retries=3):
        """자동 노출/밝기 설정 전송. 실패 시 이유 출력 후 재시도 (이 센서는 HTTP 500으로 거부 -> 소프트웨어 보정)"""
        for attempt in range(1, retries + 1):
            ok, errors = 0, []
            for var, val in CAMERA_SETTINGS.items():
                try:
                    r = requests.get(CONTROL_URL, params={"var": var, "val": val}, timeout=3)
                    if r.status_code == 200:
                        ok += 1
                    else:
                        errors.append(f"{var}: HTTP {r.status_code}")
                except requests.exceptions.RequestException as e:
                    errors.append(f"{var}: {type(e).__name__}")
            print(f"[카메라] 자동 노출/밝기 설정 {ok}/{len(CAMERA_SETTINGS)}개 적용 (시도 {attempt}/{retries})")
            if ok == len(CAMERA_SETTINGS):
                return True
            if all("HTTP 500" in e for e in errors):
                print("[카메라] 이 센서는 자동 노출 설정을 지원하지 않음 -> 노트북 밝기 보정으로 대체")
                return False
            print(f"         실패 내역: {', '.join(errors[:3])}")
            time.sleep(1.5)
        print("[카메라] 설정 실패 -> 노트북 밝기 보정으로 대체")
        return False

    def run(self):
        self.setup_camera()
        threading.Thread(target=self._reader, daemon=True).start()
        last_t = time.time()
        for frame in self._frames():
            if FLIP_MODE is not None:
                frame = cv2.flip(frame, FLIP_MODE)
            self.frame_count += 1
            frame, self.gain = brighten(frame)
            self._frame = (frame, time.time())
            h, w = frame.shape[:2]
            ball = find_ball(frame)
            now = time.time()
            self.fps = 0.9 * self.fps + 0.1 / max(now - last_t, 1e-6)
            last_t = now
            if ball:
                offset = (ball[0] - w / 2) / (w / 2)
                self._raw = (offset, ball[2], now)
                self._recent.append((offset, ball[2]))
            else:
                self._recent.append(None)
            # 현재 위치 근처에서 잡힌 횟수만 셈 (다른 곳의 잡음이 섞여도 공 확인에 영향 없음)
            if ball:  # 위치와 크기가 모두 비슷해야 같은 공 (잡음이 섞여도 확인 방해 못 함)
                r_now = ball[2]
                self._streak = sum(1 for x in self._recent
                                   if x is not None and abs(x[0] - offset) <= CONFIRM_SPREAD
                                   and 0.6 * r_now <= x[1] <= 1.6 * r_now)
            else:
                self._streak = 0
            if ball and self._streak >= CONFIRM_FRAMES:
                with self._lock:
                    self._latest = (offset, ball[2], now)
            if self._show:
                self._draw(frame, ball)
        if self._show:
            cv2.destroyAllWindows()

    def _draw(self, frame, ball):
        try:
            s = DISPLAY_SCALE
            h, w = frame.shape[:2]
            view = cv2.resize(frame, (w * s, h * s))
            cv2.line(view, (w * s // 2, 0), (w * s // 2, h * s), (0, 255, 255), 1)
            text = f"FPS {self.fps:.1f} x{self.gain:.1f} | "
            if ball:
                cv2.circle(view, (int(ball[0] * s), int(ball[1] * s)), int(ball[2] * s), (0, 220, 0), 2)
                ok = "OK" if self._streak >= CONFIRM_FRAMES else f"checking {self._streak}/{CONFIRM_FRAMES}"
                text += f"green r={ball[2]:.1f}px offset={(ball[0] - w / 2) / (w / 2):+.2f} {ok}"
            else:
                text += "green not found"
            cv2.putText(view, text, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
            ov = self.overlay
            if ov and time.time() - ov[0] < 1.0:  # 사람 인식 박스 (주황)
                for (x1, y1, x2, y2), label in ov[1]:
                    cv2.rectangle(view, (int(x1 * s), int(y1 * s)), (int(x2 * s), int(y2 * s)), (0, 140, 255), 2)
                    cv2.putText(view, label, (int(x1 * s), max(15, int(y1 * s) - 5)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 140, 255), 2)
            cv2.imshow("Escort Camera", view)
            cv2.waitKey(1)
        except cv2.error as e:
            print(f"[카메라] 화면 표시 불가, 표시 없이 계속 ({e})")
            self._show = False


if __name__ == "__main__":
    t = BallTracker()
    t.start()
    try:
        while True:
            time.sleep(0.5)
            print("연결됨" if t.connected else "연결 안 됨", "|", t.get(), f"| FPS {t.fps:.1f}")
    except KeyboardInterrupt:
        t.stop()
