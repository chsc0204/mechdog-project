"""
MQTT -> DB 브릿지 서비스
- 팀 전체가 사용하는 이벤트 토픽들을 구독
- 들어오는 메시지를 그대로 PostgreSQL event_logs 테이블에 저장
- 상시 실행되는 서비스 (계속 켜둬야 함)

메시지 형식 (JSON 문자열):
{
  "robot_id": 3,
  "event_type": "도착완료",
  "detail": "회의실A 도착, 인사동작 재생"
}
"""

import paho.mqtt.client as mqtt
import psycopg2
import json

BROKER = "mosquitto"
PORT = 1883

# 팀 통합 Flow 문서(MD-TF-001) 기준 공식 토픽명 (2026-09-21 확정)
TOPICS = [
    "gate.session",     # A→B 세션 인계
    "vision.face",       # A 얼굴 판정
    "vision.ppe",         # A PPE 판정
    "dialog.result",      # B 목적지 확정 결과 → C 인계
    "escort.status",       # C 에스코트 상태 (idle/moving/arrived 등)
    "alert.event",          # 전체 파트 공용 경고 (A/B/C/D)
    "system.health",         # 전체 파트 공용 노드 상태
    "robot.command",          # D → 경고 로봇 자세/부저 명령 (수집기는 전 토픽 구독)
]

# ── PostgreSQL 연결 ──
conn = psycopg2.connect(
    host="postgres",
    port=5432,
    dbname="mechdog",
    user="postgres",
    password="mechdog1234"
)
cursor = conn.cursor()


def on_connect(client, userdata, flags, rc, properties=None):
    print(f"MQTT 브로커 연결됨 (코드: {rc})")
    for topic in TOPICS:
        client.subscribe(topic)
        print(f"구독 시작: {topic}")


def on_message(client, userdata, msg):
    payload_str = msg.payload.decode()
    print(f"[수신] {msg.topic} | {payload_str}")

    try:
        data = json.loads(payload_str)
    except json.JSONDecodeError:
        data = {}

    # alert.event -> 전용 테이블(alert_logs)로 라우팅
    if msg.topic == "alert.event":
        msg_id = data.get("msg_id")

        # [수정] 해제(resolved=true) 이벤트면 기존 행을 UPDATE, 아니면 신규 INSERT
        if data.get("resolved") is True and msg_id:
            cursor.execute("""
                UPDATE alert_logs
                SET resolved = true, resolved_at = NOW()
                WHERE msg_id = %s
            """, (msg_id,))
            conn.commit()
            print(f"  -> alert_logs 해제 처리 완료 (msg_id={msg_id})")
            return

        cursor.execute("""
            INSERT INTO alert_logs (msg_id, session_id, robot_id, level, reason, detail, snapshot_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (msg_id) DO NOTHING
        """, (
            msg_id,
            data.get("session_id"),
            data.get("src") or data.get("robot_id"),
            data.get("level", "WARNING"),
            data.get("reason", ""),
            data.get("detail", ""),
            data.get("snapshot_path")   # [추가 2026-09-28] C파트 경고 시 카메라 사진 경로 (없으면 NULL)
        ))
        conn.commit()
        print(f"  -> alert_logs 저장 완료 (reason={data.get('reason')})")
        return

    # system.health -> 전용 테이블(system_health)로 라우팅
    if msg.topic == "system.health":
        cursor.execute("""
            INSERT INTO system_health (node, status, detail)
            VALUES (%s, %s, %s)
        """, (
            data.get("node", ""),
            data.get("status", ""),
            data.get("detail", "")
        ))
        conn.commit()
        print(f"  -> system_health 저장 완료 (node={data.get('node')}, status={data.get('status')})")
        return

    # 나머지 토픽은 기존처럼 공용 event_logs에 저장
    robot_id = data.get("robot_id")
    event_type = data.get("event_type", msg.topic)
    detail = data.get("detail", payload_str if not data else "")

    cursor.execute("""
        INSERT INTO event_logs (robot_id, event_type, timestamp, detail)
        VALUES (%s, %s, NOW(), %s)
    """, (robot_id, event_type, detail))
    conn.commit()
    print(f"  -> event_logs 저장 완료 (event_type={event_type})")
    print(f"  -> DB 저장 완료 (event_type={event_type})")


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message

client.connect(BROKER, PORT, 60)

print("MQTT -> DB 브릿지 서비스 시작 (Ctrl+C로 종료)")
client.loop_forever()
