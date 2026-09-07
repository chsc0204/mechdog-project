"""
초기 시드 데이터: dataset 폴더 기준으로 registered_faces 테이블에 등록
(SQLite에 있던 등록 정보를 PostgreSQL로 다시 넣는 역할)
"""

import psycopg2
import os

conn = psycopg2.connect(
    host="localhost",
    port=5432,
    dbname="mechdog",
    user="postgres",
    password="mechdog1234"
)
cursor = conn.cursor()

dataset_dir = "dataset"

for folder_name in os.listdir(dataset_dir):
    folder_path = os.path.join(dataset_dir, folder_name)
    if not os.path.isdir(folder_path):
        continue

    # 폴더명 예: "1_chs" -> user_id="1", name="chs"
    user_id, name = folder_name.split("_", 1)

    cursor.execute("""
        INSERT INTO registered_faces (user_id, name, face_embedding, role)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id) DO NOTHING
    """, (user_id, name, folder_path, "작업자"))

    print(f"등록됨: user_id={user_id}, name={name}")

conn.commit()
cursor.close()
conn.close()

print("\n시드 데이터 삽입 완료!")