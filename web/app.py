import os
import json
import uuid
import time
import threading
import queue as threading_queue
from datetime import datetime

from flask import (
    Flask,
    request,
    render_template_string,
    send_from_directory,
    redirect,
    jsonify,
    make_response
)
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*")

QUEUE_FILE = "/queue/jobs.json"
INPUT_DIR = "/input"
DOWNLOAD_DIR = "/downloads"
CONFIG_FILE = "/queue/config.json"

# Global progress tracking
progress_queue = threading_queue.Queue()
active_downloads = {}

# ---------- CONFIGURATION MANAGEMENT ----------

def load_config():
    """Load configuration from file"""
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "max_concurrent_downloads": 3,
            "download_quality": "best",
            "enable_audio_extraction": False,
            "auto_cleanup_completed": False,
            "notification_enabled": True,
            "theme": "dark"
        }
        save_config(default_config)
        return default_config
    
    with open(CONFIG_FILE, "r") as f:
        try:
            return json.load(f)
        except Exception:
            return {}


def save_config(config):
    """Save configuration to file"""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

# ---------- JOB STORAGE HELPERS ----------

def load_jobs():
    """Load jobs from the shared JSON queue file"""
    if not os.path.exists(QUEUE_FILE):
        os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
        with open(QUEUE_FILE, "w") as f:
            f.write("[]")
        return []

    with open(QUEUE_FILE, "r") as f:
        try:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
        except Exception:
            with open(QUEUE_FILE, "w") as wf:
                wf.write("[]")
            return []


def save_jobs(jobs):
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    with open(QUEUE_FILE, "w") as f:
        json.dump(jobs, f, indent=2)

# ---------- REAL-TIME PROGRESS TRACKING ----------

def broadcast_progress(job_id, progress_type, data):
    """Broadcast progress updates via WebSocket"""
    socketio.emit('progress_update', {
        'job_id': job_id,
        'type': progress_type,
        'data': data,
        'timestamp': time.time()
    }, room=None)


def update_job_progress(job_id, file_name, status, progress=None):
    """Update job progress and broadcast via WebSocket"""
    jobs = load_jobs()
    for job in jobs:
        if job.get("id") == job_id:
            if "files" not in job:
                job["files"] = {}
            
            job["files"][file_name] = {
                "status": status,
                "progress": progress,
                "updated": time.time()
            }
            
            # Update overall job progress
            completed_files = sum(1 for f in job.get("files", {}).values() if f["status"] == "completed")
            total_files = len(job.get("files", {}))
            if total_files > 0:
                job["progress"] = completed_files
                job["overall_progress"] = int((completed_files / total_files) * 100)
            
            save_jobs(jobs)
            broadcast_progress(job_id, "file_update", {
                "file_name": file_name,
                "status": status,
                "progress": progress,
                "overall_progress": job.get("overall_progress", 0)
            })
            break

# ---------- WEBSOCKET EVENTS ----------

@socketio.on('connect')
def handle_connect():
    print('Client connected')
    # Send current jobs status
    jobs = load_jobs()
    emit('initial_state', {'jobs': jobs})


@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')


@socketio.on('get_job_details')
def handle_get_job_details(data):
    job_id = data.get('job_id')
    jobs = load_jobs()
    for job in jobs:
        if job.get("id") == job_id:
            emit('job_details', job)
            break

# ---------- ROUTES ----------

@app.route("/")
def index():
    config = load_config()
    return render_template_string(TEMPLATE, config=config)


@app.route("/api/jobs", methods=["GET"])
def api_jobs():
    """API endpoint to get all jobs"""
    jobs = load_jobs()
    return jsonify(jobs)


@app.route("/api/jobs", methods=["POST"])
def create_job():
    """API endpoint to create a new job"""
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    uploaded = request.files['file']
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "No file selected"}), 400
    
    filename = uploaded.filename.strip()
    if not filename.lower().endswith(".txt"):
        filename += ".txt"
    
    os.makedirs(INPUT_DIR, exist_ok=True)
    dest = os.path.join(INPUT_DIR, filename)
    uploaded.save(dest)
    
    # Count total URLs
    total_urls = 0
    with open(dest, 'r') as f:
        total_urls = sum(1 for line in f if line.strip() and not line.startswith('#'))
    
    jobs = load_jobs()
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "file": filename,
        "status": "queued",
        "progress": 0,
        "total": total_urls,
        "created": time.time(),
        "files": {}
    }
    jobs.append(job)
    save_jobs(jobs)
    
    # Broadcast new job
    socketio.emit('new_job', job)
    
    return jsonify(job)


@app.route("/api/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    """API endpoint to get a specific job"""
    jobs = load_jobs()
    for job in jobs:
        if job.get("id") == job_id:
            return jsonify(job)
    return jsonify({"error": "Job not found"}), 404


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    """Cancel a queued job"""
    jobs = load_jobs()
    for job in jobs:
        if job.get("id") == job_id and job.get("status") == "queued":
            job["status"] = "cancelled"
            job["finished"] = time.time()
            save_jobs(jobs)
            socketio.emit('job_cancelled', job)
            return jsonify(job)
    return jsonify({"error": "Job not found or not queued"}), 404


@app.route("/api/jobs/<job_id>/retry", methods=["POST"])
def retry_job(job_id):
    """Retry a failed job"""
    jobs = load_jobs()
    for job in jobs:
        if job.get("id") == job_id and job.get("status") in ["failed", "cancelled"]:
            job["status"] = "queued"
            job["progress"] = 0
            job["files"] = {}
            job.pop("error", None)
            job.pop("finished", None)
            job.pop("started", None)
            save_jobs(jobs)
            socketio.emit('job_retried', job)
            return jsonify(job)
    return jsonify({"error": "Job not found or not retryable"}), 404


@app.route("/api/jobs/<job_id>/delete", methods=["DELETE"])
def delete_job(job_id):
    """Delete a job"""
    jobs = load_jobs()
    jobs = [j for j in jobs if j.get("id") != job_id]
    save_jobs(jobs)
    socketio.emit('job_deleted', {"job_id": job_id})
    return jsonify({"success": True})


@app.route("/api/config", methods=["GET"])
def get_config():
    """Get current configuration"""
    return jsonify(load_config())


@app.route("/api/config", methods=["PUT"])
def update_config():
    """Update configuration"""
    config = load_config()
    new_config = request.get_json()
    config.update(new_config)
    save_config(config)
    socketio.emit('config_updated', config)
    return jsonify(config)


@app.route("/api/files")
def api_files():
    """API endpoint to get downloaded files"""
    files_list = []
    if os.path.exists(DOWNLOAD_DIR):
        for user in os.listdir(DOWNLOAD_DIR):
            user_path = os.path.join(DOWNLOAD_DIR, user)
            if os.path.isdir(user_path):
                for filename in os.listdir(user_path):
                    if os.path.isfile(os.path.join(user_path, filename)):
                        files_list.append({
                            "user": user,
                            "filename": filename,
                            "path": f"/download/{user}/{filename}",
                            "size": os.path.getsize(os.path.join(user_path, filename)),
                            "modified": os.path.getmtime(os.path.join(user_path, filename))
                        })
    return jsonify(files_list)


@app.route("/download/<user>/<file>")
def download(user, file):
    return send_from_directory(
        os.path.join(DOWNLOAD_DIR, user), file, as_attachment=True
    )


@app.route("/api/progress", methods=["POST"])
def update_progress():
    """Receive progress updates from worker"""
    data = request.get_json()
    job_id = data.get('job_id')
    file_name = data.get('file_name')
    status = data.get('status')
    progress = data.get('progress')
    
    # Broadcast progress update via WebSocket
    socketio.emit('progress_update', {
        'job_id': job_id,
        'file_name': file_name,
        'status': status,
        'progress': progress,
        'timestamp': time.time()
    }, room=None)
    
    # Also update the job in the queue file
    jobs = load_jobs()
    for job in jobs:
        if job.get("id") == job_id:
            if "files" not in job:
                job["files"] = {}
            
            if file_name:
                job["files"][file_name] = {
                    "status": status,
                    "progress": progress,
                    "updated": time.time()
                }
                
                # Update overall progress
                completed_files = sum(1 for f in job.get("files", {}).values() if f["status"] == "completed")
                total_files = len(job.get("files", {}))
                if total_files > 0:
                    job["progress"] = completed_files
                    job["overall_progress"] = int((completed_files / total_files) * 100)
            
            save_jobs(jobs)
            break
    
    return jsonify({"success": True})


# ---------- MASTER TEMPLATE ----------

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Real-time Batch Downloader</title>
    <script src="https://cdn.socket.io/4.6.2/socket.io.min.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        .progress-bar {
            transition: width 0.3s ease;
        }
        .file-item {
            transition: all 0.2s ease;
        }
        .file-item:hover {
            transform: translateY(-1px);
        }
        .status-queued { background-color: #fbbf24; }
        .status-running { background-color: #3b82f6; }
        .status-completed { background-color: #10b981; }
        .status-failed { background-color: #ef4444; }
        .status-cancelled { background-color: #6b7280; }
    </style>
</head>

<body class="bg-gray-900 text-white">
    <div class="max-w-6xl mx-auto mt-6 px-4">
        <!-- Header -->
        <div class="flex justify-between items-center mb-8">
            <div>
                <h1 class="text-4xl font-bold text-blue-400">
                    <i class="fas fa-download mr-3"></i>
                    Real-time Batch Downloader
                </h1>
                <p class="text-gray-400 mt-2">Monitor and manage your downloads in real-time</p>
            </div>
            <button id="settingsBtn" class="bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded-lg">
                <i class="fas fa-cog mr-2"></i>Settings
            </button>
        </div>

        <!-- Upload Section -->
        <div class="bg-gray-800 rounded-lg p-6 mb-6">
            <h2 class="text-xl font-semibold mb-4">
                <i class="fas fa-upload mr-2"></i>Upload URL List
            </h2>
            <div id="uploadArea" class="border-2 border-dashed border-gray-600 rounded-lg p-8 text-center cursor-pointer hover:border-blue-400 transition-colors">
                <i class="fas fa-cloud-upload-alt text-4xl text-gray-400 mb-4"></i>
                <p class="text-lg font-medium">Drop your .txt file here or click to browse</p>
                <p class="text-sm text-gray-400 mt-2">One URL per line, comments with #</p>
                <input type="file" id="fileInput" class="hidden" accept=".txt">
            </div>
        </div>

        <!-- Stats Cards -->
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
            <div class="bg-gray-800 rounded-lg p-4">
                <div class="flex items-center">
                    <i class="fas fa-tasks text-blue-400 text-2xl mr-3"></i>
                    <div>
                        <p class="text-gray-400 text-sm">Total Jobs</p>
                        <p id="totalJobs" class="text-2xl font-bold">0</p>
                    </div>
                </div>
            </div>
            <div class="bg-gray-800 rounded-lg p-4">
                <div class="flex items-center">
                    <i class="fas fa-spinner text-yellow-400 text-2xl mr-3"></i>
                    <div>
                        <p class="text-gray-400 text-sm">Active Jobs</p>
                        <p id="activeJobs" class="text-2xl font-bold">0</p>
                    </div>
                </div>
            </div>
            <div class="bg-gray-800 rounded-lg p-4">
                <div class="flex items-center">
                    <i class="fas fa-check-circle text-green-400 text-2xl mr-3"></i>
                    <div>
                        <p class="text-gray-400 text-sm">Completed</p>
                        <p id="completedJobs" class="text-2xl font-bold">0</p>
                    </div>
                </div>
            </div>
            <div class="bg-gray-800 rounded-lg p-4">
                <div class="flex items-center">
                    <i class="fas fa-file text-purple-400 text-2xl mr-3"></i>
                    <div>
                        <p class="text-gray-400 text-sm">Total Files</p>
                        <p id="totalFiles" class="text-2xl font-bold">0</p>
                    </div>
                </div>
            </div>
        </div>

        <!-- Jobs Section -->
        <div class="bg-gray-800 rounded-lg p-6 mb-6">
            <div class="flex justify-between items-center mb-4">
                <h2 class="text-xl font-semibold">
                    <i class="fas fa-list mr-2"></i>Download Jobs
                </h2>
                <button id="refreshJobs" class="bg-blue-600 hover:bg-blue-700 px-3 py-1 rounded text-sm">
                    <i class="fas fa-sync-alt mr-1"></i>Refresh
                </button>
            </div>
            <div id="jobsList" class="space-y-4">
                <!-- Jobs will be populated here -->
            </div>
        </div>

        <!-- Files Section -->
        <div class="bg-gray-800 rounded-lg p-6">
            <div class="flex justify-between items-center mb-4">
                <h2 class="text-xl font-semibold">
                    <i class="fas fa-folder mr-2"></i>Downloaded Files
                </h2>
                <button id="refreshFiles" class="bg-green-600 hover:bg-green-700 px-3 py-1 rounded text-sm">
                    <i class="fas fa-sync-alt mr-1"></i>Refresh
                </button>
            </div>
            <div id="filesList" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <!-- Files will be populated here -->
            </div>
        </div>
    </div>

    <!-- Settings Modal -->
    <div id="settingsModal" class="fixed inset-0 bg-black bg-opacity-50 hidden z-50">
        <div class="flex items-center justify-center min-h-screen p-4">
            <div class="bg-gray-800 rounded-lg p-6 w-full max-w-md">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="text-xl font-semibold">
                        <i class="fas fa-cog mr-2"></i>Settings
                    </h3>
                    <button id="closeSettings" class="text-gray-400 hover:text-white">
                        <i class="fas fa-times text-xl"></i>
                    </button>
                </div>
                <div class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium mb-2">Max Concurrent Downloads</label>
                        <input type="number" id="maxConcurrent" class="w-full bg-gray-700 rounded px-3 py-2" min="1" max="10">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Download Quality</label>
                        <select id="downloadQuality" class="w-full bg-gray-700 rounded px-3 py-2">
                            <option value="best">Best</option>
                            <option value="worst">Worst</option>
                            <option value="best[height<=720]">720p Max</option>
                            <option value="best[height<=480]">480p Max</option>
                        </select>
                    </div>
                    <div class="flex items-center">
                        <input type="checkbox" id="enableAudio" class="mr-2">
                        <label for="enableAudio">Enable Audio Extraction</label>
                    </div>
                    <div class="flex items-center">
                        <input type="checkbox" id="autoCleanup" class="mr-2">
                        <label for="autoCleanup">Auto-cleanup Completed Jobs</label>
                    </div>
                    <div class="flex items-center">
                        <input type="checkbox" id="notifications" class="mr-2">
                        <label for="notifications">Enable Notifications</label>
                    </div>
                </div>
                <div class="flex justify-end mt-6">
                    <button id="saveSettings" class="bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded">
                        Save Settings
                    </button>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Socket.IO connection
        const socket = io();
        
        let jobs = [];
        let files = [];
        let config = {};

        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            loadConfig();
            setupEventListeners();
            socket.emit('get_initial_state');
        });

        // Socket.IO event handlers
        socket.on('connect', function() {
            console.log('Connected to server');
        });

        socket.on('initial_state', function(data) {
            jobs = data.jobs || [];
            updateJobsList();
            updateStats();
        });

        socket.on('new_job', function(job) {
            jobs.unshift(job);
            updateJobsList();
            updateStats();
            showNotification('New job added', 'success');
        });

        socket.on('job_cancelled', function(job) {
            updateJobInList(job);
            updateStats();
            showNotification('Job cancelled', 'info');
        });

        socket.on('job_retried', function(job) {
            updateJobInList(job);
            updateStats();
            showNotification('Job retried', 'success');
        });

        socket.on('job_deleted', function(data) {
            jobs = jobs.filter(j => j.id !== data.job_id);
            updateJobsList();
            updateStats();
            showNotification('Job deleted', 'info');
        });

        socket.on('progress_update', function(data) {
            updateJobProgress(data);
        });

        socket.on('config_updated', function(newConfig) {
            config = newConfig;
            updateConfigUI();
            showNotification('Settings saved', 'success');
        });

        // Event listeners
        function setupEventListeners() {
            // File upload
            document.getElementById('uploadArea').addEventListener('click', () => {
                document.getElementById('fileInput').click();
            });

            document.getElementById('fileInput').addEventListener('change', handleFileUpload);

            // Settings
            document.getElementById('settingsBtn').addEventListener('click', () => {
                document.getElementById('settingsModal').classList.remove('hidden');
            });

            document.getElementById('closeSettings').addEventListener('click', () => {
                document.getElementById('settingsModal').classList.add('hidden');
            });

            document.getElementById('saveSettings').addEventListener('click', saveSettings);

            // Refresh buttons
            document.getElementById('refreshJobs').addEventListener('click', loadJobs);
            document.getElementById('refreshFiles').addEventListener('click', loadFiles);

            // Drag and drop
            const uploadArea = document.getElementById('uploadArea');
            uploadArea.addEventListener('dragover', (e) => {
                e.preventDefault();
                uploadArea.classList.add('border-blue-400');
            });

            uploadArea.addEventListener('dragleave', () => {
                uploadArea.classList.remove('border-blue-400');
            });

            uploadArea.addEventListener('drop', (e) => {
                e.preventDefault();
                uploadArea.classList.remove('border-blue-400');
                const files = e.dataTransfer.files;
                if (files.length > 0) {
                    handleFileUpload({ target: { files: files } });
                }
            });
        }

        // File upload handler
        function handleFileUpload(event) {
            const file = event.target.files[0];
            if (!file) return;

            const formData = new FormData();
            formData.append('file', file);

            fetch('/api/jobs', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    showNotification(data.error, 'error');
                } else {
                    showNotification('Job created successfully', 'success');
                }
            })
            .catch(error => {
                showNotification('Upload failed', 'error');
                console.error('Upload error:', error);
            });
        }

        // Load and save settings
        function loadConfig() {
            fetch('/api/config')
                .then(response => response.json())
                .then(data => {
                    config = data;
                    updateConfigUI();
                })
                .catch(error => console.error('Error loading config:', error));
        }

        function updateConfigUI() {
            document.getElementById('maxConcurrent').value = config.max_concurrent_downloads || 3;
            document.getElementById('downloadQuality').value = config.download_quality || 'best';
            document.getElementById('enableAudio').checked = config.enable_audio_extraction || false;
            document.getElementById('autoCleanup').checked = config.auto_cleanup_completed || false;
            document.getElementById('notifications').checked = config.notification_enabled || false;
        }

        function saveSettings() {
            const newConfig = {
                max_concurrent_downloads: parseInt(document.getElementById('maxConcurrent').value),
                download_quality: document.getElementById('downloadQuality').value,
                enable_audio_extraction: document.getElementById('enableAudio').checked,
                auto_cleanup_completed: document.getElementById('autoCleanup').checked,
                notification_enabled: document.getElementById('notifications').checked
            };

            fetch('/api/config', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(newConfig)
            })
            .then(response => response.json())
            .then(data => {
                document.getElementById('settingsModal').classList.add('hidden');
            })
            .catch(error => {
                showNotification('Failed to save settings', 'error');
                console.error('Settings save error:', error);
            });
        }

        // Job management
        function loadJobs() {
            fetch('/api/jobs')
                .then(response => response.json())
                .then(data => {
                    jobs = data;
                    updateJobsList();
                    updateStats();
                })
                .catch(error => console.error('Error loading jobs:', error));
        }

        function updateJobsList() {
            const container = document.getElementById('jobsList');
            if (jobs.length === 0) {
                container.innerHTML = `
                    <div class="text-center py-8 text-gray-400">
                        <i class="fas fa-inbox text-4xl mb-2"></i>
                        <p>No jobs yet. Upload a URL list to get started!</p>
                    </div>
                `;
                return;
            }

            container.innerHTML = jobs.map(job => {
                const statusColor = getStatusColor(job.status);
                const progress = job.overall_progress || 0;
                const created = new Date(job.created * 1000).toLocaleString();
                
                return `
                    <div class="bg-gray-700 rounded-lg p-4 file-item">
                        <div class="flex justify-between items-start mb-3">
                            <div>
                                <h3 class="font-semibold text-lg">${job.file}</h3>
                                <p class="text-sm text-gray-400">Created: ${created}</p>
                                <p class="text-xs text-gray-500">ID: ${job.id.substring(0, 8)}</p>
                            </div>
                            <div class="flex items-center space-x-2">
                                <span class="px-2 py-1 rounded text-xs font-medium ${statusColor}">
                                    ${job.status.toUpperCase()}
                                </span>
                                ${getJobActions(job)}
                            </div>
                        </div>
                        
                        <div class="mb-3">
                            <div class="flex justify-between text-sm mb-1">
                                <span>Progress: ${job.progress}/${job.total}</span>
                                <span>${progress}%</span>
                            </div>
                            <div class="w-full bg-gray-600 rounded-full h-2">
                                <div class="bg-blue-500 h-2 rounded-full progress-bar" style="width: ${progress}%"></div>
                            </div>
                        </div>
                        
                        ${job.error ? `<div class="text-red-400 text-sm mb-2">${job.error}</div>` : ''}
                        
                        ${renderJobFiles(job)}
                    </div>
                `;
            }).join('');
        }

        function getStatusColor(status) {
            const colors = {
                'queued': 'bg-yellow-500 text-yellow-900',
                'running': 'bg-blue-500 text-white',
                'completed': 'bg-green-500 text-white',
                'failed': 'bg-red-500 text-white',
                'cancelled': 'bg-gray-500 text-white'
            };
            return colors[status] || 'bg-gray-500 text-white';
        }

        function getJobActions(job) {
            let actions = '';
            
            if (job.status === 'queued') {
                actions += `<button onclick="cancelJob('${job.id}')" class="text-red-400 hover:text-red-300 text-sm">Cancel</button>`;
            }
            
            if (job.status === 'failed' || job.status === 'cancelled') {
                actions += `<button onclick="retryJob('${job.id}')" class="text-blue-400 hover:text-blue-300 text-sm ml-2">Retry</button>`;
            }
            
            if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
                actions += `<button onclick="deleteJob('${job.id}')" class="text-gray-400 hover:text-gray-300 text-sm ml-2">Delete</button>`;
            }
            
            return actions;
        }

        function renderJobFiles(job) {
            if (!job.files || Object.keys(job.files).length === 0) {
                return '';
            }
            
            const fileList = Object.entries(job.files).slice(0, 5).map(([filename, file]) => {
                const fileStatusColor = getStatusColor(file.status);
                return `
                    <div class="flex justify-between items-center text-sm">
                        <span class="truncate flex-1 mr-2">${filename}</span>
                        <span class="px-1 py-0.5 rounded text-xs ${fileStatusColor}">${file.status}</span>
                    </div>
                `;
            }).join('');
            
            const remaining = Object.keys(job.files).length - 5;
            const moreText = remaining > 0 ? `<div class="text-xs text-gray-400 mt-1">+${remaining} more files</div>` : '';
            
            return `
                <div class="mt-3 pt-3 border-t border-gray-600">
                    <h4 class="text-sm font-medium mb-2">Files:</h4>
                    <div class="space-y-1">
                        ${fileList}
                        ${moreText}
                    </div>
                </div>
            `;
        }

        function updateJobProgress(data) {
            const job = jobs.find(j => j.id === data.job_id);
            if (job) {
                if (!job.files) job.files = {};
                job.files[data.data.file_name] = {
                    status: data.data.status,
                    progress: data.data.progress
                };
                job.overall_progress = data.data.overall_progress;
                updateJobsList();
                updateStats();
            }
        }

        function updateJobInList(updatedJob) {
            const index = jobs.findIndex(j => j.id === updatedJob.id);
            if (index !== -1) {
                jobs[index] = updatedJob;
                updateJobsList();
                updateStats();
            }
        }

        // Job actions
        function cancelJob(jobId) {
            fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        showNotification(data.error, 'error');
                    } else {
                        showNotification('Job cancelled', 'success');
                    }
                })
                .catch(error => {
                    showNotification('Failed to cancel job', 'error');
                    console.error('Cancel error:', error);
                });
        }

        function retryJob(jobId) {
            fetch(`/api/jobs/${jobId}/retry`, { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        showNotification(data.error, 'error');
                    } else {
                        showNotification('Job retried', 'success');
                    }
                })
                .catch(error => {
                    showNotification('Failed to retry job', 'error');
                    console.error('Retry error:', error);
                });
        }

        function deleteJob(jobId) {
            if (!confirm('Are you sure you want to delete this job?')) {
                return;
            }
            
            fetch(`/api/jobs/${jobId}`, { method: 'DELETE' })
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        showNotification(data.error, 'error');
                    } else {
                        showNotification('Job deleted', 'success');
                    }
                })
                .catch(error => {
                    showNotification('Failed to delete job', 'error');
                    console.error('Delete error:', error);
                });
        }

        // Files management
        function loadFiles() {
            fetch('/api/files')
                .then(response => response.json())
                .then(data => {
                    files = data;
                    updateFilesList();
                    updateStats();
                })
                .catch(error => console.error('Error loading files:', error));
        }

        function updateFilesList() {
            const container = document.getElementById('filesList');
            if (files.length === 0) {
                container.innerHTML = `
                    <div class="col-span-full text-center py-8 text-gray-400">
                        <i class="fas fa-folder-open text-4xl mb-2"></i>
                        <p>No files downloaded yet.</p>
                    </div>
                `;
                return;
            }

            container.innerHTML = files.map(file => {
                const size = formatFileSize(file.size);
                const modified = new Date(file.modified * 1000).toLocaleDateString();
                
                return `
                    <div class="bg-gray-700 rounded-lg p-4 file-item">
                        <div class="flex items-start justify-between mb-2">
                            <i class="fas fa-file text-blue-400 text-xl mr-2 mt-1"></i>
                            <div class="flex-1 min-w-0">
                                <h4 class="font-medium truncate">${file.filename}</h4>
                                <p class="text-sm text-gray-400">${file.user}</p>
                            </div>
                        </div>
                        <div class="text-sm text-gray-400 mb-3">
                            <p>Size: ${size}</p>
                            <p>Modified: ${modified}</p>
                        </div>
                        <a href="${file.path}" download class="block w-full bg-blue-600 hover:bg-blue-700 text-center py-2 rounded text-sm">
                            <i class="fas fa-download mr-1"></i>Download
                        </a>
                    </div>
                `;
            }).join('');
        }

        // Statistics
        function updateStats() {
            const totalJobs = jobs.length;
            const activeJobs = jobs.filter(j => j.status === 'running' || j.status === 'queued').length;
            const completedJobs = jobs.filter(j => j.status === 'completed').length;
            const totalFiles = files.length;

            document.getElementById('totalJobs').textContent = totalJobs;
            document.getElementById('activeJobs').textContent = activeJobs;
            document.getElementById('completedJobs').textContent = completedJobs;
            document.getElementById('totalFiles').textContent = totalFiles;
        }

        // Utility functions
        function formatFileSize(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }

        function showNotification(message, type = 'info') {
            if (config.notification_enabled === false) return;
            
            // Create notification element
            const notification = document.createElement('div');
            notification.className = `fixed top-4 right-4 p-4 rounded-lg shadow-lg z-50 ${getNotificationColor(type)}`;
            notification.innerHTML = `
                <div class="flex items-center">
                    <i class="fas ${getNotificationIcon(type)} mr-2"></i>
                    <span>${message}</span>
                </div>
            `;
            
            document.body.appendChild(notification);
            
            // Remove after 3 seconds
            setTimeout(() => {
                notification.remove();
            }, 3000);
        }

        function getNotificationColor(type) {
            const colors = {
                'success': 'bg-green-600 text-white',
                'error': 'bg-red-600 text-white',
                'warning': 'bg-yellow-600 text-white',
                'info': 'bg-blue-600 text-white'
            };
            return colors[type] || colors.info;
        }

        function getNotificationIcon(type) {
            const icons = {
                'success': 'fa-check-circle',
                'error': 'fa-exclamation-circle',
                'warning': 'fa-exclamation-triangle',
                'info': 'fa-info-circle'
            };
            return icons[type] || icons.info;
        }

        // Auto-refresh
        setInterval(() => {
            loadJobs();
            loadFiles();
        }, 5000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)