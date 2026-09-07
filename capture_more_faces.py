"""
기존 등록된 사람의 dataset 폴더에 사진을 추가로 촬영
- 다양한 거리/각도로 찍어야 인식률이 좋아짐
- 기존 파일과 번호 안 겹치게 이어서 저장
"""

import cv2
import os

# ── 기존 user_id, name 입력 ──
user_id = input("기존 user_id를 입력하세요 (예: 1): ")
name = input("이름을 입력하세요 (기존과 동일하게, 예: chs): ")

save_dir = f"dataset/{user_id}_{name}"

if not os.path.exists(save_dir):
    print("해당 폴더가 없습니다. 먼저 capture_faces.py로 최초 등록을 해주세요.")
    exit()

# ── 기존 사진 개수 확인해서 이어서 번호 매기기 ──
existing_files = [f for f in os.listdir(save_dir) if f.endswith(".jpg")]
start_count = len(existing_files)

face_cascade = cv2.CascadeClassifier(
    r"C:\cv_data\haarcascade_frontalface_default.xml"
)

cap = cv2.VideoCapture(0)
count = 0
target = 50  # 이번엔 50장 추가 (다양한 거리/각도로)

print(f"현재 {start_count}장 있음. 50장 추가 촬영합니다.")
print("가까이/중간/멀리, 좌우로 고개 돌리며 다양하게 찍어주세요.")
print("'s' 키로 촬영 시작, 'q' 키로 종료합니다.")

capturing = False

while True:
    ret, frame = cap.read()
    if not ret:
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)

        if capturing and count < target:
            face_img = gray[y:y+h, x:x+w]
            face_img = cv2.resize(face_img, (200, 200))
            count += 1
            file_num = start_count + count
            cv2.imwrite(f"{save_dir}/{file_num}.jpg", face_img)

    cv2.putText(frame, f"Captured: {count}/{target}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    cv2.imshow("Additional Face Capture", frame)

    key = cv2.waitKey(1) & 0xFF
    if key == ord('s'):
        capturing = True
    if key == ord('q') or count >= target:
        break

cap.release()
cv2.destroyAllWindows()

print(f"\n추가 촬영 완료! 총 {start_count + count}장 (기존 {start_count} + 추가 {count})")
print("이제 train_faces.py를 다시 실행해서 재학습하세요.")