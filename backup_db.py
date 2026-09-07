"""
PostgreSQL 백업 스크립트
- docker exec으로 컨테이너 안의 pg_dump 실행
- backups 폴더에 날짜/시간이 찍힌 .sql 파일로 저장
"""

import subprocess
import os
from datetime import datetime

CONTAINER_NAME = "mechdog-postgres"
DB_NAME = "mechdog"
DB_USER = "postgres"

os.makedirs("backups", exist_ok=True)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_file = f"backups/mechdog_backup_{timestamp}.sql"

print(f"백업 시작: {backup_file}")

# docker exec으로 컨테이너 안의 pg_dump 실행, 결과를 파일로 저장
with open(backup_file, "w", encoding="utf-8") as f:
    result = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "pg_dump", "-U", DB_USER, DB_NAME],
        stdout=f,
        stderr=subprocess.PIPE,
        text=True
    )

if result.returncode == 0:
    print(f"백업 완료: {backup_file}")
else:
    print("백업 실패!")
    print(result.stderr)
    