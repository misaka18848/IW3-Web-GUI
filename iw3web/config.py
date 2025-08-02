import os

# 基本配置
class Config:
    #上传文件存储目录
    UPLOAD_FOLDER = r'C:\TOOL\nunif-windows\iw3web\uploads'
    # 转换文件存储目录
    CONVERTED_FOLDER = r'C:\TOOL\nunif-windows\iw3web\converted'
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024 * 1024  # 当前设置1GB单最大文件大小
    MAX_STORAGE_SIZE = 20 * 1024 * 1024 * 1024  # 当前设置20GB最大存储空间
    # 是否启用 OneDrive 存储模式
    USE_ONEDRIVE_STORAGE = False  # 默认关闭，安全起见

    # Microsoft Graph API 相关
    # 必须通过 Azure AD 注册应用获取
    ONEDRIVE_CLIENT_ID = '注册应用id' 
    ONEDRIVE_USER_ID = '使用的用户id'
    ONEDRIVE_CLIENT_SECRET = '应用密钥'
    ONEDRIVE_TENANT_ID = '组织id'  
    ONEDRIVE_REDIRECT_URI = '' # 用于获取Token，当前版本用不到，不用填，先加着

    # OneDrive 上用于存放转换后文件的文件夹路径 (相对于根目录)
    ONEDRIVE_FOLDER_PATH = '/IW3Converted'  # 也可以是 '/Shared Documents/IW3Converted' 等

    # Microsoft Graph API 的基础 URL
    GRAPH_API_BASE_URL = 'https://graph.microsoft.com/v1.0'

    # Token 存储路径 (用于持久化刷新Token)
    TOKEN_PATH = os.path.join(os.path.dirname(__file__), 'onedrive_token.json')

# 确保目录存在
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(Config.CONVERTED_FOLDER, exist_ok=True)