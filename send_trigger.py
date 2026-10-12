"""
send_trigger.py - B파트 대신 목적지 확정(dialog.result) 신호를 보내 C 자동 출발을 시험
형식은 B->C 인계 계약(MECHDOG_B_TO_C_DIALOG_HANDOFF.md)과 동일한 공통 봉투

사용법 (escort_wifi.py 를 켜 둔 상태에서, 다른 cmd 창에서):
  python send_trigger.py outbound_dock            정상 -> 자동 출발
  python send_trigger.py outbound_dock --same     직전과 같은 세션 -> 중복이라 출발 안 해야 정상
  python send_trigger.py parking_lot              허용 안 된 목적지 -> 출발 안 하고 경고

우리 Docker 브로커(localhost:1883)로만 보냄 (팀 브로커의 이 토픽은 B만 발행 가능)
"""

import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

BROKER = "localhost"
PORT = 1883
TOPIC = "mechdog/v1/dialog/result"
LAST_SESSION_FILE = ".last_test_session"


def main():
    if len(sys.argv) < 2:
        print("사용법: python send_trigger.py <목적지 코드> [--same]")
        return
    dest = sys.argv[1]
    if "--same" in sys.argv:
        try:
            sid = open(LAST_SESSION_FILE, encoding="utf-8").read().strip()
        except FileNotFoundError:
            print("직전 세션 없음 - 먼저 --same 없이 한 번 보내세요")
            return
    else:
        sid = f"sess-test-{time.strftime('%Y%m%d%H%M%S')}"
        open(LAST_SESSION_FILE, "w", encoding="utf-8").write(sid)

    msg = {
        "ver": 1,
        "msg_id": uuid.uuid4().hex,
        "ts": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="milliseconds"),
        "src": "mechdog_b",
        "session_id": sid,
        "payload": {
            "msg_type": "dialog.result",
            "destination": dest,
            "purpose": "시험용 방문 목적",
            "confidence": 0.92,
            "retry_count": 0,
        },
    }
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    c.connect(BROKER, PORT, 60)
    c.loop_start()
    c.publish(TOPIC, json.dumps(msg, ensure_ascii=False), qos=1).wait_for_publish(2)
    c.loop_stop()
    c.disconnect()
    print(f"전송: {TOPIC} 세션={sid} 목적지={dest}")


if __name__ == "__main__":
    main()
