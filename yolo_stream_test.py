"""
로봇 카메라 + YOLO 객체인식 테스트 (로봇은 움직이지 않음)

목적: 출발지 복귀용 "목표 물체"를 로봇 카메라(240x240)로
      몇 m 거리까지, 초당 몇 프레임으로 인식할 수 있는지 확인

사전 준비 (최초 1회):
  pip install ultralytics opencv-python requests
  (처음 실행 시 yolov8n.pt 모델(약 6MB)을 자동 다운로드 - 인터넷 필요)

사용법:
  1. 노트북을 카메라와 같은 핫스팟에 연결
  2. 브라우저 스트리밍 창은 닫기 (카메라가 동시 접속 1개만 지원하는 경우가 많음)
  3. python yolo_stream_test.py
  4. 목표 물체를 1m, 2m, 3m ... 거리에 두고 인식 여부/신뢰도/화면 비율 기록

키:
  q : 종료
  s : 현재 화면 저장 (테스트 증거용, snapshots 폴더)

화면 표시:
  - 초록 박스: 목표 물체 (TARGET_CLASSES)
  - 회색 박스: 그 외 인식된 물체
  - offset: 화면 중앙 대비 좌우 위치 (-: 왼쪽, +: 오른쪽) -> 조향에 사용 예정
  - area: 화면 대비 박스 크기(%) -> 가까워질수록 커짐, 도착 판정에 사용 예정
"""

import os
import time

import cv2
import numpy as np
import requests
from ultralytics import YOLO

STREAM_URL = "http://172.20.10.7:81/stream"

# 목표 물체 (COCO 클래스 이름). 예: "potted plant"(화분), "chair", "bottle",
# "backpack", "suitcase", "tv", "laptop", "person" 등
TARGET_CLASSES = ["potted plant", "chair", "bench", "suitcase", "backpack"]

MODEL_NAME = "yolov8s.pt"  # n(가장 빠름) < s < m(가장 정확). 처음 쓰는 모델은 자동 다운로드
CONF_THRESHOLD = 0.2    # 이 신뢰도 이상만 표시 (테스트용으로 낮춤)
IMGSZ = 640             # YOLO 입력 크기. 크게 할수록 작은(먼) 물체를 더 잘 잡음, FPS는 감소
DISPLAY_SCALE = 2       # 화면 표시 배율 (240 -> 480)
LOG_INTERVAL = 1.0      # 목표 물체 정보 터미널 출력 주기(초)

# 영상 뒤집기 (카메라가 재부팅되면 브라우저 V-Flip 설정이 초기화되므로 여기서 보정)
# None=그대로, 0=상하반전, 1=좌우반전, -1=상하+좌우(180도 회전)
FLIP_MODE = 0


def mjpeg_frames(url):
    """ESP32-CAM MJPEG 스트림에서 프레임을 하나씩 꺼냄 (끊기면 재접속)"""
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
                    buf = b""  # 비정상 누적 방지
        except requests.exceptions.RequestException as e:
            print(f"스트림 끊김 ({type(e).__name__}) - 2초 후 재접속")
            time.sleep(2)


def main():
    print("YOLO 모델 로딩 중... (처음이면 다운로드)")
    model = YOLO(MODEL_NAME)
    print(f"모델: {MODEL_NAME}, 입력 크기: {IMGSZ}")
    names = model.names
    unknown = [c for c in TARGET_CLASSES if c not in names.values()]
    if unknown:
        print(f"[경고] COCO에 없는 클래스 이름: {unknown}")

    os.makedirs("snapshots", exist_ok=True)
    fps, last_t, last_log = 0.0, time.time(), 0.0

    for frame in mjpeg_frames(STREAM_URL):
        if FLIP_MODE is not None:
            frame = cv2.flip(frame, FLIP_MODE)
        h, w = frame.shape[:2]
        result = model.predict(frame, imgsz=IMGSZ, conf=CONF_THRESHOLD, verbose=False)[0]

        view = cv2.resize(frame, (w * DISPLAY_SCALE, h * DISPLAY_SCALE))
        best = None  # 가장 큰 목표 물체

        for box in result.boxes:
            cls_name = names[int(box.cls)]
            conf = float(box.conf)
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            area = (x2 - x1) * (y2 - y1) / (w * h) * 100
            offset = ((x1 + x2) / 2 - w / 2) / (w / 2)  # -1(왼쪽 끝) ~ +1(오른쪽 끝)

            is_target = cls_name in TARGET_CLASSES
            color = (0, 200, 0) if is_target else (150, 150, 150)
            s = DISPLAY_SCALE
            cv2.rectangle(view, (int(x1 * s), int(y1 * s)), (int(x2 * s), int(y2 * s)), color, 2)
            cv2.putText(view, f"{cls_name} {conf:.2f}", (int(x1 * s), max(15, int(y1 * s) - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            if is_target and (best is None or area > best[3]):
                best = (cls_name, conf, offset, area)

        # FPS
        now = time.time()
        fps = 0.9 * fps + 0.1 * (1.0 / max(now - last_t, 1e-6))
        last_t = now

        status = f"FPS {fps:.1f}"
        if best:
            status += f" | TARGET {best[0]} conf={best[1]:.2f} offset={best[2]:+.2f} area={best[3]:.1f}%"
        else:
            status += " | TARGET not found"
        cv2.putText(view, status, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
        cv2.line(view, (view.shape[1] // 2, 0), (view.shape[1] // 2, view.shape[0]), (0, 255, 255), 1)

        if now - last_log >= LOG_INTERVAL:
            print(status)
            last_log = now

        cv2.imshow("YOLO Stream Test", view)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("s"):
            path = f"snapshots/yolo_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
            cv2.imwrite(path, view)
            print(f"저장: {path}")

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
