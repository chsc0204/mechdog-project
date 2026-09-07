import Hiwonder
import time
import Hiwonder_IIC
from HW_MechDog import MechDog

# MechDog 객체 초기화
mechdog = MechDog()

# IIC1 객체 생성 (초음파 센서와 통신할 채널)
i2c1 = Hiwonder_IIC.IIC(1)
# 발광 초음파 센서 객체 생성
i2csonar = Hiwonder_IIC.I2CSonar(i2c1)

# MechDog 기본 자세로 설정
mechdog.set_default_pose()
time.sleep(1)

distance = 0

def main():
  global distance

  while True:
    # 초음파 센서로 거리 측정 (단위: cm로 추정)
    distance = i2csonar.getDistance()
    print("현재 거리:", distance, "cm")

    if distance < 15:
      # 가까움 - 빨간색 표시 + 정지
      i2csonar.setRGB(0, 0xff, 0x00, 0x00)
      mechdog.move(0, 0)
      print(" -> 장애물 감지! 정지")
    elif distance > 40:
      # 멀음 - 파란색 표시 + 전진
      i2csonar.setRGB(0, 0x00, 0x00, 0x99)
      mechdog.move(50, 0)
    else:
      # 중간 거리 - 노란색 표시 + 천천히 전진
      i2csonar.setRGB(0, 0xfd, 0xd0, 0x00)
      mechdog.move(30, 0)

    time.sleep(0.2)

main()
