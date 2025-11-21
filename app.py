import os
import time
import uuid
import logging
import queue
import zipfile
import tempfile
import shutil
import glob
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from flask import Flask, request, jsonify, send_file, render_template, Response
from flask_socketio import SocketIO
import yt_dlp

import db

# --- Configuration & Setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DOWNLOAD_DIR = os.path.join(DATA_DIR, "downloads")
ARCHIVE_FILE = os.path.join(DATA_DIR, "archive.txt")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

db.init_db()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-fallback-change-me')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- Utilities ---

def get_disk_usage():
    """Returns free space in GB."""
    total, used, free = shutil.disk_usage(DOWNLOAD_DIR)
    return {
        "total": f"{total // (2**30)} GB",
        "free": f"{free // (2**30)} GB",
        "percent": round((used / total) * 100, 1)
    }

# --- Worker Logic ---

def process_download_task(job_data, urls):
    job_id = job_data['id']
    logger.info(f"Starting execution for Job {job_id}")
    
    db.update_job(job_id, {"status": "running", "progress_text": "Initializing..."})
    socketio.emit('job_update', db.get_job(job_id))

    config = db.get_config()
    
    # Enhanced yt-dlp Configuration
    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(uploader)s', '%(upload_date)s_%(title)s [%(id)s].%(ext)s'),
        'download_archive': ARCHIVE_FILE,
        'ignoreerrors': True,
        'quiet': True,
        'no_warnings': True,
        # KEY IMPROVEMENT: Force file metadata to match upload date
        'updatetime': True, 
        'writethumbnail': True,
        'addmetadata': True,
        # Anti-blocking: Spoof a modern browser
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'logger': logger,
        'progress_hooks': [lambda d: socketio.emit('file_progress', {
            'job_id': job_id,
            'filename': os.path.basename(d.get('filename', 'unknown')),
            'percent': d.get('_percent_str', '0%').replace('%',''),
            'speed': d.get('_speed_str', 'N/A'),
            'eta': d.get('_eta_str', 'N/A')
        }) if d['status'] == 'downloading' else None],
    }

    if config['extract_audio']:
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}],
        })
    else:
        ydl_opts['format'] = config.get('video_quality', 'best')

    completed = 0
    failed = 0
    total = len(urls)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for index, url in enumerate(urls):
                if db.get_job(job_id)['status'] == 'cancelled':
                    logger.info(f"Job {job_id} cancelled.")
                    return

                db.update_job(job_id, {
                    "progress_text": f"Processing {index + 1}/{total}",
                    "progress": int((index / total) * 100)
                })
                socketio.emit('job_update', db.get_job(job_id))

                try:
                    # Use extract_info to ensure we get metadata for post-processing logic if needed
                    info = ydl.extract_info(url, download=True)
                    if info:
                        completed += 1
                except Exception as e:
                    logger.error(f"Error downloading {url}: {e}")
                    failed += 1

        final_status = "completed" if failed == 0 else "completed_with_errors"
        db.update_job(job_id, {"status": final_status, "progress": 100, "progress_text": f"Done: {completed} ok, {failed} error"})
        socketio.emit('job_update', db.get_job(job_id))

    except Exception as e:
        logger.error(f"Critical failure in job {job_id}: {e}")
        db.update_job(job_id, {"status": "failed", "error": str(e)})
        socketio.emit('job_update', db.get_job(job_id))

class WorkerManager:
    def __init__(self):
        self.executor = None
        self.reload_workers()

    def reload_workers(self):
        config = db.get_config()
        max_workers = int(config.get('max_concurrent_downloads', 3))
        if self.executor: self.executor.shutdown(wait=False)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit_job(self, job_id, urls):
        job = db.get_job(job_id)
        if job: self.executor.submit(process_download_task, job, urls)

manager = WorkerManager()

# --- Routes ---

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/status')
def system_status():
    """Returns disk usage and worker status."""
    return jsonify(get_disk_usage())

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    if request.method == 'POST':
        db.save_config(request.json)
        manager.reload_workers()
        return jsonify({"status": "success"})
    return jsonify(db.get_config())

@app.route('/api/jobs', methods=['GET', 'POST'])
def handle_jobs():
    if request.method == 'GET': return jsonify(db.get_jobs())
    
    if 'file' not in request.files: return jsonify({"error": "No file uploaded"}), 400
    file = request.files['file']
    content = file.read().decode('utf-8', errors='ignore')
    urls = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith('#')]
    
    if not urls: return jsonify({"error": "File contains no valid URLs"}), 400

    job_id = str(uuid.uuid4())
    db.create_job(job_id, file.filename, urls)
    manager.submit_job(job_id, urls)
    
    new_job = db.get_job(job_id)
    socketio.emit('new_job', new_job)
    return jsonify({"status": "success", "job": new_job})

@app.route('/api/jobs/<job_id>/cancel', methods=['POST'])
def cancel_job(job_id):
    db.update_job(job_id, {"status": "cancelled", "progress_text": "Cancelling..."})
    socketio.emit('job_update', db.get_job(job_id))
    return jsonify({"status": "success"})

@app.route('/api/jobs/<job_id>', methods=['DELETE'])
def delete_job(job_id):
    db.delete_job(job_id)
    socketio.emit('job_deleted', {'job_id': job_id})
    return jsonify({"status": "success"})

@app.route('/api/downloads')
def list_downloads():
    files = []
    # Optimized walker with extension filtering
    valid_exts = {'.mp4', '.mp3', '.mkv', '.webm', '.jpg', '.webp'}
    
    for root, _, filenames in os.walk(DOWNLOAD_DIR):
        for f in filenames:
            name, ext = os.path.splitext(f)
            if ext.lower() in valid_exts:
                full_path = os.path.join(root, f)
                try:
                    stat = os.stat(full_path)
                    files.append({
                        "path": os.path.relpath(full_path, DOWNLOAD_DIR),
                        "name": f,
                        "size": stat.st_size,
                        "modified": stat.st_mtime, # This will now reflect Upload Date thanks to yt-dlp config
                        "folder": os.path.basename(root)
                    })
                except OSError: pass # File might vanish during walk
                
    return jsonify(sorted(files, key=lambda x: x['modified'], reverse=True))

@app.route('/api/download_all')
def download_all_zip():
    def generate():
        temp_handle, temp_path = tempfile.mkstemp(suffix='.zip')
        os.close(temp_handle)
        try:
            with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(DOWNLOAD_DIR):
                    for file in files:
                        if not file.startswith('.'):
                            abs_path = os.path.join(root, file)
                            rel_path = os.path.relpath(abs_path, DOWNLOAD_DIR)
                            zf.write(abs_path, rel_path)
            
            with open(temp_path, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk: break
                    yield chunk
        finally:
            if os.path.exists(temp_path): os.remove(temp_path)

    return Response(generate(), mimetype='application/zip', 
                   headers={'Content-Disposition': f'attachment; filename=ytbatch_archive_{int(time.time())}.zip'})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    print(f"Starting YTBatch Pro on http://localhost:{port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
