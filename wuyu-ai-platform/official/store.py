"""Private, server-owned data for the integrated Wuyu platform."""
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('WUYU_DATA', ROOT / 'runtime')).resolve()
DEFAULTS = {
    'display_name': 'wuyu-shensi', 'base_url': 'http://103.139.212.177:8444/v1',
    'model': 'qwen3.8-27b-tmidtrain', 'context_window': 32768, 'max_output_tokens': 8192,
    'additional_models': [],
    'supports_vision': True, 'supports_tools': True, 'thinking_mode': 'chat_template',
    'temperature': 0.2, 'history_turns': 4, 'search_enabled': True,
    'memory_tokens': 6144, 'memory_summary_tokens': 1024, 'memory_retrieval_count': 6,
    'memory_trigger_ratio': 0.8,
    'system': '你是无隅智循团队的 wuyu-shensi，专注固废与资源循环领域。请清晰、严谨地回答，区分事实、推测与待核实信息，不编造数据或来源。',
    'max_parallel': 2, 'per_user_parallel': 1, 'max_queue': 64, 'per_user_queue': 3,
    'inflight_token_budget': 65536, 'job_token_budget': 131072,
    'daily_tokens': 2000000, 'daily_questions': 100, 'busy_output_tokens': 4096,
    'job_timeout': 900, 'registration_open': True, 'storage_mb': 200,
}


@contextlib.contextmanager
def connect():
    conn = sqlite3.connect(DATA / 'platform.sqlite3', timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql, values=(), one=False):
    with connect() as conn:
        rows = conn.execute(sql, values).fetchall()
    return (dict(rows[0]) if rows else None) if one else [dict(row) for row in rows]


def execute(sql, values=()):
    with connect() as conn:
        return conn.execute(sql, values).rowcount


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def init():
    DATA.mkdir(parents=True, exist_ok=True)
    key = DATA / 'vault.key'
    if not key.exists():
        with key.open('xb') as file: file.write(Fernet.generate_key())
        os.chmod(key, 0o600)
    with connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL,organization TEXT NOT NULL,identity TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('admin','user')),active INTEGER NOT NULL DEFAULT 1,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS user_passwords(user_id TEXT PRIMARY KEY REFERENCES users(id),password_hash TEXT NOT NULL,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS user_preferences(user_id TEXT PRIMARY KEY REFERENCES users(id),value TEXT NOT NULL,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS password_attempts(email TEXT NOT NULL,ip TEXT NOT NULL,action TEXT NOT NULL,created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS password_attempts_time ON password_attempts(created);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),title TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),size INTEGER NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          status TEXT NOT NULL,input TEXT NOT NULL,turn TEXT NOT NULL,created REAL NOT NULL,started REAL,finished REAL,
          reserved INTEGER NOT NULL,charged INTEGER NOT NULL DEFAULT 0,inflight INTEGER NOT NULL,day TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
        CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,created);
        CREATE INDEX IF NOT EXISTS jobs_user ON jobs(user_id,day);
        CREATE INDEX IF NOT EXISTS jobs_conversation ON jobs(conversation_id,created,id);
        CREATE TABLE IF NOT EXISTS memory_indexed(job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_chunks(id INTEGER PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
          conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          kind TEXT NOT NULL,file_id TEXT,position INTEGER NOT NULL,text TEXT NOT NULL,created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS memory_scope ON memory_chunks(user_id,conversation_id,job_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(terms,tokenize='unicode61');
        CREATE TABLE IF NOT EXISTS memory_preferences(conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL REFERENCES users(id),mode TEXT NOT NULL DEFAULT 'auto',auto_compress INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS memory_summaries(id INTEGER PRIMARY KEY,conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL REFERENCES users(id),watermark TEXT NOT NULL,budget INTEGER NOT NULL,content TEXT NOT NULL,
          token_estimate INTEGER NOT NULL,created REAL NOT NULL,method TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS memory_versions ON memory_summaries(user_id,conversation_id,id);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor TEXT,action TEXT NOT NULL,created REAL NOT NULL);
        ''')


def settings():
    data = dict(DEFAULTS)
    data.update({r['key']: json.loads(r['value']) for r in query('SELECT key,value FROM settings') if r['key'] in DEFAULTS})
    return data


def save_settings(data, actor):
    with connect() as conn:
        for key, value in data.items():
            conn.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))
        conn.execute('INSERT INTO audit(actor,action,created) VALUES(?,?,?)', (actor, 'update_settings', time.time()))


def secret(name):
    env = {'model': 'WUYU_MODEL_API_KEY', 'search': 'METASO_API_KEY'}
    if name not in env:
        if not re.fullmatch(r'(provider|tool|voice)_[a-z][a-z0-9_]{0,47}', name): raise ValueError('密钥标识无效。')
        env[name] = 'WUYU_' + name.upper() + '_KEY'
        if name.startswith('provider_'): env[name] = 'WUYU_MODEL_' + name[9:].upper() + '_API_KEY'
        if name.startswith('voice_'): env[name] = 'WUYU_' + name.upper()
    if os.environ.get(env[name]): return os.environ[env[name]]
    row = query('SELECT value FROM settings WHERE key=?', (name + '_secret',), one=True)
    return Fernet((DATA / 'vault.key').read_bytes()).decrypt(json.loads(row['value']).encode()).decode() if row else ''


def save_secret(name, value):
    if value:
        encrypted = Fernet((DATA / 'vault.key').read_bytes()).encrypt(value.encode()).decode()
        execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (name + '_secret', json.dumps(encrypted)))


def audit(actor, action):
    execute('INSERT INTO audit(actor,action,created) VALUES(?,?,?)', (actor, action, time.time()))


class SingleProcess:
    """Fail closed if somebody starts additional schedulers on the same data."""
    def __enter__(self):
        self.file = (DATA / 'scheduler.lock').open('a+b')
        self.file.seek(0); self.file.write(b'1'); self.file.flush(); self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('该数据目录已有调度进程；请使用单 worker 启动。') from None
        return self
    def __exit__(self, *args):
        self.file.close()
