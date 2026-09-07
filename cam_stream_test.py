"""
ESP32-CAM 스트림 테스트
- 노트북이 HW_ESP32S3CAM WiFi에 연결된 상태여야 함
- 스트림을 받아서 화면에 실시간으로 보여줌
- 'q' 키로 종료
"""

import cv2

STREAM_URL = "http://192.168.5.1:81/stream"

print("스트림 연결 시도 중...")
cap = cv2.VideoCapture(STREAM_URL)

if not cap.isOpened():
    print("스트림 연결 실패! WiFi가 HW_ESP32S3CAM에 연결되어 있는지 확인하세요.")
    exit()

print("연결 성공! 'q' 키를 누르면 종료합니다.")

while True:
    ret, frame = cap.read()
    if not ret:
        print("프레임을 못 받아옴")
        break

    cv2.imshow("ESP32-CAM Stream", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
