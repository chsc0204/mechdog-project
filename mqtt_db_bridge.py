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

# 팀 전체가 쓰기로 한 토픽 이름들 (예시, 실제로는 팀원들과 맞춰서 확정)
TOPICS = [
    "escort/status",      # 3번 파트 (에스코트)
    "auth/result",        # 1번 파트 (출입게이트)
    "interaction/event",  # 2번 파트 (인터렉션)
    "security/alert",     # 4번 파트 (보안관제)
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
        # JSON 형식으로 온 경우 파싱
        data = json.loads(payload_str)
        robot_id = data.get("robot_id")
        event_type = data.get("event_type", msg.topic)  # 없으면 토픽명 사용
        detail = data.get("detail", "")
    except json.JSONDecodeError:
        # JSON이 아니라 그냥 텍스트로 온 경우 (테스트 메시지 등)
        robot_id = None
        event_type = msg.topic
        detail = payload_str

    cursor.execute("""
        INSERT INTO event_logs (robot_id, event_type, timestamp, detail)
        VALUES (%s, %s, NOW(), %s)
    """, (robot_id, event_type, detail))
    conn.commit()
    print(f"  -> DB 저장 완료 (event_type={event_type})")


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message

client.connect(BROKER, PORT, 60)

print("MQTT -> DB 브릿지 서비스 시작 (Ctrl+C로 종료)")
client.loop_forever()
