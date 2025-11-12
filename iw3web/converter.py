import subprocess
import os
import threading
from config import Config
import time # 用于时间戳
import random
from onedrive_client import one_drive_client # 导入新客户端
from datetime import datetime, time as dt_time, timedelta
import main
# 创建线程锁，保护共享资源（文件系统 + 存储管理）
storage_lock = threading.RLock()  # 使用 RLock 允许同一线程重入
def get_seconds_until_resume():
    """
    如果当前在暂停时间段内，返回距离恢复运行时间还需多少秒。
    如果不在暂停时间段，返回 0。
    """
    now = datetime.now().time()
    start = Config.STOP_TIME_START
    end = Config.STOP_TIME_END

    if start < end:
        # 同一天内暂停（如 02:00 ~ 05:00）
        if start <= now < end:
            # 计算今天 end 时间距离 now 的秒数
            resume_dt = datetime.combine(datetime.today(), end)
            now_dt = datetime.now()
            delta = (resume_dt - now_dt).total_seconds()
            return max(Config.MIN_SLEEP, delta)  # 至少 sleep MIN_SLEEP 秒
        else:
            return 0
    else:
        # 跨天暂停（如 23:00 ~ 05:00）
        now_dt = datetime.now()
        if now >= start or now < end:
            # 当前在暂停期
            if now >= start:
                # 今天 23:00 之后，恢复时间是明天 05:00
                resume_dt = datetime.combine(datetime.today() + timedelta(days=1), end)
            else:
                # 凌晨 00:00 ~ 05:00，恢复时间就是今天 05:00
                resume_dt = datetime.combine(datetime.today(), end)
            delta = (resume_dt - now_dt).total_seconds()
            return max(Config.MIN_SLEEP, delta)
        else:
            return 0
def convert_file(input_path, output_path, additional_args=""):
    """使用指定脚本转换单个文件（线程安全）"""
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        cli_script = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'iw3-cli.bat'))
        if not os.path.isfile(cli_script):
            return False, f"转换脚本不存在: {cli_script}"

        cmd = [cli_script, '-i', input_path, '-o', output_path, "--yes"]
        if additional_args:
            cmd.extend(additional_args.split())

        # ✅ === 时间暂停逻辑 ===
        print(f"[时间检查] 检查是否在停止时间段 {Config.STOP_TIME_START} ~ {Config.STOP_TIME_END}")
        while True:
            wait_seconds = get_seconds_until_resume()  # 确保这个函数已定义
            if wait_seconds <= 0:
                break
            wait_timedelta = timedelta(seconds=wait_seconds)
            print(f"[等待中] 当前处于停止时间段，还需等待 {str(wait_timedelta)} 后恢复...")
            try:
                time.sleep(wait_seconds)
            except KeyboardInterrupt:
                print("\n\n⚠️ 用户中断等待，强制退出转换任务")
                return False, "用户中断等待"
        print("[时间检查] 当前时间已允许执行转换任务")

        print(f"[转换] 执行命令: {' '.join(cmd)}")

        # ✅ 修改：使用 PIPE 但通过异步线程读取，防止阻塞
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,      # ❌ 不能用 DEVNULL（否则无法读取）
            stderr=subprocess.STDOUT,    # 合并 stderr 到 stdout
            stdin=subprocess.DEVNULL,
            text=True,
            cwd=os.path.dirname(cli_script),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            bufsize=1  # 行缓冲
        )

        # ✅ 异步读取输出的函数
        def _forward_output(pipe):
            try:
                for line in iter(pipe.readline, ''):
                    print(line.strip())
                pipe.close()
            except (OSError, ValueError):
                pass  # 进程结束或管道关闭

        # ✅ 开启单独线程实时输出日志
        output_thread = threading.Thread(target=_forward_output, args=(process.stdout,), daemon=True)
        output_thread.start()

        # ✅ 记录 PID
        with main.conversion_pid_lock:
            main.current_conversion_pid = process.pid
            print(f"[转换] 已启动进程，PID: {process.pid}")

        # ✅ 等待进程结束（无需再 readline，已由线程处理）
        process.wait()

        # ✅ 转换结束后清除 PID
        with main.conversion_pid_lock:
            if main.current_conversion_pid == process.pid:
                main.current_conversion_pid = None

        if process.returncode != 0:
            error_msg = f"[转换失败] 文件: {input_path}, 错误码: {process.returncode}"
            print(error_msg)
            return False, error_msg
        if os.path.isfile(output_path):
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
                base_delay = 2
                max_wait = 600
                attempt = 1
                while True:
                    print(f"[上传] 尝试 {attempt}: {filename}")
                    try:
                        success, msg = one_drive_client.upload_file(output_path, filename)
                        if success:
                            print(f"[上传成功] {filename}")
                            break
                        else:
                            print(f"[上传失败] 第{attempt}次尝试失败: {msg}")
                    except Exception as e:
                        print(f"[上传异常] 第{attempt}次尝试发生异常: {str(e)}")

                    wait_time = base_delay * (2 ** (attempt - 1))
                    wait_time = min(wait_time + random.uniform(0, 1), max_wait)

                    print(f"等待 {wait_time:.2f} 秒后重试... (按 Ctrl+C 可中断)")
                    try:
                        time.sleep(wait_time)
                    except KeyboardInterrupt:
                        print(f"\n\n⚠️ 用户手动中断上传流程: {filename}")
                        return False, "用户中断上传"

                    attempt += 1

                # 上传成功后删除本地文件
                with storage_lock:
                    if os.path.isfile(output_path):
                        try:
                            os.remove(output_path)
                            print(f"[删除本地转换文件] {output_path}")
                        except Exception as e:
                            print(f"[警告] 删除本地转换文件失败 {output_path}: {e}")
            else:
                # 本地存储模式
                with storage_lock:
                    if os.path.isfile(input_path):
                        try:
                            os.remove(input_path)
                            print(f"[删除源文件] {input_path}")
                        except Exception as e:
                            print(f"[警告] 删除源文件失败 {input_path}: {e}")
                manage_storage()

            return True, "转换成功"
        else:
            return False, f"[转换失败] 文件: {input_path}"


    except Exception as e:
        # ✅ 出错时清理 PID
        try:
            with main.conversion_pid_lock:
                if 'process' in locals() and main.current_conversion_pid == process.pid:
                    main.current_conversion_pid = None
        except:
            pass
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