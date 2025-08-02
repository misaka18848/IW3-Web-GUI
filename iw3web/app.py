import os
import threading
import queue
import time
import re
import json
import requests
from flask import Flask, render_template, request, redirect, url_for, send_file, flash, jsonify, Response, abort
from werkzeug.utils import secure_filename
from config import Config
from converter import convert_file, manage_storage
from onedrive_client import one_drive_client
app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = 'your-secret-key-here'

# 支持的文件扩展名
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mkv'}

def allowed_file(filename):
    """检查文件扩展名是否被允许"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# 文件队列和线程锁
conversion_queue = queue.Queue()
status_lock = threading.Lock()
status_info = {
    'processing': False,
    'current_file': None,
    'current_status': '空闲',
    'uploaded_files': [],
    'converted_files': []
}

# 状态持久化文件
STATE_FILE = 'conversion_state.json'

def load_persistent_state():
    """从文件加载持久化状态"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
                return state
    except Exception as e:
        print(f"加载持久化状态失败: {e}")
    return None

def save_persistent_state(state):
    """保存持久化状态到文件"""
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存持久化状态失败: {e}")

def cleanup_temp_files():
    """清理临时文件"""
    # 清理上传文件夹中的临时文件
    if os.path.exists(Config.UPLOAD_FOLDER):
        for filename in os.listdir(Config.UPLOAD_FOLDER):
            if filename.startswith('_tmp_'):
                file_path = os.path.join(Config.UPLOAD_FOLDER, filename)
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        print(f"已删除临时上传文件: {filename}")
                except Exception as e:
                    print(f"删除临时文件失败 {filename}: {e}")
    
    # 清理转换文件夹中的临时文件
    if os.path.exists(Config.CONVERTED_FOLDER):
        for filename in os.listdir(Config.CONVERTED_FOLDER):
            if filename.startswith('_tmp_'):
                file_path = os.path.join(Config.CONVERTED_FOLDER, filename)
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        print(f"已删除临时转换文件: {filename}")
                except Exception as e:
                    print(f"删除临时文件失败 {filename}: {e}")

def restore_processing_queue():
    """恢复处理队列"""
    # 从持久化状态恢复
    state = load_persistent_state()
    if state and 'queue' in state:
        restored_count = 0
        for task_data in state['queue']:
            try:
                # 验证文件是否存在
                if os.path.exists(task_data['input_path']):
                    task = {
                        'input_path': task_data['input_path'],
                        'original_filename': task_data['original_filename'],
                        'stored_filename': task_data['stored_filename'],
                        'additional_args': task_data.get('additional_args', '')  # ✅ 确保获取 additional_args，提供默认值
                    }
                    conversion_queue.put(task)
                    restored_count += 1
                else:
                    print(f"跳过不存在的文件: {task_data['original_filename']}")
            except Exception as e:
                print(f"恢复任务失败: {e}")
        
        print(f"恢复了 {restored_count} 个待处理任务")
    
    # 同时检查上传文件夹中的文件
    if os.path.exists(Config.UPLOAD_FOLDER):
        for filename in os.listdir(Config.UPLOAD_FOLDER):
            file_path = os.path.join(Config.UPLOAD_FOLDER, filename)
            if os.path.isfile(file_path) and filename.endswith(('.mp4', '.avi', '.mkv')):
                # 尝试从文件名解析原始文件名
                original_filename = filename
                # 这里可以根据您的命名规则调整
                if '_' in filename and filename.startswith('upload_'):
                    parts = filename.split('_')
                    if len(parts) > 3:
                        ext = parts[-1]
                        original_filename = f"恢复文件_{int(time.time())}.{ext}"
                
                task = {
                    'input_path': file_path,
                    'original_filename': original_filename,
                    'stored_filename': filename,
                    'additional_args': ''  # ✅ 新发现的文件没有额外参数，设为空字符串
                }
                conversion_queue.put(task)
                print(f"发现并添加未处理的上传文件: {filename}")

def conversion_worker():
    """后台转换工作线程"""
    print(" conversion_worker 线程已启动，等待任务...")
    while True:
        task = None
        # 仅在锁内检查状态和获取任务
        with status_lock:
            if not status_info['processing'] and not conversion_queue.empty():
                try:
                    task = conversion_queue.get_nowait()
                    status_info['processing'] = True
                    status_info['current_file'] = task['original_filename']
                    status_info['current_status'] = '正在转换'
                    # 从上传列表移除
                    if task['original_filename'] in status_info['uploaded_files']:
                        status_info['uploaded_files'].remove(task['original_filename'])
                except queue.Empty:
                    pass  # 不可能，但安全起见

        # 在锁外处理任务
        if task is None:
            time.sleep(0.5)  # 避免 CPU 占满
            continue

        print(f" 开始处理任务: {task['original_filename']}")

        input_path = task['input_path']
        original_filename = task['original_filename']
        output_path = os.path.join(Config.CONVERTED_FOLDER, original_filename)
        additional_args = task['additional_args']

        success, message = convert_file(input_path, output_path, additional_args)

        # 更新状态
        with status_lock:
            if success:
                status_info['current_status'] = '转换完成'
                if original_filename not in status_info['converted_files']:
                    status_info['converted_files'].insert(0, original_filename)
                manage_storage()
            else:
                status_info['current_status'] = f'转换失败: {message}'
                if os.path.exists(input_path) and original_filename not in status_info['uploaded_files']:
                    status_info['uploaded_files'].append(original_filename)
            status_info['processing'] = False
            status_info['current_file'] = None

        conversion_queue.task_done()
        print(f" 任务完成: {original_filename}, 成功: {success}")

        # 保存状态
        save_persistent_state({
            'queue': [],
            'processing': status_info['processing'],
            'current_file': status_info['current_file'],
            'uploaded_files': status_info['uploaded_files'].copy(),
            'converted_files': status_info['converted_files'].copy()
        })
@app.route('/upload_direct', methods=['POST'])
def upload_direct():
    data = request.get_json()
    url = data.get('url')
    filename = data.get('filename')
    additional_args = data.get('additional_args', '')

    if not url or not filename:
        return jsonify({"error": "缺少 url 或 filename"}), 400

    # 校验 URL
    if not url.lower().startswith(('http://', 'https://')):
        return jsonify({"error": "URL 必须以 http:// 或 https:// 开头"}), 400

    # 防止路径穿越
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({"error": "文件名不合法"}), 400

    # ✅ 严格校验扩展名（后端二次验证）
    ext = filename.lower().split('.')[-1]
    if ext not in ['mp4', 'avi', 'mkv']:
        return jsonify({"error": f"不支持的文件格式: .{ext}，仅支持 .mp4, .avi, .mkv"}), 400

    # 构建下载路径...
    temp_download_path = os.path.join(Config.UPLOAD_FOLDER, f"direct_{os.getpid()}_{filename}")

    def download_and_enqueue():
        try:
            # 下载文件（流式下载，避免内存溢出）
            with requests.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(temp_download_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

            # 下载成功，加入转换队列
            task = {
                'input_path': temp_download_path,
                'original_filename': filename,          # ✅ 必须添加
                'additional_args': additional_args      # ✅ 保持一致
            }
            conversion_queue.put(task)
            print(f"[直链上传] 已加入队列: {filename}")

        except Exception as e:
            print(f"[直链上传] 下载失败 {url}: {str(e)}")
            # 可选：记录失败任务到数据库或日志
            if os.path.exists(temp_download_path):
                os.remove(temp_download_path)

    # 异步下载，不阻塞响应
    thread = threading.Thread(target=download_and_enqueue)
    thread.start()

    return jsonify({"message": "直链任务已接收，正在后台下载", "filename": filename}), 200
# --- ✅ 优化 1: 启用分块上传 ---
@app.route('/upload', methods=['POST'])
def upload_chunk():
    """
    处理分块上传。
    前端需要发送:
        - chunk: 文件块数据 (POST body)
        - filename: 原始文件名
        - chunk_index: 当前块的索引 (从0开始)
        - total_chunks: 总块数
        - session_id: (可选) 会话ID，首次上传时留空，服务端返回
    """
    if 'chunk' not in request.files:
        return jsonify({'error': '没有上传文件块'}), 400

    file = request.files['chunk']
    original_filename = request.form.get('filename')
    chunk_index_str = request.form.get('chunk_index')
    total_chunks_str = request.form.get('total_chunks')
    session_id = request.form.get('session_id')  # 客户端传入，首次为空

    if not all([original_filename, chunk_index_str, total_chunks_str]):
        return jsonify({'error': '缺少必要参数'}), 400

    try:
        chunk_index = int(chunk_index_str)
        total_chunks = int(total_chunks_str)
    except ValueError:
        return jsonify({'error': 'chunk_index 或 total_chunks 必须是整数'}), 400

    if not allowed_file(original_filename):
        return jsonify({'error': '不支持的文件格式。只允许 mp4, avi, mkv 格式。'}), 400

    # 如果没有 session_id，说明是第一个分块，生成新的
    if not session_id:
        session_id = f"{int(time.time())}_{os.urandom(4).hex()}"
        new_session = True
    else:
        new_session = False

    # 创建或使用已有临时目录
    temp_dir = os.path.join(Config.UPLOAD_FOLDER, f"_upload_{session_id}")
    os.makedirs(temp_dir, exist_ok=True)

    # 保存当前分块
    chunk_filename = f"chunk_{chunk_index:04d}"
    chunk_path = os.path.join(temp_dir, chunk_filename)
    file.save(chunk_path)  # 保存当前块

    # 检查是否所有块都已上传
    uploaded_chunks = len([f for f in os.listdir(temp_dir) if f.startswith('chunk_')])
    
    if uploaded_chunks == total_chunks:
        # 所有块都已上传，合并文件
        stored_filename = f"upload_{int(time.time() * 1000)}_{os.urandom(4).hex()}{os.path.splitext(original_filename)[1].lower()}"
        final_path = os.path.join(Config.UPLOAD_FOLDER, stored_filename)
        
        try:
            with open(final_path, 'wb') as final_file:
                for i in range(total_chunks):
                    chunk_file = os.path.join(temp_dir, f"chunk_{i:04d}")
                    if os.path.exists(chunk_file):
                        with open(chunk_file, 'rb') as cf:
                            final_file.write(cf.read())
                        os.remove(chunk_file) # 删除块文件
            # 合并成功后，删除临时目录
            os.rmdir(temp_dir)
        except Exception as e:
            return jsonify({'error': f'合并文件失败: {str(e)}'}), 500

        # 将任务添加到转换队列
        additional_args = request.form.get('additional_args', '')
        task = {
            'input_path': final_path,
            'original_filename': original_filename,
            'stored_filename': stored_filename,
            'additional_args': additional_args
        }
        conversion_queue.put(task)

        # 更新状态 (使用锁)
        with status_lock:
            if original_filename not in status_info['uploaded_files']:
                status_info['uploaded_files'].insert(0, original_filename)

        # 返回 session_id 和成功信息
        return jsonify({
            'message': '上传并合并完成，已加入转换队列',
            'filename': original_filename,
            'session_id': session_id  # 返回 session_id，便于前端知道是哪个上传
        }), 200
    else:
        # 告诉前端继续上传
        return jsonify({
            'message': f'块 {chunk_index + 1}/{total_chunks} 上传成功',
            'uploaded_chunks': uploaded_chunks,
            'total_chunks': total_chunks,
            'session_id': session_id  # ✅ 关键：返回 session_id，后续请求必须带上
        }), 200
# --- ✅ 优化 2: 启用分块/流式下载 ---
@app.route('/download/<path:filename>')
def download_converted(filename):
    """下载转换后的文件 - 根据配置决定来源"""
    
    safe_filename = os.path.basename(filename)  # 防止路径遍历
    
    if Config.USE_ONEDRIVE_STORAGE and one_drive_client:
        # ✅ 从 OneDrive 生成临时直链
        download_link = one_drive_client.create_download_link(safe_filename)
        if download_link:
            # 重定向到 OneDrive 的共享链接
            return redirect(download_link)
        else:
            abort(500, description="无法生成下载链接")
    else:
        # ✅ 本地模式：原有逻辑
        safe_path = os.path.abspath(os.path.join(Config.CONVERTED_FOLDER, safe_filename))
        converted_folder_abs = os.path.abspath(Config.CONVERTED_FOLDER)
        
        if not safe_path.startswith(converted_folder_abs):
            abort(403)
        
        if not os.path.exists(safe_path) or not os.path.isfile(safe_path):
            abort(404)
        
        return send_file(
            safe_path, 
            as_attachment=True, 
            download_name=safe_filename
        )
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # 这里不再处理传统的文件上传逻辑，而是专注于可能的状态更新或其他操作（如果有）
        flash('请使用分块上传功能上传文件')
        return redirect(url_for('index'))

    return render_template('index.html', 
                         status_info=status_info,
                         additional_args=request.form.get('additional_args', ''))

@app.route('/delete/uploaded/<path:filename>')
def delete_uploaded(filename):
    task_to_remove = None
    with status_lock:  # 确保线程安全
        for task in list(conversion_queue.queue):
            if task['original_filename'] == filename:
                task_to_remove = task
                break
    
    if task_to_remove:
        temp_queue = queue.Queue()
        while not conversion_queue.empty():
            item = conversion_queue.get()
            if item != task_to_remove:
                temp_queue.put(item)
        while not temp_queue.empty():
            conversion_queue.put(temp_queue.get())
        
        if os.path.exists(task_to_remove['input_path']):
            os.remove(task_to_remove['input_path'])
    
    with status_lock:
        if filename in status_info['uploaded_files']:
            status_info['uploaded_files'].remove(filename)
            
    return redirect(url_for('index'))

@app.route('/delete/converted/<path:filename>')
def delete_converted(filename):
    safe_filename = os.path.basename(filename) # 防止路径遍历攻击

    try:
        # ✅ 根据配置决定删除位置
        if Config.USE_ONEDRIVE_STORAGE and one_drive_client:
            # 删除 OneDrive 上的文件
            success = one_drive_client.delete_file(safe_filename)
            if success:
                print(f"[删除] 成功从 OneDrive 删除: {safe_filename}")
            else:
                print(f"[删除] 从 OneDrive 删除失败: {safe_filename}")
                # 可以选择向用户反馈删除失败
        else:
            # 删除本地文件
            file_path = os.path.join(Config.CONVERTED_FOLDER, safe_filename)
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"[删除] 成功删除本地文件: {file_path}")
            else:
                print(f"[删除] 本地文件不存在，跳过: {file_path}")

        # ✅ 无论哪种模式，都需要从状态信息中移除
        with status_lock:
            if safe_filename in status_info['converted_files']:
                status_info['converted_files'].remove(safe_filename)

    except Exception as e:
        print(f"[删除] 操作失败 {safe_filename}: {str(e)}")
        # 可以选择记录错误，但不中断流程

    return redirect(url_for('index'))
@app.route('/api/status', methods=['GET'])
def api_status():
    # 使用 .get() 防止键不存在时报错，提供默认值
    return jsonify({
        'current_status': status_info.get('current_status', 'idle'),
        'current_file': status_info.get('current_file', ''),
        'uploaded_files': status_info.get('uploaded_files', []),
        'converted_files': status_info.get('converted_files', [])
    })
if __name__ == '__main__':
    # 启动时清理临时文件
    print("正在清理临时文件...")
    cleanup_temp_files()
    
    # 恢复处理队列
    print("正在恢复处理队列...")
    restore_processing_queue()
    
    # 启动后台转换线程
    worker_thread = threading.Thread(target=conversion_worker, daemon=True)
    worker_thread.start()
    

    app.run(host='0.0.0.0',debug=True, threaded=True,port=8000)