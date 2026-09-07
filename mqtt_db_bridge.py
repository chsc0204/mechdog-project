"""
MQTT -> DB 브릿지 서비스
- 팀 전체가 사용하는 이벤트 토픽들을 구독
- 들어오는 메시지를 그대로 PostgreSQL event_logs 테이블에 저장
- 상시 실행되는 서비스 (계속 켜둬야 함)
"""

import paho.mqtt.client as mqtt
import psycopg2
import json
import os

# 환경변수로 주소를 받되, 없으면 localhost 사용 (로컬 실행 대비)
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", 5432))

TOPICS = [
    "escort/status",
    "auth/result",
    "interaction/event",
    "security/alert",
]

conn = psycopg2.connect(
    host=DB_HOST,
    port=DB_PORT,
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
        robot_id = data.get("robot_id")
        event_type = data.get("event_type", msg.topic)
        detail = data.get("detail", "")
    except json.JSONDecodeError:
        robot_id = None
        event_type = msg.topic
        detail = payload_str

    cursor.execute("""
        INSERT INTO event_logs (robot_id, event_type, detail)
        VALUES (%s, %s, %s)
    """, (robot_id, event_type, detail))
    conn.commit()
    print(f"  -> DB 저장 완료 (event_type={event_type})")


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message

client.connect(MQTT_BROKER, MQTT_PORT, 60)

print("MQTT -> DB 브릿지 서비스 시작 (Ctrl+C로 종료)")
client.loop_forever()