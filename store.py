"""
store.py — Persistence store for dedup and application tracking.

Supports:
- SQLite (default; local `jobs.db`)
- MySQL (for deployments like Railway)
"""

import json
import sqlite3
import logging
from datetime import datetime
from contextlib import contextmanager
from typing import Any, Optional
from decimal import Decimal

from config import config
from parser import Job

log = logging.getLogger(__name__)


class SQLiteJobStore:
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

                -- Immutable-ish audit trail of what we tried to do on each job.
                -- This is separate from the mutable `jobs` row so we can keep
                -- a history of applied attempts (including which resume was used).
                CREATE TABLE IF NOT EXISTS application_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    company TEXT,
                    title TEXT,
                    source TEXT,
                    apply_url TEXT,
                    job_body TEXT,
                    resume_pdf_path TEXT,
                    cover_letter TEXT,
                    method TEXT,
                    status TEXT,
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

            # Backfill the application log for jobs already marked as `applied`
            # before this feature was introduced.
            try:
                row = conn.execute("SELECT COUNT(*) as c FROM application_log").fetchone()
                already_logged = int(row["c"]) if row else 0
            except Exception:
                already_logged = 0

            # Backfill only jobs that are marked `applied` but missing from `application_log`.
            missing_rows = conn.execute("""
                SELECT COUNT(*) as c
                FROM jobs j
                WHERE j.status = 'applied'
                  AND NOT EXISTS (
                      SELECT 1 FROM application_log al WHERE al.job_id = j.id
                  )
            """).fetchone()

            if missing_rows and int(missing_rows["c"]) > 0:
                applied_rows = conn.execute("""
                    SELECT id, company, title, source, apply_url, body,
                           resume_pdf_path, cover_letter, status, notes
                    FROM jobs
                    WHERE status = 'applied'
                      AND NOT EXISTS (
                          SELECT 1 FROM application_log al WHERE al.job_id = jobs.id
                      )
                """).fetchall()

                for r in applied_rows:
                    # Best-effort method extraction from notes.
                    notes = r["notes"] or ""
                    method = ""
                    if "Applied via " in notes:
                        method = notes.split("Applied via ", 1)[-1].strip()

                    conn.execute(
                        """
                        INSERT INTO application_log (
                            job_id, company, title, source, apply_url,
                            job_body, resume_pdf_path, cover_letter,
                            method, status, notes
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            r["id"],
                            r["company"] or "",
                            r["title"] or "",
                            r["source"] or "",
                            r["apply_url"] or "",
                            r["body"] or "",
                            r["resume_pdf_path"] or "",
                            r["cover_letter"] or "",
                            method,
                            "applied",
                            notes,
                        ),
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

    def log_application(
        self,
        job_id: str,
        *,
        company: str = "",
        title: str = "",
        source: str = "",
        apply_url: str = "",
        job_body: str = "",
        resume_pdf_path: str = "",
        cover_letter: str = "",
        method: str = "",
        status: str = "",
        notes: str = "",
    ):
        """Append a record of an apply attempt."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO application_log (
                    job_id, company, title, source, apply_url,
                    job_body, resume_pdf_path, cover_letter,
                    method, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    company,
                    title,
                    source,
                    apply_url,
                    job_body,
                    resume_pdf_path,
                    cover_letter,
                    method,
                    status,
                    notes,
                ),
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


class MySQLJobStore:
    """
    MySQL-backed store.

    Note: We intentionally keep the same return shapes as SQLiteJobStore
    (plain dicts with JSON fields already stored as strings).
    """

    def __init__(self):
        self._init_db()

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        for k, v in list(row.items()):
            if isinstance(v, datetime):
                row[k] = v.isoformat()
            elif isinstance(v, Decimal):
                row[k] = float(v)
        return row

    @contextmanager
    def _conn(self):
        import mysql.connector  # lazy import: only required when DB_BACKEND=mysql

        if not (config.MYSQL_HOST and config.MYSQL_USER and config.MYSQL_PASSWORD and config.MYSQL_DATABASE):
            raise RuntimeError(
                "MySQL is enabled but MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, and MYSQL_DATABASE are not all set."
            )

        conn = mysql.connector.connect(
            host=config.MYSQL_HOST,
            port=config.MYSQL_PORT,
            user=config.MYSQL_USER,
            password=config.MYSQL_PASSWORD,
            database=config.MYSQL_DATABASE,
            autocommit=False,
        )
        cursor = conn.cursor(dictionary=True)
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    def _init_db(self):
        # Create schema if missing.
        # Column types intentionally favor compatibility with the existing SQLite code paths.
        with self._conn() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_jobs (
                    id VARCHAR(64) PRIMARY KEY,
                    seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS indexed_repos (
                    repo_key VARCHAR(255) PRIMARY KEY,
                    indexed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id VARCHAR(64) PRIMARY KEY,
                    company TEXT,
                    title TEXT,
                    location TEXT,
                    apply_url TEXT,
                    source TEXT,
                    date_posted TEXT,
                    is_remote TINYINT,
                    score DOUBLE,
                    match_reasons TEXT,
                    status VARCHAR(32) DEFAULT 'pending',
                    cover_letter MEDIUMTEXT,
                    body MEDIUMTEXT,
                    resume_pdf_path TEXT,
                    resume_generated_at DATETIME NULL,
                    applied_at DATETIME NULL,
                    notes MEDIUMTEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS application_log (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    job_id VARCHAR(64) NOT NULL,
                    company TEXT,
                    title TEXT,
                    source TEXT,
                    apply_url TEXT,
                    job_body MEDIUMTEXT,
                    resume_pdf_path TEXT,
                    cover_letter MEDIUMTEXT,
                    method VARCHAR(64),
                    status VARCHAR(32),
                    notes MEDIUMTEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_settings (
                    `key` VARCHAR(128) PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

            # Initialize defaults (safe on existing DB).
            cur.execute(
                "INSERT INTO agent_settings (`key`, value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE value=VALUES(value)",
                ("agent_enabled", "true"),
            )
            cur.execute(
                "INSERT INTO agent_settings (`key`, value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE value=VALUES(value)",
                ("auto_apply_enabled", "true" if config.AUTO_APPLY_ENABLED else "false"),
            )

    # ── Repo indexing / dedup ────────────────────────────────────────────
    def is_repo_indexed(self, repo_key: str) -> bool:
        with self._conn() as cur:
            cur.execute("SELECT 1 FROM indexed_repos WHERE repo_key = %s LIMIT 1", (repo_key,))
            row = cur.fetchone()
            return row is not None

    def mark_repo_indexed(self, repo_key: str):
        with self._conn() as cur:
            cur.execute(
                """
                INSERT INTO indexed_repos (repo_key, indexed_at)
                VALUES (%s, NOW())
                ON DUPLICATE KEY UPDATE indexed_at = NOW()
                """,
                (repo_key,),
            )

    def is_seen(self, job_id: str) -> bool:
        with self._conn() as cur:
            cur.execute("SELECT 1 FROM seen_jobs WHERE id = %s LIMIT 1", (job_id,))
            row = cur.fetchone()
            return row is not None

    def mark_seen(self, job_id: str):
        with self._conn() as cur:
            cur.execute(
                "INSERT IGNORE INTO seen_jobs (id, seen_at) VALUES (%s, NOW())",
                (job_id,),
            )

    def seen_count(self) -> int:
        with self._conn() as cur:
            cur.execute("SELECT COUNT(*) as c FROM seen_jobs")
            row = cur.fetchone()
            return int(row["c"]) if row else 0

    # ── Jobs ────────────────────────────────────────────────────────────
    def save_job(self, job: Job, score: float = 0.0, match_reasons: list = None):
        with self._conn() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                    id, company, title, location, apply_url, source, date_posted,
                    is_remote, score, match_reasons, status, body
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                ON DUPLICATE KEY UPDATE
                    company=VALUES(company),
                    title=VALUES(title),
                    location=VALUES(location),
                    apply_url=VALUES(apply_url),
                    source=VALUES(source),
                    date_posted=VALUES(date_posted),
                    is_remote=VALUES(is_remote),
                    score=VALUES(score),
                    match_reasons=VALUES(match_reasons),
                    status='pending',
                    body=VALUES(body)
                """,
                (
                    job.id,
                    job.company,
                    job.title,
                    job.location,
                    job.apply_url,
                    job.source,
                    job.date_posted,
                    int(job.is_remote),
                    score,
                    json.dumps(match_reasons or []),
                    job.body,
                ),
            )

    def update_status(self, job_id: str, status: str, notes: str = "", cover_letter: str = ""):
        with self._conn() as cur:
            cur.execute(
                """
                UPDATE jobs SET
                    status = %s,
                    notes = %s,
                    cover_letter = COALESCE(NULLIF(%s, ''), cover_letter),
                    applied_at = CASE WHEN %s = 'applied' THEN NOW() ELSE applied_at END
                WHERE id = %s
                """,
                (status, notes, cover_letter, status, job_id),
            )

    def log_application(
        self,
        job_id: str,
        *,
        company: str = "",
        title: str = "",
        source: str = "",
        apply_url: str = "",
        job_body: str = "",
        resume_pdf_path: str = "",
        cover_letter: str = "",
        method: str = "",
        status: str = "",
        notes: str = "",
    ):
        with self._conn() as cur:
            cur.execute(
                """
                INSERT INTO application_log (
                    job_id, company, title, source, apply_url,
                    job_body, resume_pdf_path, cover_letter,
                    method, status, notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    job_id,
                    company,
                    title,
                    source,
                    apply_url,
                    job_body,
                    resume_pdf_path,
                    cover_letter,
                    method,
                    status,
                    notes,
                ),
            )

    def set_job_resume_pdf(self, job_id: str, resume_pdf_path: str):
        with self._conn() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET resume_pdf_path = %s,
                    resume_generated_at = NOW()
                WHERE id = %s
                """,
                (resume_pdf_path, job_id),
            )

    # ── Job reads ───────────────────────────────────────────────────────
    def get_job(self, job_id: str) -> Optional[dict]:
        with self._conn() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            return self._normalize_row(dict(row)) if row else None

    def get_by_status(self, status: str, limit: int = 200) -> list[dict]:
        with self._conn() as cur:
            cur.execute(
                "SELECT * FROM jobs WHERE status = %s ORDER BY score DESC LIMIT %s",
                (status, int(limit)),
            )
            rows = cur.fetchall() or []
            return [self._normalize_row(dict(r)) for r in rows]

    def get_pending(self) -> list[dict]:
        return self.get_by_status("pending")

    def get_all(self, limit: int = 200) -> list[dict]:
        with self._conn() as cur:
            cur.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT %s", (int(limit),))
            rows = cur.fetchall() or []
            return [self._normalize_row(dict(r)) for r in rows]

    # ── Agent settings ───────────────────────────────────────────────────
    def _get_setting(self, key: str, default: bool) -> bool:
        with self._conn() as cur:
            cur.execute("SELECT value FROM agent_settings WHERE `key` = %s LIMIT 1", (key,))
            row = cur.fetchone()
            if not row:
                return default
            return str(row["value"]).lower() == "true"

    def get_agent_enabled(self) -> bool:
        return self._get_setting("agent_enabled", True)

    def set_agent_enabled(self, enabled: bool):
        with self._conn() as cur:
            cur.execute(
                """
                INSERT INTO agent_settings (`key`, value)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE value=VALUES(value)
                """,
                ("agent_enabled", "true" if enabled else "false"),
            )

    def get_auto_apply_enabled(self) -> bool:
        return self._get_setting("auto_apply_enabled", config.AUTO_APPLY_ENABLED)

    def set_auto_apply_enabled(self, enabled: bool):
        with self._conn() as cur:
            cur.execute(
                """
                INSERT INTO agent_settings (`key`, value)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE value=VALUES(value)
                """,
                ("auto_apply_enabled", "true" if enabled else "false"),
            )

    def get_agent_settings(self) -> dict:
        return {
            "agent_enabled": self.get_agent_enabled(),
            "auto_apply_enabled": self.get_auto_apply_enabled(),
        }

    def stats(self) -> dict:
        with self._conn() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) as applied,
                    SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END) as skipped,
                    ROUND(AVG(score), 2) as avg_score
                FROM jobs
                """
            )
            row = cur.fetchone()
            return self._normalize_row(dict(row)) if row else {}


class JobStore:
    """
    Storage wrapper that selects SQLite or MySQL at runtime.
    """

    def __init__(self, db_path: str = None):
        backend = (config.DB_BACKEND or "sqlite").lower()
        if backend == "mysql":
            self._impl = MySQLJobStore()
        else:
            self._impl = SQLiteJobStore(db_path=db_path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._impl, name)
