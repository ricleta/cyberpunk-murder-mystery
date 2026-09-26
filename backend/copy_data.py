import shutil
import os

src = '../frontend/data'
dst = 'data'
os.makedirs(dst, exist_ok=True)

for filename in os.listdir(src):
    if filename.endswith('.json'):
        shutil.copy(os.path.join(src, filename), os.path.join(dst, filename))
print("Copied files successfully.")
