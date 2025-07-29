import subprocess
import os
from config import Config

def convert_file(input_path, output_path, additional_args=""):
    """使用指定脚本转换单个文件"""
    try:
        # 确保输出目录存在
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # 执行转换命令 - 输出为单个文件
        cmd = ['..\iw3-cli.bat', '-i', input_path, '-o', output_path, "--yes"]
        
        if additional_args:
            cmd.extend(additional_args.split())
            
        print(f"执行转换命令: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # 删除源文件
        if os.path.isfile(input_path):
            os.remove(input_path)
            
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        error_msg = f"转换失败: {e.stderr or e.stdout or str(e)}"
        print(error_msg)
        return False, error_msg
    except Exception as e:
        error_msg = f"转换异常: {str(e)}"
        print(error_msg)
        return False, error_msg

def manage_storage():
    """管理存储空间，当超过20GB时删除旧文件"""
    files = []
    total_size = 0
    
    # 获取所有转换完成的文件
    for filename in os.listdir(Config.CONVERTED_FOLDER):
        file_path = os.path.join(Config.CONVERTED_FOLDER, filename)
        if os.path.isfile(file_path):
            try:
                file_stat = os.stat(file_path)
                files.append((file_path, file_stat.st_mtime, file_stat.st_size))
                total_size += file_stat.st_size
            except FileNotFoundError:
                continue
    
    # 如果超过存储限制，删除最老的文件
    if total_size > Config.MAX_STORAGE_SIZE:
        files.sort(key=lambda x: x[1])  # 按修改时间排序
        while total_size > Config.MAX_STORAGE_SIZE and files:
            file_path, _, size = files.pop(0)
            try:
                os.remove(file_path)
                total_size -= size
                print(f"存储空间超限，删除旧文件: {file_path}")
            except Exception as e:
                print(f"删除文件失败: {str(e)}")
                continue