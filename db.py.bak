import sqlite3
import json
import time
from threading import Lock

DB_PATH = "data/ytbatch.db"
db_lock = Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            name TEXT,
            status TEXT,
            created_at REAL,
            total_items INTEGER,
            progress INTEGER,
            progress_text TEXT,
            error TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)''')
        conn.commit()
        conn.close()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_config():
    defaults = {
        "max_concurrent_downloads": 3,
        "video_quality": "best",
        "extract_audio": False,
        "theme": "dark"
    }
    try:
        conn = get_db_connection()
        rows = conn.execute('SELECT key, value FROM config').fetchall()
        conn.close()
        saved = {row['key']: json.loads(row['value']) for row in rows}
        return {**defaults, **saved}
    except Exception:
        return defaults

def save_config(config_dict):
    with db_lock:
        conn = get_db_connection()
        for k, v in config_dict.items():
            conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (k, json.dumps(v)))
        conn.commit()
        conn.close()

def create_job(job_id, name, urls):
    with db_lock:
        conn = get_db_connection()
        conn.execute('''INSERT INTO jobs 
            (id, name, status, created_at, total_items, progress, progress_text) 
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (job_id, name, 'queued', time.time(), len(urls), 0, "Queued"))
        conn.commit()
        conn.close()

def update_job(job_id, data):
    if not data: return
    set_clause = ', '.join([f"{k} = ?" for k in data.keys()])
    values = list(data.values())
    values.append(job_id)
    with db_lock:
        conn = get_db_connection()
        conn.execute(f'UPDATE jobs SET {set_clause} WHERE id = ?', values)
        conn.commit()
        conn.close()

def get_jobs():
    conn = get_db_connection()
    # Performance: Only fetch last 50 jobs to keep UI snappy
    jobs = conn.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 50').fetchall()
    conn.close()
    return [dict(row) for row in jobs]

def get_job(job_id):
    conn = get_db_connection()
    job = conn.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
    conn.close()
    return dict(job) if job else None

def delete_job(job_id):
    with db_lock:
        conn = get_db_connection()
        conn.execute('DELETE FROM jobs WHERE id = ?', (job_id,))
        # Run vacuum to reclaim space periodically
        conn.execute('VACUUM') 
        conn.commit()
        conn.close()
