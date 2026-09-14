"""
터치센서 테스트 (USB로 Hiwonder Python Editor에서 "Download and Run"으로 실행)
- 여러 포트 번호를 시도해서, 어느 포트에 터치센서가 연결됐는지 확인
- 눌렀을 때 터미널에 "눌림!" 출력
"""

import Hiwonder
import time

PORT_TO_TEST = 3 # 안 되면 1, 3, 4 등으로 바꿔가며 시도

button = Hiwonder.Button(PORT_TO_TEST)

pressed_count = 0

def on_button_clicked():
    global pressed_count
    pressed_count += 1
    print(f"눌림! (포트 {PORT_TO_TEST}, 총 {pressed_count}번째)")

button.Clicked(on_button_clicked)

print(f"포트 {PORT_TO_TEST}번 터치센서 테스트 시작. 눌러보세요! (Ctrl+C로 종료)")

while True:
    time.sleep(0.2)

