"""
store.py — SQLite-backed store for dedup and application tracking.
"""

import json
import sqlite3
import logging
from datetime import datetime
from typing import Any, Optional

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

                CREATE TABLE IF NOT EXISTS indexed_repos (
                    repo_key TEXT PRIMARY KEY,
                    indexed_at TEXT NOT NULL
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
                    body TEXT,
                    resume_pdf_path TEXT,
                    resume_generated_at TEXT,
                    applied_at TEXT,
                    notes TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS agent_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

            # Migration for existing databases created before `body` existed.
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
            if "body" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN body TEXT")
            if "resume_pdf_path" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN resume_pdf_path TEXT")
            if "resume_generated_at" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN resume_generated_at TEXT")

            # Initialize defaults.
            conn.execute(
                "INSERT OR IGNORE INTO agent_settings (key, value) VALUES (?, ?)",
                ("agent_enabled", "true"),
            )
            conn.execute(
                "INSERT OR IGNORE INTO agent_settings (key, value) VALUES (?, ?)",
                ("auto_apply_enabled", "true" if config.AUTO_APPLY_ENABLED else "false"),
            )

    def is_repo_indexed(self, repo_key: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM indexed_repos WHERE repo_key = ?",
                (repo_key,),
            ).fetchone()
            return row is not None

    def mark_repo_indexed(self, repo_key: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO indexed_repos (repo_key, indexed_at) VALUES (?, ?)",
                (repo_key, datetime.utcnow().isoformat()),
            )

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

    def seen_count(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) as c FROM seen_jobs").fetchone()
            return int(row["c"]) if row else 0

    # ── Jobs ─────────────────────────────────────────────────────────────

    def save_job(self, job: Job, score: float = 0.0, match_reasons: list = None):
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO jobs
                   (id, company, title, location, apply_url, source, date_posted,
                    is_remote, score, match_reasons, status, body)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    job.id, job.company, job.title, job.location,
                    job.apply_url, job.source, job.date_posted,
                    int(job.is_remote), score,
                    json.dumps(match_reasons or []),
                    job.body,
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

    def set_job_resume_pdf(self, job_id: str, resume_pdf_path: str):
        """Stores the tailored resume PDF path for this job."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE jobs SET resume_pdf_path = ?, resume_generated_at = datetime('now')
                   WHERE id = ?""",
                (resume_pdf_path, job_id),
            )

    # ── Job reads ─────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def get_by_status(self, status: str, limit: int = 200) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY score DESC LIMIT ?",
                (status, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_pending(self) -> list[dict]:
        return self.get_by_status("pending")

    def get_all(self, limit: int = 200) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Agent settings ────────────────────────────────────────────────────

    def _get_setting(self, key: str, default: bool) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM agent_settings WHERE key = ?",
                (key,),
            ).fetchone()
            if not row:
                return default
            return str(row["value"]).lower() == "true"

    def get_agent_enabled(self) -> bool:
        return self._get_setting("agent_enabled", True)

    def set_agent_enabled(self, enabled: bool):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agent_settings (key, value) VALUES (?, ?)",
                ("agent_enabled", "true" if enabled else "false"),
            )

    def get_auto_apply_enabled(self) -> bool:
        return self._get_setting("auto_apply_enabled", config.AUTO_APPLY_ENABLED)

    def set_auto_apply_enabled(self, enabled: bool):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agent_settings (key, value) VALUES (?, ?)",
                ("auto_apply_enabled", "true" if enabled else "false"),
            )

    def get_agent_settings(self) -> dict:
        return {
            "agent_enabled": self.get_agent_enabled(),
            "auto_apply_enabled": self.get_auto_apply_enabled(),
        }

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
