"""
One-time migration: SQLite `jobs.db` -> MySQL.

Usage (preferred): run with Railway MySQL env vars configured:

  python scripts/migrate_sqlite_to_mysql.py --sqlite-path jobs.db --force

By default we refuse to migrate if the target MySQL DB already has jobs.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime as dt
from typing import Any, Optional

import mysql.connector

from config import config
from store import SQLiteJobStore, MySQLJobStore


def normalize_dt(val: Any) -> Optional[str]:
    """
    Convert SQLite TEXT timestamps into a MySQL-friendly format.

    SQLite writes ISO strings like `2026-03-18T12:34:56.789012`.
    MySQL accepts `YYYY-MM-DD HH:MM:SS` nicely.
    """
    if val is None:
        return None
    if isinstance(val, dt):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    if not isinstance(val, str):
        return str(val)

    s = val.strip()
    if not s:
        return None

    # Handle ISO8601 with "T" and optional microseconds.
    try:
        # fromisoformat supports both " " and "T".
        d = dt.fromisoformat(s.replace("Z", ""))
        return d.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        # Fall back: strip microseconds if present.
        if "T" in s:
            s = s.replace("T", " ")
        if "." in s:
            s = s.split(".", 1)[0]
        return s


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--sqlite-path", default=config.DB_PATH, help="Path to local SQLite jobs.db")
    p.add_argument("--force", action="store_true", help="Allow migration even if target has existing jobs")
    return p.parse_args()


def main():
    args = parse_args()

    # Ensure both stores initialize schema; this provides predictable table existence.
    # - SQLiteJobStore: runs its migration/backfill logic (harmless for export)
    # - MySQLJobStore: creates the schema if missing
    SQLiteJobStore(db_path=args.sqlite_path)
    MySQLJobStore()

    # Connect directly to MySQL for bulk inserts.
    conn = mysql.connector.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        autocommit=False,
    )
    cur = conn.cursor()

    try:
        cur.execute("SELECT COUNT(*) FROM jobs")
        jobs_count = int(cur.fetchone()[0])
        if jobs_count > 0 and not args.force:
            raise RuntimeError(
                f"Target MySQL already has {jobs_count} jobs. Refusing to migrate. Use --force to override."
            )

        # Export all rows from SQLite.
        sconn = sqlite3.connect(args.sqlite_path)
        sconn.row_factory = sqlite3.Row
        scur = sconn.cursor()

        def export_all(query: str):
            return [dict(r) for r in scur.execute(query).fetchall()]

        def sqlite_default_owner_uid() -> int:
            row = scur.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1").fetchone()
            if row and row["id"] is not None:
                return int(row["id"])
            row = scur.execute("SELECT MIN(id) as mid FROM users").fetchone()
            if row and row["mid"] is not None:
                return int(row["mid"])
            return 1

        default_owner = sqlite_default_owner_uid()

        seen_jobs = export_all("SELECT * FROM seen_jobs")
        for r in seen_jobs:
            if "owner_user_id" not in r or r.get("owner_user_id") is None:
                r["owner_user_id"] = default_owner

        indexed_repos = export_all("SELECT repo_key, indexed_at FROM indexed_repos")
        jobs = export_all("SELECT * FROM jobs")
        for r in jobs:
            if "owner_user_id" not in r or r.get("owner_user_id") is None:
                r["owner_user_id"] = default_owner
        application_log = export_all("SELECT * FROM application_log")
        agent_settings = export_all("SELECT `key`, value FROM agent_settings")

        # Insert into MySQL.
        if seen_jobs:
            cur.executemany(
                "INSERT INTO seen_jobs (owner_user_id, id, seen_at) VALUES (%s, %s, %s)",
                [(int(r["owner_user_id"]), r["id"], normalize_dt(r["seen_at"])) for r in seen_jobs],
            )

        if indexed_repos:
            cur.executemany(
                "INSERT INTO indexed_repos (repo_key, indexed_at) VALUES (%s, %s)",
                [(r["repo_key"], normalize_dt(r["indexed_at"])) for r in indexed_repos],
            )

        if jobs:
            # Keep column order aligned with MySQL schema (composite PK owner_user_id + id).
            cur.executemany(
                """
                INSERT INTO jobs (
                    owner_user_id, id, company, title, location, apply_url, source, date_posted,
                    is_remote, score, match_reasons, status, cover_letter, body,
                    resume_pdf_path, resume_generated_at, applied_at, notes,
                    mail_action_token, mail_action_expires_at, mail_action_sent_at, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    status=VALUES(status),
                    cover_letter=VALUES(cover_letter),
                    body=VALUES(body),
                    resume_pdf_path=VALUES(resume_pdf_path),
                    resume_generated_at=VALUES(resume_generated_at),
                    applied_at=VALUES(applied_at),
                    notes=VALUES(notes),
                    mail_action_token=VALUES(mail_action_token),
                    mail_action_expires_at=VALUES(mail_action_expires_at),
                    mail_action_sent_at=VALUES(mail_action_sent_at),
                    created_at=VALUES(created_at)
                """,
                [
                    (
                        int(r["owner_user_id"]),
                        r["id"],
                        r.get("company"),
                        r.get("title"),
                        r.get("location"),
                        r.get("apply_url"),
                        r.get("source"),
                        r.get("date_posted"),
                        int(r.get("is_remote") or 0),
                        r.get("score"),
                        r.get("match_reasons"),
                        r.get("status") or "pending",
                        r.get("cover_letter"),
                        r.get("body"),
                        r.get("resume_pdf_path"),
                        normalize_dt(r.get("resume_generated_at")),
                        normalize_dt(r.get("applied_at")),
                        r.get("notes"),
                        r.get("mail_action_token"),
                        normalize_dt(r.get("mail_action_expires_at")),
                        normalize_dt(r.get("mail_action_sent_at")),
                        normalize_dt(r.get("created_at")),
                    )
                    for r in jobs
                ],
            )

        if application_log:
            cur.executemany(
                """
                INSERT INTO application_log (
                    id, job_id, company, title, source, apply_url,
                    job_body, resume_pdf_path, cover_letter,
                    method, status, notes, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        int(r["id"]),
                        r["job_id"],
                        r.get("company"),
                        r.get("title"),
                        r.get("source"),
                        r.get("apply_url"),
                        r.get("job_body"),
                        r.get("resume_pdf_path"),
                        r.get("cover_letter"),
                        r.get("method"),
                        r.get("status"),
                        r.get("notes"),
                        normalize_dt(r.get("created_at")),
                    )
                    for r in application_log
                ],
            )

        if agent_settings:
            cur.executemany(
                """
                INSERT INTO agent_settings (`key`, value)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE value=VALUES(value)
                """,
                [(r["key"], r["value"]) for r in agent_settings],
            )

        conn.commit()
        print("Migration complete.")

    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()

