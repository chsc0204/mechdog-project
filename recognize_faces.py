"""
3단계: 실시간 얼굴 인식 + Pass/Non-Pass 판정
- trainer.yml(학습된 모델)로 실시간 웹캠 얼굴 비교
- 결과를 PostgreSQL auth_logs 테이블에 기록
- 'q' 키로 종료
"""

import cv2
import psycopg2

# ── PostgreSQL 연결 ──
conn = psycopg2.connect(
    host="localhost",
    port=5432,
    dbname="mechdog",
    user="postgres",
    password="mechdog1234"
)
cursor = conn.cursor()

# ── 등록된 사용자 이름 매핑 가져오기 (user_id -> name) ──
cursor.execute("SELECT user_id, name FROM registered_faces")
id_to_name = {row[0]: row[1] for row in cursor.fetchall()}

# ── 학습된 모델 불러오기 ──
recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.read("trainer.yml")

# ── 얼굴 검출기 ──
face_cascade = cv2.CascadeClassifier(
    r"C:\cv_data\haarcascade_frontalface_default.xml"
)

# ── 판정 기준 (숫자가 작을수록 더 비슷함, LBPH 특성) ──
THRESHOLD = 75  # 이 값보다 크면 "모르는 사람"으로 판정 (필요시 조정)

cap = cv2.VideoCapture(0)
print("실시간 인식을 시작합니다. 'q'를 누르면 종료합니다.")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        face_img = gray[y:y+h, x:x+w]
        face_img = cv2.resize(face_img, (200, 200))

        label, confidence = recognizer.predict(face_img)
        # confidence: 작을수록 더 비슷함 (거리 개념)

        if confidence < THRESHOLD:
            name = id_to_name.get(str(label), "Unknown")
            result = "Pass"
            color = (0, 255, 0)  # 초록
            matched_id = str(label)
        else:
            name = "인가되지 않음"
            result = "Non-Pass"
            color = (0, 0, 255)  # 빨강
            matched_id = None

        # 화면에 표시
        cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)
        text = f"{name} ({result}) {confidence:.0f}"
        cv2.putText(frame, text, (x, y-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        # DB에 로그 기록
        cursor.execute("""
            INSERT INTO auth_logs (matched_user_id, distance_score, result)
            VALUES (%s, %s, %s)
        """, (matched_id, float(confidence), result))
        conn.commit()

    cv2.imshow("Face Recognition", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
cursor.close()
conn.close()

print("종료되었습니다. auth_logs 테이블에 기록이 저장되었습니다.")