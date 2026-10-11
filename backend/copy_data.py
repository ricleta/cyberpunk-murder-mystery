import shutil
import os

src = '../frontend/data'
dst = 'data'
if os.path.exists(dst):
    shutil.rmtree(dst)
shutil.copytree(src, dst)
print("Copied files successfully.")
