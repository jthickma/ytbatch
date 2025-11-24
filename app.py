import os
import time
import uuid
import logging
import shutil
import zipfile
import tempfile
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from flask import Flask, request, jsonify, send_file, render_template, after_this_request
from flask_socketio import SocketIO
import yt_dlp
import gallery_dl

# --- Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.environ.get('TEMP_DIR', os.path.join(BASE_DIR, "temp_data"))
MAX_WORKERS = int(os.environ.get('MAX_WORKERS', 4))
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key')

# Ensure temp dir exists
if os.path.exists(TEMP_DIR):
    shutil.rmtree(TEMP_DIR)
os.makedirs(TEMP_DIR, exist_ok=True)

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
try:
    logger.info(f"Loaded gallery-dl version: {gallery_dl.__version__}")
except AttributeError:
    logger.info("Loaded gallery-dl (version unknown)")

# --- App Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- In-Memory State ---
JOBS = {}
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# --- Worker Logic ---

def run_gallery_dl(url, output_dir):
    """
    Runs gallery-dl via subprocess.
    
    NOTE: We use subprocess instead of the Python API because gallery-dl's 
    configuration system is global and not thread-safe. Running it in a 
    separate process ensures that concurrent downloads do not interfere 
    with each other's configuration (e.g. destination directory).
    """
    try:
        # gallery-dl downloads to current directory by default or specified via config/args
        # We use -d to specify destination
        cmd = [
            "gallery-dl",
            "--directory", output_dir,
            "--no-mtime", # Use current time or preserve? User wanted metadata. 
            # gallery-dl usually preserves mtime by default or has options. 
            # Let's stick to defaults but ensure destination.
            url
        ]
        
        logger.info(f"Attempting gallery-dl for {url}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            return True, "Downloaded with gallery-dl"
        else:
            return False, f"gallery-dl failed: {result.stderr}"
            
    except Exception as e:
        return False, str(e)

def process_job(job_id, urls, options=None):
    job_dir = os.path.join(TEMP_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    
    options = options or {}
    force_gallery = options.get('force_gallery', False)

    JOBS[job_id]['status'] = 'running'
    JOBS[job_id]['progress_text'] = 'Starting downloads...'
    socketio.emit('job_update', JOBS[job_id])

    completed = 0
    failed = 0
    total = len(urls)
    
    # yt-dlp options
    ydl_opts = {
        'outtmpl': os.path.join(job_dir, '%(title)s [%(id)s].%(ext)s'),
        'ignoreerrors': True, # We handle errors manually to try fallback
        'quiet': True,
        'no_warnings': True,
        'writethumbnail': True,
        'addmetadata': True,
        'updatetime': True, # Preserve upload date
        # 'logger': logger, # Custom logger might be too verbose, we'll log manually
    }

    for index, url in enumerate(urls):
        if JOBS[job_id].get('status') == 'cancelled':
            break

        JOBS[job_id]['progress'] = int((index / total) * 100)
        JOBS[job_id]['progress_text'] = f"Processing {index + 1}/{total}: {url}"
        socketio.emit('job_update', JOBS[job_id])

        success = False
        error_msg = ""

        # Determine strategy
        use_gallery_dl = force_gallery
        
        if not use_gallery_dl:
            # 1. Try yt-dlp
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # extract_info with download=True throws exception on error if ignoreerrors=False
                    # But we set ignoreerrors=True, so it might return None or partial info.
                    # To detect failure for fallback, we might want ignoreerrors=False for the call, 
                    # or check the result.
                    
                    # Let's use a separate options dict for the check or just try/except with default opts
                    # Actually, if we want fallback, we should let it raise exception.
                    opts_strict = ydl_opts.copy()
                    opts_strict['ignoreerrors'] = False
                    
                    with yt_dlp.YoutubeDL(opts_strict) as ydl_strict:
                        ydl_strict.extract_info(url, download=True)
                    
                    success = True
                    logger.info(f"yt-dlp success for {url}")

            except Exception as e_yt:
                logger.warning(f"yt-dlp failed for {url}: {e_yt}. Trying gallery-dl...")
                use_gallery_dl = True
                error_msg = f"yt-dlp error: {str(e_yt)}"

        if use_gallery_dl:
            # 2. Fallback or Force gallery-dl
            g_success, g_msg = run_gallery_dl(url, job_dir)
            if g_success:
                success = True
                logger.info(f"gallery-dl success for {url}")
            else:
                error_msg += f"; gallery-dl error: {g_msg}"
                logger.error(f"All downloads failed for {url}. {error_msg}")

        if success:
            completed += 1
        else:
            failed += 1
            JOBS[job_id]['errors'].append({'url': url, 'error': error_msg})

    # Create Zip
    JOBS[job_id]['progress_text'] = "Zipping files..."
    socketio.emit('job_update', JOBS[job_id])
    
    zip_path = os.path.join(TEMP_DIR, f"{job_id}.zip")
    has_files = False
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(job_dir):
            for file in files:
                has_files = True
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, job_dir)
                zf.write(abs_path, rel_path)
    
    # Cleanup raw files immediately to save space? 
    # Or keep them until zip is downloaded? 
    # Let's keep zip, remove raw files.
    shutil.rmtree(job_dir)

    if not has_files:
        JOBS[job_id]['status'] = 'failed'
        JOBS[job_id]['error'] = "No files were downloaded."
    else:
        JOBS[job_id]['status'] = 'completed'
        JOBS[job_id]['zip_path'] = zip_path
        JOBS[job_id]['progress'] = 100
        JOBS[job_id]['progress_text'] = f"Done. {completed} succeeded, {failed} failed."

    socketio.emit('job_update', JOBS[job_id])


# --- Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/jobs', methods=['POST'])
def create_job():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    content = file.read().decode('utf-8', errors='ignore')
    urls = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith('#')]
    
    if not urls:
        return jsonify({"error": "No valid URLs found"}), 400

    job_id = str(uuid.uuid4())
    
    # Parse options from form data
    options = {
        'force_gallery': request.form.get('force_gallery') == 'true'
    }

    JOBS[job_id] = {
        'id': job_id,
        'status': 'queued',
        'progress': 0,
        'progress_text': 'Queued',
        'created_at': time.time(),
        'filename': file.filename,
        'url_count': len(urls),
        'errors': []
    }
    
    executor.submit(process_job, job_id, urls, options)
    
    socketio.emit('new_job', JOBS[job_id])
    return jsonify(JOBS[job_id])

@app.route('/api/jobs/<job_id>', methods=['GET'])
def get_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)

@app.route('/api/jobs/<job_id>/download', methods=['GET'])
def download_job_zip(job_id):
    job = JOBS.get(job_id)
    if not job or job['status'] != 'completed' or 'zip_path' not in job:
        return jsonify({"error": "File not ready or job failed"}), 404
    
    return send_file(
        job['zip_path'],
        as_attachment=True,
        download_name=f"ytbatch_{job['filename']}_{job_id[:8]}.zip"
    )

@app.route('/api/jobs', methods=['GET'])
def list_jobs():
    # Return list of jobs sorted by time
    jobs_list = sorted(JOBS.values(), key=lambda x: x['created_at'], reverse=True)
    return jsonify(jobs_list)

@app.route('/api/status')
def status():
    # Simple health check
    return jsonify({"status": "ok", "active_jobs": len([j for j in JOBS.values() if j['status'] == 'running'])})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    print(f"Starting YTBatch Pro (Ephemeral) on port {port}")
    socketio.run(app, host='0.0.0.0', port=port)
