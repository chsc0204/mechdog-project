"""
깃 클린 필터: 커밋할 때 WiFi 이름/비밀번호를 자리표시자로 바꿔서 저장
(내 컴퓨터의 파일은 그대로, GitHub에 올라가는 내용만 바뀜)

설정은 README 또는 아래 명령 참고 (한 번만):
  git config filter.wifisecret.clean "python wifi_clean_filter.py"
  git config filter.wifisecret.smudge cat
  .gitattributes 에:  main_wifi.py filter=wifisecret
"""
import re
import sys

data = sys.stdin.buffer.read().decode("utf-8")
data = re.sub(r'^(\s*WIFI_NAME\s*=\s*)".*?"', r'\1"YOUR_WIFI_NAME"', data, flags=re.M)
data = re.sub(r'^(\s*WIFI_PASSWORD\s*=\s*)".*?"', r'\1"YOUR_WIFI_PASSWORD"', data, flags=re.M)
sys.stdout.buffer.write(data.encode("utf-8"))
