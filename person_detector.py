"""
person_detector.py - 카메라 영상에서 전방의 사람을 백그라운드로 감지 (YOLO)

escort_wifi.py 가 사용:
  - 이동 중 앞에 사람이 크게 보이면 -> 멈추고 비켜갈 때까지 대기
  - 거리센서가 장애물을 감지했을 때 -> 방금 전에 사람이 보였으면 사람으로 판단해 대기,
                                       아니면 기존 S자 회피
  (15cm 거리에서는 카메라에 발만 보여 인식이 어려우므로, 다가오는 동안 미리 봐둔 결과를 사용)

YOLO의 COCO 'person'(사람) 클래스만 사용. 사람은 YOLO가 가장 잘 인식하는 클래스라
저시점·저해상도 카메라에서도 비교적 안정적.

단독 실행하면 인식 결과만 출력 (로봇 명령 없음): python person_detector.py
"""

import threading
import time

MODEL_NAME = "yolov8n.pt"   # 가장 빠른 모델 (사람 인식은 n으로 충분)
CONF = 0.40                 # 사람 신뢰도 기준
IMGSZ = 320
RATE_HZ = 4.0               # 초당 추론 횟수 (CPU 부담 조절)
CENTER_TOL = 0.6            # 화면 중앙에서 이 범위(-0.6~+0.6) 안이면 "전방"


class PersonDetector(threading.Thread):
    def __init__(self, tracker):
        """tracker: ball_vision.BallTracker (최신 프레임 제공 + 화면에 박스 표시)"""
        super().__init__(daemon=True)
        self.tracker = tracker
        self.running = True
        self.ready = False
        self._lock = threading.Lock()
        self._last = None   # (시각, [(offset, area_pct, conf, box), ...])

    def run(self):
        try:
            from ultralytics import YOLO
            model = YOLO(MODEL_NAME)
            self.ready = True
            print(f"[사람 인식] {MODEL_NAME} 준비 완료")
        except Exception as e:
            print(f"[사람 인식] 모델 로딩 실패 - 사람 구분 없이 진행 ({e})")
            return

        period = 1.0 / RATE_HZ
        while self.running:
            t0 = time.time()
            frame = self.tracker.latest_frame(max_age=0.5)
            if frame is not None:
                h, w = frame.shape[:2]
                res = model.predict(frame, imgsz=IMGSZ, conf=CONF, classes=[0], verbose=False)[0]
                people = []
                for b in res.boxes:
                    x1, y1, x2, y2 = b.xyxy[0].tolist()
                    offset = ((x1 + x2) / 2 - w / 2) / (w / 2)
                    area = (x2 - x1) * (y2 - y1) / (w * h) * 100
                    people.append((offset, area, float(b.conf), (x1, y1, x2, y2)))
                now = time.time()
                with self._lock:
                    self._last = (now, people)
                self.tracker.overlay = (now, [(p[3], f"person {p[2]:.2f}") for p in people])
            time.sleep(max(0.0, period - (time.time() - t0)))

    def stop(self):
        self.running = False

    def person_ahead(self, max_age=1.5, min_area=3.0):
        """max_age초 이내에 전방(화면 중앙 부근)에서 화면의 min_area% 이상 크기의 사람이 보였으면
        (offset, area, conf) 반환, 아니면 None"""
        with self._lock:
            last = self._last
        if last is None or time.time() - last[0] > max_age:
            return None
        ahead = [p for p in last[1] if abs(p[0]) <= CENTER_TOL and p[1] >= min_area]
        if not ahead:
            return None
        best = max(ahead, key=lambda p: p[1])
        return best[0], best[1], best[2]


if __name__ == "__main__":
    from ball_vision import BallTracker
    tracker = BallTracker()
    tracker.start()
    det = PersonDetector(tracker)
    det.start()
    try:
        while True:
            time.sleep(0.5)
            p = det.person_ahead()
            print("전방 사람:", f"offset={p[0]:+.2f} 크기={p[1]:.1f}% 신뢰도={p[2]:.2f}" if p else "없음")
    except KeyboardInterrupt:
        det.stop()
        tracker.stop()
