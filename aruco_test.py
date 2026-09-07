"""
ArUco 마커 인식 테스트 (노트북 웹캠 사용, 로봇 필요 없음)
- 화면에 마커가 보이면 ID와 위치를 표시
- 'q' 키로 종료

사전 준비:
1. generate_markers.py 로 마커 이미지 생성 후 인쇄
2. 인쇄한 마커를 웹캠 앞에 놓고 테스트
"""

import cv2

ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
ARUCO_PARAMS = cv2.aruco.DetectorParameters()
detector = cv2.aruco.ArucoDetector(ARUCO_DICT, ARUCO_PARAMS)

# 0: 노트북 기본 웹캠. 여러 개면 1, 2로 바꿔가며 시도
cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("웹캠을 열 수 없습니다.")
    exit()

print("웹캠 연결됨. 마커를 비춰보세요. 'q' 키로 종료.")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detector.detectMarkers(gray)

    if ids is not None:
        cv2.aruco.drawDetectedMarkers(frame, corners, ids)
        for i, marker_id in enumerate(ids.flatten()):
            c = corners[i][0]
            center_x = int(c[:, 0].mean())
            center_y = int(c[:, 1].mean())
            # 화면 중앙 대비 좌우 위치 (에스코트 이동 판단에 활용 가능)
            offset = center_x - frame.shape[1] // 2
            text = f"ID:{marker_id} offset:{offset}"
            cv2.putText(frame, text, (center_x - 40, center_y - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            print(f"마커 발견 - ID:{marker_id}, 중심좌표:({center_x},{center_y}), offset:{offset}")
    else:
        cv2.putText(frame, "마커 없음", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

    cv2.imshow("ArUco Marker Detection Test", frame)

    key = cv2.waitKey(1) & 0xFF
    if key == ord('q') or key == ord('Q'):
        break

cap.release()
cv2.destroyAllWindows()
