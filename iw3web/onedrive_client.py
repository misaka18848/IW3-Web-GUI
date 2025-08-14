# onedrive_client.py

import json
import os
import msal
import requests
from config import Config
from threading import RLock
import time

class OneDriveClient:
    def __init__(self):
        self.token_lock = RLock()
        self.access_token = None
        self.token_expires_at = 0
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'IW3WebGUI/1.0'})

    def _get_token_from_cache(self):
        """从本地文件加载Token"""
        try:
            if os.path.exists(Config.TOKEN_PATH):
                with open(Config.TOKEN_PATH, 'r') as f:
                    token_data = json.load(f)
                    return token_data
        except Exception as e:
            print(f"[OneDrive] 读取Token失败: {e}")
        return None

    def _save_token_to_cache(self, token_data):
        """将Token保存到本地文件"""
        try:
            with open(Config.TOKEN_PATH, 'w') as f:
                json.dump(token_data, f)
        except Exception as e:
            print(f"[OneDrive] 保存Token失败: {e}")

    def _acquire_token(self):
        """获取访问令牌 (使用客户端凭据流，适合后台服务)"""
        app = msal.ConfidentialClientApplication(
            client_id=Config.ONEDRIVE_CLIENT_ID,
            client_credential=Config.ONEDRIVE_CLIENT_SECRET,
            authority=f"https://login.microsoftonline.com/{Config.ONEDRIVE_TENANT_ID}"
        )
        
        # ✅ 修复：使用正确的资源标识符，不是 v1.0 端点
        scope = ["https://graph.microsoft.com/.default"]  # .default 表示应用注册的所有权限
        
        result = app.acquire_token_for_client(scopes=scope)
        
        if "access_token" in result:
            with self.token_lock:
                self.access_token = result["access_token"]
                self.token_expires_at = time.time() + result.get("expires_in", 3599)
            self._save_token_to_cache(result)
            print("[OneDrive] 成功获取访问令牌")
            return True
        else:
            print(f"[OneDrive] 获取令牌失败: {result.get('error')}, {result.get('error_description')}")
            return False

    def _ensure_valid_token(self):
        """确保拥有有效的访问令牌"""
        with self.token_lock:
            now = time.time()
            # 如果没有令牌，或令牌即将在10秒内过期，则刷新
            if not self.access_token or now >= (self.token_expires_at - 10):
                # 尝试从缓存加载
                token_data = self._get_token_from_cache()
                if token_data and now < token_data.get("expires_at", 0) - 10:
                    self.access_token = token_data["access_token"]
                    self.token_expires_at = token_data["expires_at"]
                    return True
                # 否则重新获取
                return self._acquire_token()
            return True

    def _make_request(self, method, url, **kwargs):
        """封装HTTP请求，自动处理认证"""
        if not self._ensure_valid_token():
            raise Exception("无法获取有效的OneDrive访问令牌")

        headers = kwargs.pop('headers', {})
        headers['Authorization'] = f'Bearer {self.access_token}'
        headers['Content-Type'] = 'application/json'
        kwargs['headers'] = headers

        response = self.session.request(method, url, **kwargs)
        if response.status_code == 401: # Unauthorized, 可能是Token过期
            # 尝试强制刷新一次
            if self._acquire_token() and self._ensure_valid_token():
                headers['Authorization'] = f'Bearer {self.access_token}'
                kwargs['headers'] = headers
                response = self.session.request(method, url, **kwargs)
        return response

    def get_folder_id_by_path(self, path):
        """根据路径获取文件夹ID (例如: '/IW3Converted')"""
        # 构建基于用户ID的完整路径
        base_path = f"/users/{Config.ONEDRIVE_USER_ID}/drive"
        
        # 根目录
        if path == '/' or path == '':
            # 查询根目录本身
            full_path = f"{base_path}/root"
        else:
            # 使用冒号语法访问相对路径
            full_path = f"{base_path}/root:{path}"
        
        url = f"{Config.GRAPH_API_BASE_URL}{full_path}"
        print(f"[OneDrive] 正在查询路径: {url}")  # 调试输出
        response = self._make_request("GET", url)
        
        if response.status_code == 200:
            data = response.json()
            folder_id = data.get('id')
            print(f"[OneDrive] 找到文件夹ID: {folder_id}")
            return folder_id
        elif response.status_code == 404:
            print(f"[OneDrive] 文件夹未找到: {path}")
            # 可选：打印当前根目录内容，帮助调试
            self.list_root_contents()
            return None
        else:
            print(f"[OneDrive] 获取文件夹ID失败: {response.status_code}, {response.text}")
            return None
    def upload_file(self, local_file_path, target_filename, folder_path=Config.ONEDRIVE_FOLDER_PATH):
        """
        将本地文件上传到 OneDrive 指定文件夹。
        自动根据文件大小选择简单上传（≤4MB）或分段上传（>4MB）。
        
        :param local_file_path: 本地文件的完整路径
        :param target_filename: 上传到 OneDrive 后的文件名
        :param folder_path: OneDrive 上的目标文件夹路径（例如: '/IW3Converted'）
        :return: (success: bool, message: str)
        """
        # 获取目标文件夹 ID
        folder_id = self.get_folder_id_by_path(folder_path)
        if not folder_id:
            error_msg = f"[OneDrive] 无法找到目标文件夹: {folder_path}"
            print(error_msg)
            return False, error_msg

        # 获取本地文件大小
        try:
            file_size = os.path.getsize(local_file_path)
        except OSError as e:
            error_msg = f"[OneDrive] 无法获取文件大小: {local_file_path}, 错误: {str(e)}"
            print(error_msg)
            return False, error_msg

        # 构建上传 URL 的公共前缀
        base_url = f"{Config.GRAPH_API_BASE_URL}/users/{Config.ONEDRIVE_USER_ID}/drive/items/{folder_id}:/{target_filename}"

        # --- 策略 1: 简单上传 (适用于 ≤ 4MB 的文件) ---
        if file_size <= 4 * 1024 * 1024:  # 4MB in bytes
            upload_url = f"{base_url}:/content"
            print(f"[OneDrive] 使用简单上传 ({file_size} bytes)")
            
            try:
                with open(local_file_path, 'rb') as f:
                    response = self._make_request("PUT", upload_url, data=f)
                
                if response.status_code in (200, 201):
                    print(f"[OneDrive] 简单上传成功: {target_filename}")
                    return True, "上传成功"
                else:
                    error_msg = f"[OneDrive] 简单上传失败: {response.status_code}, {response.text}"
                    print(error_msg)
                    return False, error_msg
                    
            except Exception as e:
                error_msg = f"[OneDrive] 简单上传时发生异常: {str(e)}"
                print(error_msg)
                return False, error_msg

        # --- 策略 2: 分段上传 (适用于 > 4MB 的文件) ---
        else:
            print(f"[OneDrive] 文件较大 ({file_size} bytes)，使用分段上传")
            
            # 1. 创建上传会话
            session_url = f"{base_url}:/createUploadSession"
            payload = {
                "item": {
                    "@microsoft.graph.conflictBehavior": "rename"  # 可选: rename, replace, fail
                }
            }
            
            response = self._make_request("POST", session_url, json=payload)
            if response.status_code != 200:
                error_msg = f"[OneDrive] 创建上传会话失败: {response.status_code}, {response.text}"
                print(error_msg)
                return False, error_msg

            session_data = response.json()
            upload_url = session_data.get('uploadUrl')
            if not upload_url:
                error_msg = "[OneDrive] 创建上传会话成功，但未返回 uploadUrl"
                print(error_msg)
                return False, error_msg

            expiration = session_data.get('expirationDateTime', 'Unknown')
            print(f"[OneDrive] 上传会话已创建，过期时间: {expiration}")

            # 2. 分块上传
            chunk_size = min(10 * 1024 * 1024, file_size)  # 每块最大 10MB，或文件总大小
            headers = {'Content-Type': 'application/octet-stream'}
            uploaded_bytes = 0

            try:
                with open(local_file_path, 'rb') as f:
                    while uploaded_bytes < file_size:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break

                        chunk_end = uploaded_bytes + len(chunk) - 1
                        content_range = f"bytes {uploaded_bytes}-{chunk_end}/{file_size}"
                        headers['Content-Range'] = content_range

                        # 发送当前块
                        chunk_response = self._make_request(
                            "PUT", 
                            upload_url, 
                            data=chunk, 
                            headers=headers,
                            timeout=600  # 为大块上传设置较长超时
                        )

                        if chunk_response.status_code == 202:
                            # 继续上传
                            uploaded_bytes = chunk_end + 1
                            print(f"[OneDrive] 已上传: {uploaded_bytes}/{file_size} ({uploaded_bytes/file_size*100:.1f}%)")
                        elif chunk_response.status_code in (200, 201):
                            # 上传完成
                            print(f"[OneDrive] 分段上传成功: {target_filename}")
                            return True, "上传成功"
                        else:
                            error_msg = f"[OneDrive] 分块上传失败: {chunk_response.status_code}, {chunk_response.text}"
                            print(error_msg)
                            return False, error_msg

                # 如果循环结束但未收到 200/201，说明出错
                error_msg = "[OneDrive] 分段上传未完成，未知错误"
                print(error_msg)
                return False, error_msg

            except Exception as e:
                error_msg = f"[OneDrive] 分段上传过程中发生异常: {str(e)}"
                print(error_msg)
                return False, error_msg

            finally:
                # 可选：这里可以添加逻辑来清理 uploadUrl（Graph API 通常会在 24 小时后自动清理）
                pass
    def delete_file(self, filename, folder_path=Config.ONEDRIVE_FOLDER_PATH):
        """删除 OneDrive 指定文件夹中的文件"""
        folder_id = self.get_folder_id_by_path(folder_path)
        if not folder_id:
            print(f"[OneDrive] 无法找到目标文件夹的ID: {folder_path}")
            return False

        # 构建删除文件的 URL
        # 使用 /users/{Config.ONEDRIVE_USER_ID}/drive/items/{parent-id}:/{filename} 这种寻址方式
        file_url = f"{Config.GRAPH_API_BASE_URL}/users/{Config.ONEDRIVE_USER_ID}/drive/items/{folder_id}:/{filename}"
        response = self._make_request("DELETE", file_url)
        
        if response.status_code == 204:
            # 204 No Content 表示删除成功
            print(f"[OneDrive] 成功删除文件: {filename}")
            return True
        elif response.status_code == 404:
            # 404 Not Found, 文件可能已被删除，视为成功
            print(f"[OneDrive] 文件未找到 (可能已删除): {filename}")
            return True
        else:
            print(f"[OneDrive] 删除文件失败: {response.status_code}, {response.text}")
            return False
    def create_download_link(self, filename, folder_path=Config.ONEDRIVE_FOLDER_PATH):
        """为OneDrive中的文件创建临时共享链接 (允许下载)"""
        folder_id = self.get_folder_id_by_path(folder_path)
        if not folder_id:
            return None

        # 首先获取文件的 item_id
        file_url = f"{Config.GRAPH_API_BASE_URL}/users/{Config.ONEDRIVE_USER_ID}/drive/items/{folder_id}:/{filename}"
        response = self._make_request("GET", file_url)
        if response.status_code != 200:
            print(f"[OneDrive] 无法找到文件获取ID: {filename}, {response.text}")
            return None

        item_data = response.json()
        item_id = item_data.get('id')
        if not item_id:
            return None

        # 尝试获取 @microsoft.graph.downloadUrl
        download_url = item_data.get('@microsoft.graph.downloadUrl')
        if download_url:
            print(f"[OneDrive] 直接下载链接: {download_url}")
            return download_url

        # 如果没有下载链接，则创建共享链接
        share_url = f"{Config.GRAPH_API_BASE_URL}/users/{Config.ONEDRIVE_USER_ID}/drive/items/{item_id}/createLink"
        payload = {
            "type": "view",  # 或者 "edit" 根据需求
            "scope": "anonymous"  # 或者 "organization"
        }
        response = self._make_request("POST", share_url, json=payload)

        if response.status_code == 201:
            link_data = response.json()
            web_url = link_data.get('link', {}).get('webUrl')
            if web_url:
                # 添加 ?download=1 参数以尝试直接下载
                download_link = f"{web_url}?download=1"
                print(f"[OneDrive] 创建的下载链接: {download_link}")
                return download_link
        else:
            print(f"[OneDrive] 创建共享链接失败: {response.status_code}, {response.text}")
            return None
    def list_files_in_folder(self, folder_path=Config.ONEDRIVE_FOLDER_PATH):
        """列出指定文件夹中的所有文件及其大小和修改时间"""
        folder_id = self.get_folder_id_by_path(folder_path)
        if not folder_id:
            return []

        # ✅ 修复：加上 'file' 字段！
        url = f"{Config.GRAPH_API_BASE_URL}/users/{Config.ONEDRIVE_USER_ID}/drive/items/{folder_id}/children?$select=name,size,lastModifiedDateTime,file"
        files = []
        while url:
            response = self._make_request("GET", url)
            if response.status_code != 200:
                print(f"[OneDrive] 列出文件失败: {response.status_code}, {response.text}")
                break

            data = response.json()
            for item in data.get('value', []):
                if item.get('file'):  # 现在能正确识别文件了
                    files.append({
                        'name': item['name'],
                        'size': item['size'],
                        'lastModifiedDateTime': item['lastModifiedDateTime']
                    })
            url = data.get('@odata.nextLink')
        return files

# 全局实例 (确保在 app.py 中初始化)
one_drive_client = None
if Config.USE_ONEDRIVE_STORAGE:
    one_drive_client = OneDriveClient()