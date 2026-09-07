"""
브릿지 서비스 테스트용 - JSON 형식 이벤트 메시지 전송
"""

import paho.mqtt.client as mqtt
import json
import time

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect("localhost", 1883, 60)

message = {
    "robot_id": 3,
    "event_type": "도착완료",
    "detail": "회의실A 도착, 인사동작 재생"
}

client.publish("escort/status", json.dumps(message, ensure_ascii=False))
print(f"전송: {message}")

time.sleep(1)
client.disconnect()
