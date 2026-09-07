"""
MQTT 구독자(Subscriber) 테스트
- escort/status 토픽으로 오는 메시지를 계속 받아서 출력
"""

import paho.mqtt.client as mqtt

BROKER = "localhost"
PORT = 1883
TOPIC = "escort/status"


def on_connect(client, userdata, flags, rc, properties=None):
    print(f"브로커에 연결됨 (코드: {rc})")
    client.subscribe(TOPIC)
    print(f"'{TOPIC}' 토픽 구독 시작")


def on_message(client, userdata, msg):
    print(f"[수신] 토픽: {msg.topic} | 내용: {msg.payload.decode()}")


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message

client.connect(BROKER, PORT, 60)

print("메시지 대기 중... (Ctrl+C로 종료)")
client.loop_forever()