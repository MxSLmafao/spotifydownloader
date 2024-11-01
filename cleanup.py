import os
import shutil
from config import DOWNLOAD_DIR

# Remove all files in the downloads directory
if os.path.exists(DOWNLOAD_DIR):
    shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR)
print("Downloads folder cleared successfully!")
