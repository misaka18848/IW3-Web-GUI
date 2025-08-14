import subprocess
import os
import threading
from config import Config
import time # 用于时间戳
import random
from onedrive_client import one_drive_client # 导入新客户端

# 创建线程锁，保护共享资源（文件系统 + 存储管理）
storage_lock = threading.RLock()  # 使用 RLock 允许同一线程重入

def convert_file(input_path, output_path, additional_args=""):
    """使用指定脚本转换单个文件（线程安全）"""
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        cli_script = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'iw3-cli.bat'))
        if not os.path.isfile(cli_script):
            return False, f"转换脚本不存在: {cli_script}"

        cmd = [cli_script, '-i', input_path, '-o', output_path, "--yes", "--video-codec", "libx265"]
        if additional_args:
            cmd.extend(additional_args.split())

        print(f"[转换] 执行命令: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            cwd=os.path.dirname(cli_script)
        )

        if result.returncode != 0:
            error_msg = f"[转换失败] 文件: {input_path}, 错误: {result.stderr.strip() or result.stdout.strip()}"
            print(error_msg)
            return False, error_msg

        # ✅ 转换成功后，根据配置决定存储位置
        filename = os.path.basename(output_path)
        
        if Config.USE_ONEDRIVE_STORAGE and one_drive_client:
            with storage_lock:
                if os.path.isfile(input_path):
                    try:
                        os.remove(input_path)
                        print(f"[删除源文件] {input_path}")
                    except Exception as e:
                        print(f"[警告] 删除源文件失败 {input_path}: {e}")

            # === 上传到 OneDrive（无限重试） ===
            base_delay = 2   # 初始延迟2秒
            max_wait = 600  # 最大等待时间：10分钟（避免等待太久）

            attempt = 1
            while True:
                print(f"[上传] 尝试 {attempt}: {filename}")
                try:
                    success, msg = one_drive_client.upload_file(output_path, filename)
                    if success:
                        print(f"[上传成功] {filename}")
                        break  # ✅ 成功则退出循环
                    else:
                        print(f"[上传失败] 第{attempt}次尝试失败: {msg}")
                except Exception as e:
                    print(f"[上传异常] 第{attempt}次尝试发生异常: {str(e)}")

                # 计算等待时间：指数退避 + 随机抖动
                wait_time = base_delay * (2 ** (attempt - 1))
                wait_time = wait_time + random.uniform(0, 1)  # 加上 0~1 秒抖动
                wait_time = min(wait_time, max_wait)         # 限制最大等待时间

                print(f"等待 {wait_time:.2f} 秒后重试... (按 Ctrl+C 可中断)")
                try:
                    time.sleep(wait_time)
                except KeyboardInterrupt:
                    print(f"\n\n⚠️ 用户手动中断上传流程: {filename}")
                    # 可根据需要决定是否返回失败、保留文件等
                    error_msg = "用户中断上传"
                    return False, error_msg

                attempt += 1  # 尝试次数递增

            # 上传成功后，删除本地转换后的文件
            with storage_lock:
                if os.path.isfile(output_path):
                    try:
                        os.remove(output_path)
                        print(f"[删除本地转换文件] {output_path}")
                    except Exception as e:
                        print(f"[警告] 删除本地转换文件失败 {output_path}: {e}")

        else:
            # 本地存储模式，保留原有逻辑
            with storage_lock:
                if os.path.isfile(input_path):
                    try:
                        os.remove(input_path)
                        print(f"[删除源文件] {input_path}")
                    except Exception as e:
                        print(f"[警告] 删除源文件失败 {input_path}: {e}")

            # 本地模式下，调用存储管理
            manage_storage()

        return True, "转换并上传成功" if Config.USE_ONEDRIVE_STORAGE else result.stdout.strip()

    except Exception as e:
        error_msg = f"[转换异常] {str(e)}"
        print(error_msg)
        return False, error_msg


def manage_storage():
    """
    管理存储空间，当超过20GB时删除最旧的已转换文件
    此函数是线程安全的，使用 storage_lock 保护
    """
    with storage_lock:
        try:
            total_size = 0
            files_to_delete = [] # 存储 (name, size, timestamp) 用于删除

            if Config.USE_ONEDRIVE_STORAGE and one_drive_client:
                # ✅ OneDrive 模式：统计 OneDrive 上的文件
                files = one_drive_client.list_files_in_folder()
                # 将时间字符串转换为时间戳以便排序
                import datetime
                for file in files:
                    try:
                        dt = datetime.datetime.fromisoformat(file['lastModifiedDateTime'].replace('Z', '+00:00'))
                        files_to_delete.append((file['name'], file['size'], dt.timestamp()))
                        total_size += file['size']
                    except Exception as e:
                        print(f"[存储管理] 解析时间失败 {file['name']}: {e}")
                        continue

                # 按时间戳排序 (最旧的在前)
                files_to_delete.sort(key=lambda x: x[2])

                print(f"[存储管理] OneDrive 总大小: {total_size / (1024**3):.2f}GB")
                while total_size > Config.MAX_STORAGE_SIZE and files_to_delete:
                    filename, size, _ = files_to_delete.pop(0)
                    # 调用 OneDrive 客户端删除
                    if one_drive_client.delete_file(filename):
                        total_size -= size
                        print(f"[存储管理] 已删除 OneDrive 旧文件: {filename}")
                    else:
                        print(f"[存储管理] 删除 OneDrive 文件失败: {filename}")
                        continue

            else:
                # ✅ 本地模式：原有逻辑 (保持不变)
                # 遍历已转换文件夹
                if os.path.exists(Config.CONVERTED_FOLDER):
                    for root, dirs, files in os.walk(Config.CONVERTED_FOLDER):
                        for file in files:
                            filepath = os.path.join(root, file)
                            if os.path.isfile(filepath):
                                try:
                                    file_size = os.path.getsize(filepath)
                                    # 获取文件的修改时间 (时间戳)
                                    file_mtime = os.path.getmtime(filepath)
                                    files_to_delete.append((filepath, file_size, file_mtime))
                                    total_size += file_size
                                except Exception as e:
                                    print(f"[存储管理] 读取文件信息失败 {filepath}: {e}")
                                    continue

                # 按修改时间排序 (最旧的在前)
                files_to_delete.sort(key=lambda x: x[2])

                print(f"[存储管理] 本地总大小: {total_size / (1024**3):.2f}GB")
                while total_size > Config.MAX_STORAGE_SIZE and files_to_delete:
                    filepath, size, _ = files_to_delete.pop(0)
                    try:
                        os.remove(filepath)
                        total_size -= size
                        print(f"[存储管理] 已删除本地旧文件: {filepath}")
                    except Exception as e:
                        print(f"[存储管理] 删除本地文件失败 {filepath}: {e}")
                        continue

        except Exception as e:
            print(f"[存储管理] 发生异常: {e}")