import Hiwonder
import time
from HW_MechDog import MechDog

# MechDog 객체 초기화
mechdog = MechDog()

# MechDog 기본 자세로 설정
mechdog.set_default_pose()
time.sleep(2)

def main():
  print("인사 동작을 실행합니다...")
  # 미리 정의된 동작 실행: 허리 숙여 인사
  mechdog.action_run("scrape_a_bow")
  time.sleep(4)
  print("동작 완료!")

  # 기본 자세로 복귀
  mechdog.set_default_pose()
  time.sleep(1)

main()
