"""
store.py — Persistence store for dedup and application tracking.

Supports:
- SQLite (default; local `jobs.db`)
- MySQL (for deployments like Railway)
"""

import json
import sqlite3
import logging
import hmac
import hashlib
import secrets
import base64
from datetime import datetime, timedelta
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
                    owner_user_id INTEGER NOT NULL,
                    id TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    PRIMARY KEY (owner_user_id, id)
                );

                CREATE TABLE IF NOT EXISTS indexed_repos (
                    repo_key TEXT PRIMARY KEY,
                    indexed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    owner_user_id INTEGER NOT NULL,
                    id TEXT NOT NULL,
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
                    mail_action_token TEXT,
                    mail_action_expires_at TEXT,
                    mail_action_sent_at TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (owner_user_id, id)
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

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    notification_email TEXT NOT NULL DEFAULT '',
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
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
            if "mail_action_token" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN mail_action_token TEXT")
            if "mail_action_expires_at" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN mail_action_expires_at TEXT")
            if "mail_action_sent_at" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN mail_action_sent_at TEXT")

            # Initialize defaults.
            conn.execute(
                "INSERT OR IGNORE INTO agent_settings (key, value) VALUES (?, ?)",
                ("agent_enabled", "true"),
            )
            conn.execute(
                "INSERT OR IGNORE INTO agent_settings (key, value) VALUES (?, ?)",
                ("auto_apply_enabled", "true" if config.AUTO_APPLY_ENABLED else "false"),
            )

            self._migrate_users_is_admin_sqlite(conn)
            self._bootstrap_default_user_sqlite(conn)
            self._ensure_admin_flags_sqlite(conn)
            self._migrate_sqlite_job_ownership_composite(conn)

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
                    SELECT j.id, j.company, j.title, j.source, j.apply_url, j.body,
                           j.resume_pdf_path, j.cover_letter, j.status, j.notes
                    FROM jobs j
                    WHERE j.status = 'applied'
                      AND NOT EXISTS (
                          SELECT 1 FROM application_log al WHERE al.job_id = j.id
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

    def _migrate_users_is_admin_sqlite(self, conn):
        try:
            ucols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        except sqlite3.OperationalError:
            return
        if not ucols:
            return
        if "is_admin" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")

    def _ensure_admin_flags_sqlite(self, conn):
        """Exactly one admin when possible: env username, else lowest user id."""
        try:
            n = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()
            if not n or int(n["c"]) == 0:
                return
        except sqlite3.OperationalError:
            return

        admin_u = (config.DASHBOARD_USERNAME or "").strip()
        if admin_u:
            conn.execute("UPDATE users SET is_admin=0")
            conn.execute(
                "UPDATE users SET is_admin=1 WHERE username = ? COLLATE NOCASE",
                (admin_u,),
            )
            row = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_admin=1").fetchone()
            if not row or int(row["c"]) == 0:
                r2 = conn.execute("SELECT MIN(id) as mid FROM users").fetchone()
                if r2 and r2["mid"] is not None:
                    conn.execute("UPDATE users SET is_admin=1 WHERE id = ?", (int(r2["mid"]),))
        else:
            row = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_admin=1").fetchone()
            if not row or int(row["c"]) == 0:
                r2 = conn.execute("SELECT MIN(id) as mid FROM users").fetchone()
                if r2 and r2["mid"] is not None:
                    conn.execute("UPDATE users SET is_admin=1 WHERE id = ?", (int(r2["mid"]),))

    def _default_job_owner_id_sqlite(self, conn) -> int:
        row = conn.execute(
            "SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1"
        ).fetchone()
        if row and row["id"] is not None:
            return int(row["id"])
        row = conn.execute("SELECT MIN(id) as mid FROM users").fetchone()
        if row and row["mid"] is not None:
            return int(row["mid"])
        return 1

    def _migrate_sqlite_job_ownership_composite(self, conn):
        """
        Legacy DBs used a single global `jobs.id` primary key. Per-user queues use
        PRIMARY KEY (owner_user_id, id) plus scoped `seen_jobs`.
        """
        try:
            jcols = [r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        except sqlite3.OperationalError:
            return
        if not jcols or "owner_user_id" in jcols:
            return

        default_owner = self._default_job_owner_id_sqlite(conn)
        log.info("Migrating jobs/seen_jobs to per-user ownership (default owner_user_id=%s)", default_owner)

        conn.execute("ALTER TABLE jobs RENAME TO jobs_legacy_ownermig")
        conn.executescript(
            """
            CREATE TABLE jobs (
                owner_user_id INTEGER NOT NULL,
                id TEXT NOT NULL,
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
                mail_action_token TEXT,
                mail_action_expires_at TEXT,
                mail_action_sent_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (owner_user_id, id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO jobs (
                owner_user_id, id, company, title, location, apply_url, source, date_posted,
                is_remote, score, match_reasons, status, cover_letter, body,
                resume_pdf_path, resume_generated_at, applied_at, notes,
                mail_action_token, mail_action_expires_at, mail_action_sent_at, created_at
            )
            SELECT
                ?, id, company, title, location, apply_url, source, date_posted,
                is_remote, score, match_reasons, status, cover_letter, body,
                resume_pdf_path, resume_generated_at, applied_at, notes,
                mail_action_token, mail_action_expires_at, mail_action_sent_at, created_at
            FROM jobs_legacy_ownermig
            """,
            (default_owner,),
        )
        conn.execute("DROP TABLE jobs_legacy_ownermig")

        conn.execute("ALTER TABLE seen_jobs RENAME TO seen_jobs_legacy_ownermig")
        conn.executescript(
            """
            CREATE TABLE seen_jobs (
                owner_user_id INTEGER NOT NULL,
                id TEXT NOT NULL,
                seen_at TEXT NOT NULL,
                PRIMARY KEY (owner_user_id, id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO seen_jobs (owner_user_id, id, seen_at)
            SELECT ?, id, seen_at FROM seen_jobs_legacy_ownermig
            """,
            (default_owner,),
        )
        conn.execute("DROP TABLE seen_jobs_legacy_ownermig")

    def _bootstrap_default_user_sqlite(self, conn):
        from auth_password import hash_password

        row = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()
        if row and int(row["c"]) > 0:
            return
        u = (config.DASHBOARD_USERNAME or "").strip()
        p = config.DASHBOARD_PASSWORD or ""
        if not u or not p:
            return
        h = hash_password(p)
        em = (config.NOTIFY_EMAIL or "").strip()
        conn.execute(
            "INSERT INTO users (username, password_hash, notification_email, is_admin) VALUES (?,?,?,1)",
            (u, h, em),
        )

    def count_users(self) -> int:
        with self._conn() as conn:
            r = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()
            return int(r["c"]) if r else 0

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
            return dict(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (username.strip(),),
            ).fetchone()
            return dict(row) if row else None

    def verify_user_password(self, username: str, password: str) -> Optional[int]:
        row = self.get_user_by_username(username)
        if not row:
            return None
        from auth_password import verify_password

        if not verify_password(password, row.get("password_hash") or ""):
            return None
        return int(row["id"])

    def create_user(self, username: str, password: str, notification_email: str = "") -> int:
        from auth_password import hash_password

        u = username.strip()
        if not u or not password:
            raise ValueError("username and password required")
        h = hash_password(password)
        em = (notification_email or "").strip()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, notification_email, is_admin) VALUES (?,?,?,0)",
                (u, h, em),
            )
            return int(cur.lastrowid)

    def user_is_admin(self, user_id: int) -> bool:
        row = self.get_user_by_id(int(user_id))
        if not row:
            return False
        try:
            if int(row.get("is_admin") or 0) == 1:
                return True
        except (TypeError, ValueError):
            pass
        admin_u = (config.DASHBOARD_USERNAME or "").strip()
        if admin_u and str(row.get("username") or "").strip().lower() == admin_u.lower():
            return True
        return False

    def delete_user_by_id(self, user_id: int) -> bool:
        """Delete user row if they are not the admin. Returns True if a row was removed."""
        if self.user_is_admin(int(user_id)):
            return False
        with self._conn() as conn:
            conn.execute("DELETE FROM jobs WHERE owner_user_id = ?", (int(user_id),))
            cur = conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
            return cur.rowcount > 0

    def update_user_notification_email(self, user_id: int, notification_email: str) -> None:
        em = (notification_email or "").strip()
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET notification_email = ? WHERE id = ?",
                (em, int(user_id)),
            )

    def list_user_notification_emails(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT notification_email FROM users WHERE notification_email IS NOT NULL AND TRIM(notification_email) != ''"
            ).fetchall()
            return [str(r["notification_email"]).strip() for r in rows if r["notification_email"]]

    def resolve_scan_notification_recipients(self, web_user_id: Optional[int] = None) -> list[str]:
        if web_user_id is not None:
            row = self.get_user_by_id(int(web_user_id))
            if row:
                em = (row.get("notification_email") or "").strip()
                if em:
                    return [em]
            fe = (config.NOTIFY_EMAIL or "").strip()
            return [fe] if fe else []

        out: dict[str, None] = {}
        for em in self.list_user_notification_emails():
            if em:
                out.setdefault(em, None)
        fe = (config.NOTIFY_EMAIL or "").strip()
        if fe:
            out.setdefault(fe, None)
        return sorted(out.keys())

    def get_worker_owner_user_id(self) -> int:
        with self._conn() as conn:
            return self._default_job_owner_id_sqlite(conn)

    def resolve_scan_owner_user_id(self, notification_user_id: Optional[int]) -> int:
        if notification_user_id is not None:
            return int(notification_user_id)
        return self.get_worker_owner_user_id()

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

    # ── Dedup (per scanning user) ────────────────────────────────────────

    def is_seen(self, owner_user_id: int, job_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_jobs WHERE owner_user_id = ? AND id = ?",
                (int(owner_user_id), job_id),
            ).fetchone()
            return row is not None

    def mark_seen(self, owner_user_id: int, job_id: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_jobs (owner_user_id, id, seen_at) VALUES (?, ?, ?)",
                (int(owner_user_id), job_id, datetime.utcnow().isoformat()),
            )

    def seen_count(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) as c FROM seen_jobs").fetchone()
            return int(row["c"]) if row else 0

    # ── Jobs ─────────────────────────────────────────────────────────────

    def save_job(
        self,
        job: Job,
        score: float = 0.0,
        match_reasons: list = None,
        *,
        owner_user_id: int,
    ):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    owner_user_id, id, company, title, location, apply_url, source, date_posted,
                    is_remote, score, match_reasons, status, body
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(owner_user_id, id) DO UPDATE SET
                    company=excluded.company,
                    title=excluded.title,
                    location=excluded.location,
                    apply_url=excluded.apply_url,
                    source=excluded.source,
                    date_posted=excluded.date_posted,
                    is_remote=excluded.is_remote,
                    score=excluded.score,
                    match_reasons=excluded.match_reasons,
                    body=excluded.body,
                    status=CASE
                        WHEN jobs.status IN ('approved','applied','rejected','skipped') THEN jobs.status
                        ELSE 'pending'
                    END
                """,
                (
                    int(owner_user_id),
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

    # ── Email approval tokens ─────────────────────────────────────────────

    @staticmethod
    def _mail_signing_secret() -> str:
        secret = (config.MAIL_SIGNING_SECRET or "").strip()
        if secret:
            return secret
        # Backwards-compatible fallback (not ideal for production).
        return (config.SESSION_SECRET_KEY or "").strip()

    @staticmethod
    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    def issue_mail_action_token(self, owner_user_id: int, job_id: str) -> tuple[str, str]:
        """
        Returns (token, expires_at_iso).

        Token format: v2|<owner_user_id>|<job_id>|<exp_unix>|<rand>|<sig_b64url>
        """
        secret = self._mail_signing_secret()
        if not secret:
            raise RuntimeError("MAIL_SIGNING_SECRET/SESSION_SECRET_KEY missing; cannot sign mail action links.")

        exp = datetime.utcnow() + timedelta(hours=max(int(config.MAIL_ACTION_EXPIRY_HOURS), 1))
        exp_unix = str(int(exp.timestamp()))
        rand = secrets.token_urlsafe(16)
        ou = int(owner_user_id)
        payload = f"v2|{ou}|{job_id}|{exp_unix}|{rand}"
        sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
        token = f"{payload}|{self._b64url(sig)}"

        with self._conn() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET mail_action_token = ?,
                    mail_action_expires_at = ?
                WHERE owner_user_id = ? AND id = ?
                """,
                (token, exp.isoformat(), ou, job_id),
            )

        return token, exp.isoformat()

    def parse_and_verify_mail_action_token(self, token: str) -> Optional[tuple[Optional[int], str]]:
        """
        Returns (owner_user_id, job_id) on success.
        owner_user_id is None for legacy v1 tokens (caller must disambiguate).
        """
        secret = self._mail_signing_secret()
        if not secret or not token:
            return None
        parts = token.split("|")
        if len(parts) == 6 and parts[0] == "v2":
            try:
                owner_user_id = int(parts[1])
                job_id = parts[2]
                exp_unix_s = parts[3]
                rand = parts[4]
                sig_b64 = parts[5]
                exp_unix = int(exp_unix_s)
            except Exception:
                return None
            if int(datetime.utcnow().timestamp()) > exp_unix:
                return None
            payload = f"v2|{owner_user_id}|{job_id}|{exp_unix_s}|{rand}"
            expected_sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
            try:
                got_sig = base64.urlsafe_b64decode(sig_b64 + "==")
            except Exception:
                return None
            if not hmac.compare_digest(expected_sig, got_sig):
                return None
            return (owner_user_id, job_id)
        if len(parts) == 5 and parts[0] == "v1":
            job_id = parts[1]
            exp_unix_s = parts[2]
            rand = parts[3]
            sig_b64 = parts[4]
            try:
                exp_unix = int(exp_unix_s)
            except Exception:
                return None
            if int(datetime.utcnow().timestamp()) > exp_unix:
                return None
            payload = f"v1|{job_id}|{exp_unix_s}|{rand}"
            expected_sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
            try:
                got_sig = base64.urlsafe_b64decode(sig_b64 + "==")
            except Exception:
                return None
            if not hmac.compare_digest(expected_sig, got_sig):
                return None
            return (None, job_id)
        return None

    def verify_mail_action_token(self, job_id: str, token: str, owner_user_id: Optional[int] = None) -> bool:
        meta = self.parse_and_verify_mail_action_token(token)
        if not meta:
            return False
        tok_owner, tok_job = meta
        if tok_job != job_id:
            return False
        if tok_owner is not None:
            return owner_user_id is None or int(tok_owner) == int(owner_user_id)
        return True

    def get_mail_action_token_row(self, owner_user_id: int, job_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT mail_action_token, mail_action_expires_at FROM jobs WHERE owner_user_id = ? AND id = ?",
                (int(owner_user_id), job_id),
            ).fetchone()
            return dict(row) if row else None

    def mark_mail_action_sent(self, owner_user_id: int, job_id: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET mail_action_sent_at = datetime('now') WHERE owner_user_id = ? AND id = ?",
                (int(owner_user_id), job_id),
            )

    def get_mail_action_sent_at(self, owner_user_id: int, job_id: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT mail_action_sent_at FROM jobs WHERE owner_user_id = ? AND id = ?",
                (int(owner_user_id), job_id),
            ).fetchone()
            if not row:
                return None
            return row["mail_action_sent_at"]

    def update_status(
        self,
        owner_user_id: int,
        job_id: str,
        status: str,
        notes: str = "",
        cover_letter: str = "",
    ):
        """status: pending | approved | applied | rejected | skipped"""
        with self._conn() as conn:
            conn.execute(
                """UPDATE jobs SET status = ?, notes = ?,
                   cover_letter = COALESCE(NULLIF(?, ''), cover_letter),
                   applied_at = CASE WHEN ? = 'applied' THEN datetime('now') ELSE applied_at END
                   WHERE owner_user_id = ? AND id = ?""",
                (status, notes, cover_letter, status, int(owner_user_id), job_id),
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

    def set_job_resume_pdf(self, owner_user_id: int, job_id: str, resume_pdf_path: str):
        """Stores the tailored resume PDF path for this job."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE jobs SET resume_pdf_path = ?, resume_generated_at = datetime('now')
                   WHERE owner_user_id = ? AND id = ?""",
                (resume_pdf_path, int(owner_user_id), job_id),
            )

    # ── Job reads ─────────────────────────────────────────────────────────

    def get_job(self, owner_user_id: int, job_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE owner_user_id = ? AND id = ?",
                (int(owner_user_id), job_id),
            ).fetchone()
            return dict(row) if row else None

    def find_jobs_by_canonical_id(self, job_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_by_status(
        self,
        status: str,
        limit: int = 200,
        *,
        owner_user_id: Optional[int] = None,
        viewer_is_admin: bool = False,
    ) -> list[dict]:
        with self._conn() as conn:
            if viewer_is_admin:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY score DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            elif owner_user_id is not None:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = ? AND owner_user_id = ? ORDER BY score DESC LIMIT ?",
                    (status, int(owner_user_id), limit),
                ).fetchall()
            else:
                rows = []
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

    def stats(self, *, owner_user_id: Optional[int] = None, viewer_is_admin: bool = False) -> dict:
        with self._conn() as conn:
            if viewer_is_admin:
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) as applied,
                        SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
                        SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END) as skipped,
                        ROUND(AVG(score), 2) as avg_score
                    FROM jobs
                    """
                ).fetchone()
            elif owner_user_id is not None:
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) as applied,
                        SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
                        SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END) as skipped,
                        ROUND(AVG(score), 2) as avg_score
                    FROM jobs
                    WHERE owner_user_id = ?
                    """,
                    (int(owner_user_id),),
                ).fetchone()
            else:
                row = None
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
                    owner_user_id INT NOT NULL,
                    id VARCHAR(64) NOT NULL,
                    seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (owner_user_id, id)
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
                    owner_user_id INT NOT NULL,
                    id VARCHAR(64) NOT NULL,
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
                    mail_action_token TEXT NULL,
                    mail_action_expires_at DATETIME NULL,
                    mail_action_sent_at DATETIME NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (owner_user_id, id)
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

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(255) NOT NULL UNIQUE,
                    password_hash VARCHAR(512) NOT NULL,
                    notification_email VARCHAR(320) NOT NULL DEFAULT '',
                    is_admin TINYINT(1) NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            self._migrate_users_is_admin_mysql(cur)
            self._bootstrap_default_user_mysql(cur)
            self._ensure_admin_flags_mysql(cur)
            self._migrate_mysql_job_ownership_composite(cur)

            # Migrations for older MySQL schemas.
            def _add_col(sql: str):
                try:
                    cur.execute(sql)
                except Exception:
                    # Duplicate column name, etc.
                    pass

            _add_col("ALTER TABLE jobs ADD COLUMN mail_action_token TEXT NULL")
            _add_col("ALTER TABLE jobs ADD COLUMN mail_action_expires_at DATETIME NULL")
            _add_col("ALTER TABLE jobs ADD COLUMN mail_action_sent_at DATETIME NULL")

    def _default_job_owner_id_mysql_inline(self, cur) -> int:
        cur.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
        if row and row.get("id") is not None:
            return int(row["id"])
        cur.execute("SELECT MIN(id) as mid FROM users")
        row = cur.fetchone()
        if row and row.get("mid") is not None:
            return int(row["mid"])
        return 1

    def _migrate_mysql_job_ownership_composite(self, cur):
        try:
            cur.execute(
                """
                SELECT COUNT(*) as c FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'jobs' AND COLUMN_NAME = 'owner_user_id'
                """
            )
            row = cur.fetchone()
            if row and int(row["c"]) > 0:
                return
        except Exception:
            return

        default_owner = self._default_job_owner_id_mysql_inline(cur)
        log.info("MySQL: migrating jobs/seen_jobs to per-user ownership (default owner_user_id=%s)", default_owner)

        cur.execute("ALTER TABLE jobs ADD COLUMN owner_user_id INT NULL")
        cur.execute("UPDATE jobs SET owner_user_id = %s WHERE owner_user_id IS NULL", (default_owner,))
        cur.execute("ALTER TABLE jobs MODIFY COLUMN owner_user_id INT NOT NULL")
        cur.execute("ALTER TABLE jobs DROP PRIMARY KEY")
        cur.execute("ALTER TABLE jobs ADD PRIMARY KEY (owner_user_id, id)")

        cur.execute("ALTER TABLE seen_jobs ADD COLUMN owner_user_id INT NULL")
        cur.execute("UPDATE seen_jobs SET owner_user_id = %s WHERE owner_user_id IS NULL", (default_owner,))
        cur.execute("ALTER TABLE seen_jobs MODIFY COLUMN owner_user_id INT NOT NULL")
        cur.execute("ALTER TABLE seen_jobs DROP PRIMARY KEY")
        cur.execute("ALTER TABLE seen_jobs ADD PRIMARY KEY (owner_user_id, id)")

    def _migrate_users_is_admin_mysql(self, cur):
        try:
            cur.execute(
                """
                SELECT COUNT(*) as c FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'is_admin'
                """
            )
            row = cur.fetchone()
            if row and int(row["c"]) == 0:
                cur.execute("ALTER TABLE users ADD COLUMN is_admin TINYINT(1) NOT NULL DEFAULT 0")
        except Exception:
            try:
                cur.execute("ALTER TABLE users ADD COLUMN is_admin TINYINT(1) NOT NULL DEFAULT 0")
            except Exception:
                pass

    def _ensure_admin_flags_mysql(self, cur):
        cur.execute("SELECT COUNT(*) as c FROM users")
        row = cur.fetchone()
        if not row or int(row["c"]) == 0:
            return

        admin_u = (config.DASHBOARD_USERNAME or "").strip()
        if admin_u:
            cur.execute("UPDATE users SET is_admin=0")
            cur.execute(
                "UPDATE users SET is_admin=1 WHERE LOWER(username) = LOWER(%s)",
                (admin_u,),
            )
            cur.execute("SELECT COUNT(*) as c FROM users WHERE is_admin=1")
            row2 = cur.fetchone()
            if not row2 or int(row2["c"]) == 0:
                cur.execute("UPDATE users SET is_admin=1 ORDER BY id ASC LIMIT 1")
        else:
            cur.execute("SELECT COUNT(*) as c FROM users WHERE is_admin=1")
            row2 = cur.fetchone()
            if not row2 or int(row2["c"]) == 0:
                cur.execute("UPDATE users SET is_admin=1 ORDER BY id ASC LIMIT 1")

    def _bootstrap_default_user_mysql(self, cur):
        from auth_password import hash_password

        cur.execute("SELECT COUNT(*) as c FROM users")
        row = cur.fetchone()
        if row and int(row["c"]) > 0:
            return
        u = (config.DASHBOARD_USERNAME or "").strip()
        p = config.DASHBOARD_PASSWORD or ""
        if not u or not p:
            return
        h = hash_password(p)
        em = (config.NOTIFY_EMAIL or "").strip()
        cur.execute(
            "INSERT INTO users (username, password_hash, notification_email, is_admin) VALUES (%s,%s,%s,1)",
            (u, h, em),
        )

    def count_users(self) -> int:
        with self._conn() as cur:
            cur.execute("SELECT COUNT(*) as c FROM users")
            row = cur.fetchone()
            return int(row["c"]) if row else 0

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        with self._conn() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s LIMIT 1", (int(user_id),))
            row = cur.fetchone()
            return self._normalize_row(dict(row)) if row else None

    def get_user_by_username(self, username: str) -> Optional[dict]:
        with self._conn() as cur:
            cur.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1", (username.strip(),))
            row = cur.fetchone()
            return self._normalize_row(dict(row)) if row else None

    def verify_user_password(self, username: str, password: str) -> Optional[int]:
        row = self.get_user_by_username(username)
        if not row:
            return None
        from auth_password import verify_password

        if not verify_password(password, row.get("password_hash") or ""):
            return None
        return int(row["id"])

    def create_user(self, username: str, password: str, notification_email: str = "") -> int:
        from auth_password import hash_password

        u = username.strip()
        if not u or not password:
            raise ValueError("username and password required")
        h = hash_password(password)
        em = (notification_email or "").strip()
        with self._conn() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, notification_email, is_admin) VALUES (%s,%s,%s,0)",
                (u, h, em),
            )
            return int(cur.lastrowid)

    def user_is_admin(self, user_id: int) -> bool:
        row = self.get_user_by_id(int(user_id))
        if not row:
            return False
        try:
            v = row.get("is_admin")
            if v is True or int(v) == 1:
                return True
        except (TypeError, ValueError):
            pass
        admin_u = (config.DASHBOARD_USERNAME or "").strip()
        if admin_u and str(row.get("username") or "").strip().lower() == admin_u.lower():
            return True
        return False

    def delete_user_by_id(self, user_id: int) -> bool:
        if self.user_is_admin(int(user_id)):
            return False
        with self._conn() as cur:
            cur.execute("DELETE FROM jobs WHERE owner_user_id = %s", (int(user_id),))
            cur.execute("DELETE FROM users WHERE id = %s", (int(user_id),))
            return cur.rowcount > 0

    def update_user_notification_email(self, user_id: int, notification_email: str) -> None:
        em = (notification_email or "").strip()
        with self._conn() as cur:
            cur.execute(
                "UPDATE users SET notification_email = %s WHERE id = %s",
                (em, int(user_id)),
            )

    def list_user_notification_emails(self) -> list[str]:
        with self._conn() as cur:
            cur.execute(
                "SELECT DISTINCT notification_email FROM users WHERE notification_email IS NOT NULL AND TRIM(notification_email) != ''"
            )
            rows = cur.fetchall() or []
            return [str(r["notification_email"]).strip() for r in rows if r.get("notification_email")]

    def resolve_scan_notification_recipients(self, web_user_id: Optional[int] = None) -> list[str]:
        if web_user_id is not None:
            row = self.get_user_by_id(int(web_user_id))
            if row:
                em = (row.get("notification_email") or "").strip()
                if em:
                    return [em]
            fe = (config.NOTIFY_EMAIL or "").strip()
            return [fe] if fe else []

        out: dict[str, None] = {}
        for em in self.list_user_notification_emails():
            if em:
                out.setdefault(em, None)
        fe = (config.NOTIFY_EMAIL or "").strip()
        if fe:
            out.setdefault(fe, None)
        return sorted(out.keys())

    def get_worker_owner_user_id(self) -> int:
        with self._conn() as cur:
            return self._default_job_owner_id_mysql_inline(cur)

    def resolve_scan_owner_user_id(self, notification_user_id: Optional[int]) -> int:
        if notification_user_id is not None:
            return int(notification_user_id)
        return self.get_worker_owner_user_id()

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

    def is_seen(self, owner_user_id: int, job_id: str) -> bool:
        with self._conn() as cur:
            cur.execute(
                "SELECT 1 FROM seen_jobs WHERE owner_user_id = %s AND id = %s LIMIT 1",
                (int(owner_user_id), job_id),
            )
            row = cur.fetchone()
            return row is not None

    def mark_seen(self, owner_user_id: int, job_id: str):
        with self._conn() as cur:
            cur.execute(
                "INSERT IGNORE INTO seen_jobs (owner_user_id, id, seen_at) VALUES (%s, %s, NOW())",
                (int(owner_user_id), job_id),
            )

    def seen_count(self) -> int:
        with self._conn() as cur:
            cur.execute("SELECT COUNT(*) as c FROM seen_jobs")
            row = cur.fetchone()
            return int(row["c"]) if row else 0

    # ── Jobs ────────────────────────────────────────────────────────────
    def save_job(
        self,
        job: Job,
        score: float = 0.0,
        match_reasons: list = None,
        *,
        owner_user_id: int,
    ):
        with self._conn() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                    owner_user_id, id, company, title, location, apply_url, source, date_posted,
                    is_remote, score, match_reasons, status, body
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
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
                    status=IF(jobs.status IN ('approved','applied','rejected','skipped'), jobs.status, 'pending'),
                    body=VALUES(body)
                """,
                (
                    int(owner_user_id),
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

    # ── Email approval tokens ─────────────────────────────────────────────
    # (Duplicated from SQLiteJobStore to keep MySQLJobStore self-contained.)

    @staticmethod
    def _mail_signing_secret() -> str:
        secret = (config.MAIL_SIGNING_SECRET or "").strip()
        if secret:
            return secret
        return (config.SESSION_SECRET_KEY or "").strip()

    @staticmethod
    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    def issue_mail_action_token(self, owner_user_id: int, job_id: str) -> tuple[str, str]:
        secret = self._mail_signing_secret()
        if not secret:
            raise RuntimeError("MAIL_SIGNING_SECRET/SESSION_SECRET_KEY missing; cannot sign mail action links.")

        exp = datetime.utcnow() + timedelta(hours=max(int(config.MAIL_ACTION_EXPIRY_HOURS), 1))
        exp_unix = str(int(exp.timestamp()))
        rand = secrets.token_urlsafe(16)
        ou = int(owner_user_id)
        payload = f"v2|{ou}|{job_id}|{exp_unix}|{rand}"
        sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
        token = f"{payload}|{self._b64url(sig)}"

        with self._conn() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET mail_action_token = %s,
                    mail_action_expires_at = %s
                WHERE owner_user_id = %s AND id = %s
                """,
                (token, exp, ou, job_id),
            )

        return token, exp.isoformat()

    def parse_and_verify_mail_action_token(self, token: str) -> Optional[tuple[Optional[int], str]]:
        secret = self._mail_signing_secret()
        if not secret or not token:
            return None
        parts = token.split("|")
        if len(parts) == 6 and parts[0] == "v2":
            try:
                owner_user_id = int(parts[1])
                job_id = parts[2]
                exp_unix_s = parts[3]
                rand = parts[4]
                sig_b64 = parts[5]
                exp_unix = int(exp_unix_s)
            except Exception:
                return None
            if int(datetime.utcnow().timestamp()) > exp_unix:
                return None
            payload = f"v2|{owner_user_id}|{job_id}|{exp_unix_s}|{rand}"
            expected_sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
            try:
                got_sig = base64.urlsafe_b64decode(sig_b64 + "==")
            except Exception:
                return None
            if not hmac.compare_digest(expected_sig, got_sig):
                return None
            return (owner_user_id, job_id)
        if len(parts) == 5 and parts[0] == "v1":
            job_id = parts[1]
            exp_unix_s = parts[2]
            rand = parts[3]
            sig_b64 = parts[4]
            try:
                exp_unix = int(exp_unix_s)
            except Exception:
                return None
            if int(datetime.utcnow().timestamp()) > exp_unix:
                return None
            payload = f"v1|{job_id}|{exp_unix_s}|{rand}"
            expected_sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
            try:
                got_sig = base64.urlsafe_b64decode(sig_b64 + "==")
            except Exception:
                return None
            if not hmac.compare_digest(expected_sig, got_sig):
                return None
            return (None, job_id)
        return None

    def verify_mail_action_token(self, job_id: str, token: str, owner_user_id: Optional[int] = None) -> bool:
        meta = self.parse_and_verify_mail_action_token(token)
        if not meta:
            return False
        tok_owner, tok_job = meta
        if tok_job != job_id:
            return False
        if tok_owner is not None:
            return owner_user_id is None or int(tok_owner) == int(owner_user_id)
        return True

    def get_mail_action_token_row(self, owner_user_id: int, job_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as cur:
            cur.execute(
                "SELECT mail_action_token, mail_action_expires_at FROM jobs WHERE owner_user_id = %s AND id = %s LIMIT 1",
                (int(owner_user_id), job_id),
            )
            row = cur.fetchone()
            return self._normalize_row(dict(row)) if row else None

    def mark_mail_action_sent(self, owner_user_id: int, job_id: str):
        with self._conn() as cur:
            cur.execute(
                "UPDATE jobs SET mail_action_sent_at = NOW() WHERE owner_user_id = %s AND id = %s",
                (int(owner_user_id), job_id),
            )

    def get_mail_action_sent_at(self, owner_user_id: int, job_id: str) -> Optional[str]:
        with self._conn() as cur:
            cur.execute(
                "SELECT mail_action_sent_at FROM jobs WHERE owner_user_id = %s AND id = %s LIMIT 1",
                (int(owner_user_id), job_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            val = row.get("mail_action_sent_at")
            if isinstance(val, datetime):
                return val.isoformat()
            return str(val) if val is not None else None

    def update_status(
        self,
        owner_user_id: int,
        job_id: str,
        status: str,
        notes: str = "",
        cover_letter: str = "",
    ):
        with self._conn() as cur:
            cur.execute(
                """
                UPDATE jobs SET
                    status = %s,
                    notes = %s,
                    cover_letter = COALESCE(NULLIF(%s, ''), cover_letter),
                    applied_at = CASE WHEN %s = 'applied' THEN NOW() ELSE applied_at END
                WHERE owner_user_id = %s AND id = %s
                """,
                (status, notes, cover_letter, status, int(owner_user_id), job_id),
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

    def set_job_resume_pdf(self, owner_user_id: int, job_id: str, resume_pdf_path: str):
        with self._conn() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET resume_pdf_path = %s,
                    resume_generated_at = NOW()
                WHERE owner_user_id = %s AND id = %s
                """,
                (resume_pdf_path, int(owner_user_id), job_id),
            )

    # ── Job reads ───────────────────────────────────────────────────────
    def get_job(self, owner_user_id: int, job_id: str) -> Optional[dict]:
        with self._conn() as cur:
            cur.execute(
                "SELECT * FROM jobs WHERE owner_user_id = %s AND id = %s",
                (int(owner_user_id), job_id),
            )
            row = cur.fetchone()
            return self._normalize_row(dict(row)) if row else None

    def find_jobs_by_canonical_id(self, job_id: str) -> list[dict]:
        with self._conn() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            rows = cur.fetchall() or []
            return [self._normalize_row(dict(r)) for r in rows]

    def get_by_status(
        self,
        status: str,
        limit: int = 200,
        *,
        owner_user_id: Optional[int] = None,
        viewer_is_admin: bool = False,
    ) -> list[dict]:
        with self._conn() as cur:
            if viewer_is_admin:
                cur.execute(
                    "SELECT * FROM jobs WHERE status = %s ORDER BY score DESC LIMIT %s",
                    (status, int(limit)),
                )
            elif owner_user_id is not None:
                cur.execute(
                    "SELECT * FROM jobs WHERE status = %s AND owner_user_id = %s ORDER BY score DESC LIMIT %s",
                    (status, int(owner_user_id), int(limit)),
                )
            else:
                cur.execute("SELECT * FROM jobs WHERE 1=0", ())
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

    def stats(self, *, owner_user_id: Optional[int] = None, viewer_is_admin: bool = False) -> dict:
        with self._conn() as cur:
            if viewer_is_admin:
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
            elif owner_user_id is not None:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) as applied,
                        SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
                        SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END) as skipped,
                        ROUND(AVG(score), 2) as avg_score
                    FROM jobs
                    WHERE owner_user_id = %s
                    """,
                    (int(owner_user_id),),
                )
            else:
                return {}
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
