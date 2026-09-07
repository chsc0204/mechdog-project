"""
2단계: dataset 폴더의 얼굴 사진들로 LBPH 모델 학습
- dataset 폴더 안의 모든 사람 사진을 모아서 학습
- 결과물: trainer.yml (학습된 모델 파일)
"""

import cv2
import os
import numpy as np

dataset_dir = "dataset"

face_samples = []  # 얼굴 이미지들
labels = []         # 각 이미지가 누구 것인지 (숫자 라벨)

# dataset 폴더 안의 각 사람 폴더를 순회
for folder_name in os.listdir(dataset_dir):
    folder_path = os.path.join(dataset_dir, folder_name)
    if not os.path.isdir(folder_path):
        continue

    # 폴더명 예: "1_chs" -> user_id = "1"
    user_id = folder_name.split("_")[0]
    label = int(user_id)

    for img_name in os.listdir(folder_path):
        img_path = os.path.join(folder_path, img_name)
        img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        face_samples.append(img)
        labels.append(label)

print(f"총 {len(face_samples)}장의 사진, {len(set(labels))}명의 사람으로 학습합니다.")

# LBPH 모델 생성 및 학습
recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.train(face_samples, np.array(labels))

# 학습된 모델 저장
recognizer.save("trainer.yml")
print("학습 완료! trainer.yml 파일로 저장되었습니다.")