"""
store.py — SQLite-backed store for dedup and application tracking.
"""

import json
import sqlite3
import logging
from datetime import datetime
from typing import Any

from config import config
from parser import Job

log = logging.getLogger(__name__)


class JobStore:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DB_PATH
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS seen_jobs (
                    id TEXT PRIMARY KEY,
                    seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    company TEXT,
                    title TEXT,
                    location TEXT,
                    apply_url TEXT,
                    source TEXT,
                    date_posted TEXT,
                    is_remote INTEGER,
                    score REAL,
                    match_reasons TEXT,
                    status TEXT DEFAULT 'pending',
                    cover_letter TEXT,
                    applied_at TEXT,
                    notes TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
            """)

    # ── Dedup ────────────────────────────────────────────────────────────

    def is_seen(self, job_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM seen_jobs WHERE id = ?", (job_id,)).fetchone()
            return row is not None

    def mark_seen(self, job_id: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_jobs (id, seen_at) VALUES (?, ?)",
                (job_id, datetime.utcnow().isoformat()),
            )

    # ── Jobs ─────────────────────────────────────────────────────────────

    def save_job(self, job: Job, score: float = 0.0, match_reasons: list = None):
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO jobs
                   (id, company, title, location, apply_url, source, date_posted,
                    is_remote, score, match_reasons, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    job.id, job.company, job.title, job.location,
                    job.apply_url, job.source, job.date_posted,
                    int(job.is_remote), score,
                    json.dumps(match_reasons or []),
                ),
            )

    def update_status(self, job_id: str, status: str, notes: str = "", cover_letter: str = ""):
        """status: pending | approved | applied | rejected | skipped"""
        with self._conn() as conn:
            conn.execute(
                """UPDATE jobs SET status = ?, notes = ?,
                   cover_letter = COALESCE(NULLIF(?, ''), cover_letter),
                   applied_at = CASE WHEN ? = 'applied' THEN datetime('now') ELSE applied_at END
                   WHERE id = ?""",
                (status, notes, cover_letter, status, job_id),
            )

    def get_pending(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = 'pending' ORDER BY score DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all(self, limit: int = 200) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) as applied,
                    SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END) as skipped,
                    ROUND(AVG(score), 2) as avg_score
                FROM jobs
            """).fetchone()
            return dict(row) if row else {}
