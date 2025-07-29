import os
import threading
import queue
import time
import re
import json
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify
from werkzeug.utils import secure_filename
from config import Config
from converter import convert_file, manage_storage

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
    while True:
        try:
            # 使用锁确保状态检查的原子性
            with status_lock:
                if not status_info['processing'] and not conversion_queue.empty():
                    task = conversion_queue.get()
                    # 设置处理状态
                    status_info['processing'] = True
                    status_info['current_file'] = task['original_filename']
                    status_info['current_status'] = '正在转换'
                    
                    # 从上传列表中移除
                    if task['original_filename'] in status_info['uploaded_files']:
                        status_info['uploaded_files'].remove(task['original_filename'])
                else:
                    task = None
            
            # 如果没有任务，等待1秒
            if task is None:
                time.sleep(1)
                continue
            
            # 在锁外执行耗时的转换操作
            input_path = task['input_path']
            original_filename = task['original_filename']
            output_path = os.path.join(Config.CONVERTED_FOLDER, original_filename)
            additional_args = task['additional_args']
            
            # 执行转换
            success, message = convert_file(input_path, output_path, additional_args)
            
            # 转换完成后，使用锁更新状态
            with status_lock:
                if success:
                    status_info['current_status'] = '转换完成'
                    if original_filename not in status_info['converted_files']:
                        status_info['converted_files'].insert(0, original_filename)
                    manage_storage()
                else:
                    status_info['current_status'] = f'转换失败: {message}'
                    # 如果失败，可以考虑重新加入队列或移回上传列表
                    if os.path.exists(input_path):
                        if original_filename not in status_info['uploaded_files']:
                            status_info['uploaded_files'].append(original_filename)
                
                # 重置处理状态
                status_info['processing'] = False
                status_info['current_file'] = None
            
            conversion_queue.task_done()
            
            # 保存当前状态
            with status_lock:
                queue_list = []
                temp_queue = queue.Queue()
                while not conversion_queue.empty():
                    item = conversion_queue.get()
                    # ✅ 将 additional_args 添加到保存的数据结构中
                    queue_list.append({
                        'input_path': item['input_path'],
                        'original_filename': item['original_filename'],
                        'stored_filename': item['stored_filename'],
                        'additional_args': item['additional_args']  # ✅ 关键：保存额外参数
                    })
                    temp_queue.put(item)
                # 恢复队列
                while not temp_queue.empty():
                    conversion_queue.put(temp_queue.get())
                
                current_state = {
                    'queue': queue_list,  # ✅ queue_list 现在包含 additional_args
                    'processing': status_info['processing'],
                    'current_file': status_info['current_file'],
                    'uploaded_files': status_info['uploaded_files'].copy(),
                    'converted_files': status_info['converted_files'].copy()
                }
                save_persistent_state(current_state)
                
        except Exception as e:
            # 出现异常时也要释放锁和状态
            with status_lock:
                status_info['processing'] = False
                status_info['current_file'] = None
                status_info['current_status'] = f'处理异常: {str(e)}'
            print(f"转换工作线程异常: {e}")
            time.sleep(1)  # 避免快速重试

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('没有选择文件')
            return redirect(request.url)
        
        file = request.files['file']
        additional_args = request.form.get('additional_args', '')
        
        if file.filename == '':
            flash('没有选择文件')
            return redirect(request.url)
        
        if file:
            original_filename = file.filename
            
            if not allowed_file(original_filename):
                flash('不支持的文件格式。只允许 mp4, avi, mkv 格式。')
                return redirect(request.url)
            
            timestamp = int(time.time() * 1000)
            random_suffix = os.urandom(4).hex()
            file_ext = os.path.splitext(original_filename)[1].lower()
            stored_filename = f"upload_{timestamp}_{random_suffix}{file_ext}"
            
            input_path = os.path.join(Config.UPLOAD_FOLDER, stored_filename)
            file.save(input_path)
            
            # 使用锁安全地更新状态
            with status_lock:
                if original_filename not in status_info['uploaded_files']:
                    status_info['uploaded_files'].append(original_filename)
            
            task = {
                'input_path': input_path,
                'original_filename': original_filename,
                'stored_filename': stored_filename,
                'additional_args': additional_args
            }
            
            conversion_queue.put(task)
            return redirect(url_for('index'))
    
    return render_template('index.html', 
                         status_info=status_info,
                         additional_args=request.form.get('additional_args', ''))

@app.route('/download/<path:filename>')
def download_converted(filename):
    decoded_filename = filename
    return send_from_directory(
        Config.CONVERTED_FOLDER, decoded_filename, as_attachment=True)

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
    file_path = os.path.join(Config.CONVERTED_FOLDER, filename)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        with status_lock:
            if filename in status_info['converted_files']:
                status_info['converted_files'].remove(filename)
    except Exception as e:
        print(f"删除转换文件失败: {str(e)}")
    return redirect(url_for('index'))
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
    
    # 禁用Flask多线程，使用单线程模式
    app.run(debug=True, threaded=False,port=8000)