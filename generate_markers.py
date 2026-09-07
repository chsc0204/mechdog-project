"""
ArUco 마커 이미지 생성 (인쇄해서 벽/바닥에 붙일 마커)
- 0번, 1번, 2번 마커를 각각 PNG 파일로 저장
- 출력 후 A4 용지에 인쇄해서 사용 (마커 주변 흰 여백 충분히 남기기)
"""

import cv2

ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)

MARKER_IDS = {
    0: "marker_0_destination_A.png",   # 목적지 A 표시용
    1: "marker_1_destination_B.png",   # 목적지 B 표시용
    2: "marker_2_junction.png",        # 분기점 표시용
}

MARKER_SIZE_PX = 400  # 인쇄 크기에 맞게 조정 가능

for marker_id, filename in MARKER_IDS.items():
    img = cv2.aruco.generateImageMarker(ARUCO_DICT, marker_id, MARKER_SIZE_PX)
    cv2.imwrite(filename, img)
    print(f"저장됨: {filename} (마커 ID: {marker_id})")

print("\n인쇄 시 마커 주변에 흰 여백을 충분히 남겨주세요 (인식률에 중요).")
