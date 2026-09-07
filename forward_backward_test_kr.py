import Hiwonder
import time
from HW_MechDog import MechDog

# MechDog 객체 초기화
mechdog = MechDog()

# 메인 함수
def main():
  # 딜레이 함수, 인자는 딜레이 시간 (단위: 초)
  time.sleep(2)
  # move() 함수
  # 인자1: 보폭 (단위: mm) (양수=전진, 음수=후진)
  # 인자2: 회전각도 (단위: 도) (양수=좌회전, 음수=우회전)
  mechdog.move(80, 0)
  time.sleep(5)
  mechdog.move(0, 0)
  time.sleep(2)
  mechdog.move(-50, 0)
  time.sleep(5)
  # 정지
  mechdog.move(0, 0)
  time.sleep(2)

# 메인 함수 실행
main()
