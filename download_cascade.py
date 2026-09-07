"""
haarcascade xml 파일을 GitHub에서 직접 다운로드
(최신 opencv-python 5.x부터 이 데이터 파일이 패키지에서 빠져서 별도로 받아야 함)
"""

import urllib.request
import os

url = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"

dest_dir = r"C:\cv_data"
os.makedirs(dest_dir, exist_ok=True)

dest = os.path.join(dest_dir, "haarcascade_frontalface_default.xml")

print("다운로드 중...")
urllib.request.urlretrieve(url, dest)
print(f"다운로드 완료: {dest}")