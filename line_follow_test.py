import Hiwonder
import time
import Hiwonder_IIC
from HW_MechDog import MechDog

# MechDog object init
mechdog = MechDog()

# IIC channel 1 (changed from 2 - testing alternate channel)
iic2 = Hiwonder_IIC.IIC(1)
# ESP32S3 camera object
cam = Hiwonder_IIC.ESP32S3Cam(iic2)

mechdog.set_default_pose()
time.sleep(2)

print("Line follow start (yellow line, 30 sec run)")

start_time = time.time()
RUN_SECONDS = 30

def main():
  while (time.time() - start_time) < RUN_SECONDS:
    center1 = cam.line_follow(cam.YELLOW)

    if center1[0] == 0 and center1[1] == 0:
      mechdog.move(0, 0)
      print("Line not found")
    elif center1[0] < 60:
      mechdog.move(50, 25)
      print("Turning left, pos:", center1)
    elif center1[0] > 100:
      mechdog.move(50, -25)
      print("Turning right, pos:", center1)
    else:
      mechdog.move(50, 0)
      print("Going straight, pos:", center1)

    time.sleep(0.1)

  mechdog.move(0, 0)
  print("30 sec elapsed - auto stop")

main()
