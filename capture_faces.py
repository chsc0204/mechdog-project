"""
1단계: 웹캠으로 얼굴 사진 촬영 + DB 등록
- 얼굴을 30장 정도 찍어서 dataset 폴더에 저장
- registered_faces 테이블에 사용자 정보 등록
"""

import cv2
import os
import sqlite3

# ── 사용자 정보 입력받기 ──
name = input("등록할 이름을 입력하세요: ")
role = input("역할을 입력하세요 (작업자/방문자): ")

# ── DB에서 다음 번호(label) 정하기 ──
conn = sqlite3.connect("mechdog.db")
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM registered_faces")
count = cursor.fetchone()[0]
user_id = str(count + 1)  # 1, 2, 3... 순서로 번호 부여

# ── 사진 저장할 폴더 만들기 ──
save_dir = f"dataset/{user_id}_{name}"
os.makedirs(save_dir, exist_ok=True)

# ── 얼굴 검출기 준비 (OpenCV 내장 haar cascade) ──
face_cascade = cv2.CascadeClassifier(
    r"C:\cv_data\haarcascade_frontalface_default.xml"
)

# ── 웹캠 켜기 ──
cap = cv2.VideoCapture(0)
count = 0
target = 30  # 몇 장 찍을지

print("웹캠을 바라봐주세요. 's' 키를 누르면 촬영 시작, 'q' 키로 종료합니다.")

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
            cv2.imwrite(f"{save_dir}/{count}.jpg", face_img)

    cv2.putText(frame, f"Captured: {count}/{target}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    cv2.imshow("Face Capture", frame)

    key = cv2.waitKey(1) & 0xFF
    if key == ord('s'):
        capturing = True
    if key == ord('q') or count >= target:
        break

cap.release()
cv2.destroyAllWindows()

# ── DB에 등록 ──
cursor.execute("""
INSERT INTO registered_faces (user_id, name, face_embedding, role)
VALUES (?, ?, ?, ?)
""", (user_id, name, save_dir, role))
conn.commit()
conn.close()

print(f"\n{name}님 얼굴 {count}장 촬영 및 DB 등록 완료! (user_id={user_id})")