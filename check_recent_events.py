"""
최근 이벤트 로그를 종류별로 집계해서 확인
"""

import psycopg2

conn = psycopg2.connect(
    host="localhost",
    dbname="mechdog",
    user="postgres",
    password="mechdog1234"
)
cur = conn.cursor()

cur.execute("""
    SELECT event_type, COUNT(*)
    FROM event_logs
    WHERE timestamp > NOW() - INTERVAL '30 minutes'
    GROUP BY event_type
    ORDER BY event_type
""")

print("최근 30분간 이벤트 집계:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}회")

conn.close()
