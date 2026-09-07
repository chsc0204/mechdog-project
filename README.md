# MechDog 프로젝트 - DB / 인프라 파트 (담당: 최현수)

## 개요
- 3번 파트(목적지 유도·에스코트) + DB 파트 겸직
- PostgreSQL + MQTT 브로커 + MQTT→DB 브릿지를 Docker Compose로 통합 관리
- 웹캠 기반 얼굴인식(LBPH) 및 Pass/Non-Pass 판정 로직 포함

## 폴더 구조
```
Mechdog/
├── dataset/                    # 등록된 얼굴 사진 (사람별 폴더)
├── backups/                    # DB 백업 .sql 파일
├── docker-compose.yml          # 전체 인프라 정의 (postgres, mqtt, bridge)
├── Dockerfile                  # bridge 서비스 이미지 빌드용
├── requirements.txt            # bridge 서비스 파이썬 패키지 목록
├── mqtt_db_bridge.py           # MQTT 메시지를 받아 DB에 저장하는 브릿지 서비스
├── pg_setup.py                 # PostgreSQL 테이블 생성 (SQLAlchemy)
├── seed_faces_pg.py            # dataset 폴더 기준 얼굴 등록 정보를 DB에 삽입
├── capture_faces.py            # 웹캠으로 얼굴 최초 등록 (30장 촬영)
├── capture_more_faces.py       # 기존 등록자에 사진 추가 촬영 (인식률 개선용)
├── train_faces.py              # LBPH 모델 학습 (trainer.yml 생성)
├── recognize_faces.py          # 실시간 얼굴인식 + Pass/Non-Pass + DB 로깅
├── backup_db.py                # PostgreSQL 백업 (.sql 파일 생성)
├── download_cascade.py         # 얼굴 검출용 haarcascade 파일 다운로드
├── mqtt_publisher_test2.py     # 브릿지 서비스 테스트용 메시지 전송
└── mqtt_subscriber_test.py     # MQTT 통신 자체 테스트용
```

## 최초 세팅 순서 (다른 컴퓨터/팀원이 처음 실행할 때)

1. Docker Desktop 설치 (WSL2 백엔드 필요)
2. haarcascade 파일 다운로드
   ```
   python download_cascade.py
   ```
   (C:\cv_data 폴더에 저장됨 - 한글 사용자 경로 문제 우회용)
3. Docker Compose로 인프라 실행
   ```
   docker compose up -d
   ```
4. DB 테이블 생성
   ```
   python pg_setup.py
   ```
5. 얼굴 등록 (본인 얼굴)
   ```
   python capture_faces.py
   ```
6. 인식률 개선을 위해 다양한 거리/각도로 추가 촬영 (권장)
   ```
   python capture_more_faces.py
   ```
7. 모델 학습
   ```
   python train_faces.py
   ```
8. DB에 등록 정보 반영
   ```
   python seed_faces_pg.py
   ```
9. 실시간 인식 테스트
   ```
   python recognize_faces.py
   ```

## 평소 사용 (인프라 이미 세팅된 경우)

- 인프라 켜기: `docker compose up -d`
- 인프라 끄기: `docker compose down` (데이터는 volume에 보존됨)
- 브릿지 로그 확인: `docker logs mechdog-bridge` (버퍼링으로 안 보일 수 있음 → `docker compose run --rm bridge`로 대체 확인)
- DB 백업: `python backup_db.py`

## DB 접속 정보
- Host: localhost (컨테이너 내부에서는 `postgres`)
- Port: 5432
- DB명: mechdog
- User: postgres
- Password: mechdog1234

## MQTT 연동 (팀 공용)
- Broker: localhost:1883 (컨테이너 내부에서는 `mosquitto`)
- 브릿지가 구독 중인 토픽 (팀원들과 실제 이름 재확인 필요):
  - `escort/status` (3번 파트)
  - `auth/result` (1번 파트)
  - `interaction/event` (2번 파트)
  - `security/alert` (4번 파트)
- 메시지 형식 (JSON):
  ```json
  {"robot_id": 3, "event_type": "도착완료", "detail": "회의실A 도착, 인사동작 재생"}
  ```

## 알려진 이슈 / 주의사항
- **Windows 한글 경로**: 사용자명에 한글이 있으면 OpenCV의 CascadeClassifier가 경로를 못 읽음 → haarcascade 파일을 `C:\cv_data`처럼 영문 경로에 별도 저장해서 사용 중
- **PostgreSQL 18+ 볼륨 경로**: `/var/lib/postgresql/data`가 아니라 `/var/lib/postgresql`에 마운트해야 함 (구버전과 다름)
- **PowerShell 리다이렉션(`<`) 미지원**: `.sql` 복원 시 `cmd /c "..."`로 감싸서 실행해야 인코딩도 안 깨지고 정상 작동
- **LBPH 인식 임계값(THRESHOLD)**: 조명/거리에 따라 민감하게 변함. `recognize_faces.py`의 `THRESHOLD` 값을 상황에 맞게 조정 필요 (현재 85 근처로 설정, 본인 확인 후 필요시 재조정)
- **docker logs 버퍼링**: `mqtt_db_bridge.py`가 정상 작동해도 `docker logs`에 출력이 안 보일 수 있음 → `docker compose run --rm bridge`로 실시간 확인 가능 (Dockerfile에 `PYTHONUNBUFFERED=1` 반영 완료)

## 다음 할 일
- 팀원들과 MQTT 토픽 이름 및 JSON 형식 최종 확정
- 데이터 다양성 확보를 위한 추가 얼굴 등록 (팀원 전체)
- 이미지/오디오 파일의 Docker Volume 관리 확장 (현재 bridge 서비스에 `./dataset` 읽기 전용 마운트 준비됨)
