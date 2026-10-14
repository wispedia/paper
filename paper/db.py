import os
import sqlite3
from pathlib import Path


def data_dir():
    return Path(os.environ.get("PAPER_DATA_DIR", "data")).resolve()


def connect():
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(path / "paper.sqlite3", timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    return conn


def initialize():
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
            active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
            last_request_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            csrf TEXT NOT NULL, expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
            address TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS attempts_time ON login_attempts(created_at);
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            original_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
            stage TEXT NOT NULL DEFAULT '等待处理', error TEXT,
            created_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER,
            title TEXT, warnings TEXT NOT NULL DEFAULT '[]', usage TEXT,
            attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS jobs_user ON jobs(user_id, created_at);
        """)
        # Historical jobs used high reasoning. Serialize the additive migration
        # because the web and worker processes can start at the same time.
        conn.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        if "recognition_mode" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN recognition_mode TEXT NOT NULL DEFAULT 'fine'")
