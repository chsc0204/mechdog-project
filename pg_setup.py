"""
PostgreSQL + SQLAlchemy 버전 - DB 초기 설정
Docker로 띄운 postgres 컨테이너에 테이블을 생성합니다.
"""

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float
from sqlalchemy.orm import declarative_base
from datetime import datetime

# ── DB 연결 정보 ──
# 형식: postgresql://사용자이름:비밀번호@주소:포트/DB이름
DATABASE_URL = "postgresql://postgres:mechdog1234@localhost:5432/mechdog"

engine = create_engine(DATABASE_URL)
Base = declarative_base()


# ── 1. 얼굴 등록 테이블 ──
class RegisteredFace(Base):
    __tablename__ = "registered_faces"

    user_id = Column(String, primary_key=True)
    name = Column(String)
    face_embedding = Column(Text)   # 얼굴 데이터 경로 또는 특징값
    role = Column(String)           # '작업자' 또는 '방문자'
    registered_at = Column(DateTime, default=datetime.utcnow)


# ── 2. 얼굴 인증 로그 테이블 ──
class AuthLog(Base):
    __tablename__ = "auth_logs"

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    matched_user_id = Column(String)
    distance_score = Column(Float)
    result = Column(String)         # 'Pass' 또는 'Non-Pass'
    timestamp = Column(DateTime, default=datetime.utcnow)


# ── 3. 에스코트(3번 파트) 로그 테이블 ──
class EscortLog(Base):
    __tablename__ = "escort_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    robot_id = Column(Integer)
    destination = Column(String)
    start_time = Column(DateTime)
    arrival_time = Column(DateTime)
    success = Column(Integer)       # 1: 성공, 0: 실패
    motion_played = Column(String)


# ── 4. 팀 공용 이벤트 로그 테이블 ──
class EventLog(Base):
    __tablename__ = "event_logs"

    event_id = Column(Integer, primary_key=True, autoincrement=True)
    robot_id = Column(Integer)
    event_type = Column(String)     # '인증됨', '목적지수신', '도착완료', '예외발생' 등
    timestamp = Column(DateTime, default=datetime.utcnow)
    detail = Column(Text)


# ── 실제 테이블 생성 ──
Base.metadata.create_all(engine)

print("PostgreSQL에 테이블 생성 완료!")
print("생성된 테이블: registered_faces, auth_logs, escort_logs, event_logs")