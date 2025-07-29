import os

# 基本配置
class Config:
    UPLOAD_FOLDER = r'C:\TOOL\nunif-windows\iw3web\uploads'
    CONVERTED_FOLDER = r'C:\TOOL\nunif-windows\iw3web\converted'
    MAX_CONTENT_LENGTH = 1024 * 1024 * 1024  # 1GB最大文件大小
    MAX_STORAGE_SIZE = 20 * 1024 * 1024 * 1024  # 20GB最大存储空间

# 确保目录存在
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(Config.CONVERTED_FOLDER, exist_ok=True)