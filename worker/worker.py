import os
import time
import json
import subprocess
import requests
import threading
from pathlib import Path

QUEUE = "/queue/jobs.json"
INPUT = "/input"
SCRIPT = "/app/download_batch.sh"
WEB_URL = "http://ytbatch-web:5000"

POLL_INTERVAL = float(os.getenv("YTBATCH_POLL_INTERVAL", "3"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))

# Global tracking
active_downloads = {}
download_progress = {}


def load_jobs():
    """Load jobs array from QUEUE"""
    if not os.path.exists(QUEUE):
        os.makedirs(os.path.dirname(QUEUE), exist_ok=True)
        with open(QUEUE, "w") as f:
            f.write("[]")
        return []

    with open(QUEUE, "r") as f:
        try:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
        except Exception:
            with open(QUEUE, "w") as wf:
                wf.write("[]")
            return []


def save_jobs(data):
    os.makedirs(os.path.dirname(QUEUE), exist_ok=True)
    with open(QUEUE, "w") as f:
        json.dump(data, f, indent=2)


def count_lines(path: str) -> int:
    """Count non-empty, non-comment lines in file"""
    try:
        with open(path, "r") as f:
            return sum(1 for line in f if line.strip() and not line.startswith('#'))
    except FileNotFoundError:
        return 0


def send_progress_update(job_id, file_name, status, progress=None, overall_progress=None):
    """Send progress update to web UI via HTTP request"""
    try:
        data = {
            "job_id": job_id,
            "file_name": file_name,
            "status": status,
            "progress": progress,
            "overall_progress": overall_progress,
            "timestamp": time.time()
        }
        requests.post(f"{WEB_URL}/api/progress", json=data, timeout=2)
    except Exception as e:
        print(f"Failed to send progress update: {e}")


def parse_download_output(line, job_id, current_file):
    """Parse yt-dlp output for progress information"""
    if "Downloading webpage" in line:
        send_progress_update(job_id, current_file, "downloading_webpage", 10)
    elif "Downloading video" in line or "Downloading thumbnail" in line:
        send_progress_update(job_id, current_file, "downloading", 30)
    elif "[download]" in line and "%" in line:
        # Extract percentage from [download] line
        try:
            percent_str = line.split("%")[0].split()[-1]
            percent = float(percent_str)
            send_progress_update(job_id, current_file, "downloading", percent)
        except:
            pass
    elif "has already been downloaded" in line:
        send_progress_update(job_id, current_file, "completed", 100)
    elif "Deleting original file" in line or "merging formats" in line:
        send_progress_update(job_id, current_file, "processing", 90)
    elif "Download completed" in line or "Finished downloading" in line:
        send_progress_update(job_id, current_file, "completed", 100)
    elif "ERROR:" in line or "Failed" in line:
        send_progress_update(job_id, current_file, "failed", 0)


def download_with_progress(job_id, url, output_dir, filename):
    """Download a single URL with progress tracking"""
    try:
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        
        # Send initial progress
        send_progress_update(job_id, filename, "starting", 0)
        
        # Build yt-dlp command with progress output
        cmd = [
            "yt-dlp",
            "--ignore-errors",
            "--no-warnings",
            "--newline",  # Force newline output for easier parsing
            "-o", f"{output_dir}/%(upload_date>%Y-%m-%d)s_%(id)s.%(ext)s",
            "--download-archive", f"{output_dir}/.downloaded.txt",
            url
        ]
        
        # Start the download process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Read output line by line
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                print(f"[{job_id}] {line.strip()}")
                parse_download_output(line, job_id, filename)
        
        # Check final status
        return_code = process.poll()
        if return_code == 0:
            send_progress_update(job_id, filename, "completed", 100)
            return True
        else:
            error_output = process.stderr.read()
            send_progress_update(job_id, filename, "failed", 0)
            print(f"Download failed for {url}: {error_output}")
            return False
            
    except Exception as e:
        send_progress_update(job_id, filename, "failed", 0)
        print(f"Exception during download: {e}")
        return False


def process_job(job, jobs):
    """Process a single queued job with real-time progress tracking"""
    job_id = job["id"]
    filename = job["file"]
    file_path = os.path.join(INPUT, filename)
    
    # Determine total number of lines/URLs for progress display
    total_urls = count_lines(file_path)
    if total_urls <= 0:
        total_urls = 1  # Avoid division by zero in UI
    
    # Update job status to running
    job["status"] = "running"
    job["progress"] = 0
    job["total"] = total_urls
    job["started"] = time.time()
    job["files"] = {}
    save_jobs(jobs)
    
    # Notify web UI
    send_progress_update(job_id, "", "job_started", 0, 0)
    
    user_dir = os.path.join("/downloads", filename.replace(".txt", ""))
    completed_files = 0
    failed_files = 0
    
    try:
        # Read URLs from file
        with open(file_path, 'r') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
        # Process each URL
        for i, url in enumerate(urls):
            if not url:
                continue
                
            # Skip TikTok photo URLs (not supported)
            if '/photo/' in url:
                print(f"Skipping photo URL: {url}")
                continue
            
            # Generate filename from URL
            filename = f"video_{i+1:03d}"
            
            # Update current file status
            job["files"][filename] = {
                "status": "queued",
                "progress": 0,
                "url": url
            }
            save_jobs(jobs)
            
            # Download with progress tracking
            print(f"Starting download: {url}")
            success = download_with_progress(job_id, url, user_dir, filename)
            
            if success:
                completed_files += 1
                job["files"][filename]["status"] = "completed"
                job["files"][filename]["progress"] = 100
            else:
                failed_files += 1
                job["files"][filename]["status"] = "failed"
                job["files"][filename]["progress"] = 0
            
            # Update overall progress
            job["progress"] = completed_files
            overall_progress = int((completed_files / total_urls) * 100) if total_urls > 0 else 0
            job["overall_progress"] = overall_progress
            save_jobs(jobs)
            
            # Send overall progress update
            send_progress_update(job_id, filename, "completed" if success else "failed", 
                               100 if success else 0, overall_progress)
        
        # Final job status
        if failed_files == 0:
            job["status"] = "completed"
            job["overall_progress"] = 100
        elif completed_files > 0:
            job["status"] = "completed_with_errors"
            job["error"] = f"Completed {completed_files} files, {failed_files} failed"
        else:
            job["status"] = "failed"
            job["error"] = "All downloads failed"
        
        job["finished"] = time.time()
        save_jobs(jobs)
        
        # Send final update
        send_progress_update(job_id, "", "job_completed" if job["status"] == "completed" else "job_failed", 
                           None, 100 if job["status"] == "completed" else 0)
        
    except Exception as e:
        print(f"Error processing job {job_id}: {e}")
        job["status"] = "failed"
        job["error"] = str(e)
        job["finished"] = time.time()
        save_jobs(jobs)
        send_progress_update(job_id, "", "job_failed", None, 0)


def main():
    print("Worker started, waiting for jobs...")
    
    while True:
        jobs = load_jobs()
        
        # Find next queued job
        found_queued = False
        for job in jobs:
            if job.get("status") == "queued":
                found_queued = True
                print(f"Processing job: {job['id']} ({job['file']})")
                process_job(job, jobs)
                break
        
        if not found_queued:
            # No jobs to process, sleep
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()