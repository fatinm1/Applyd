#!/usr/bin/env python3
"""
Send one synthetic approval-request email (same code path as the agent).

From repo root, with env loaded (do not commit secrets):

  set -a && source .env && set +a
  python scripts/send_test_approval_email.py

Required:
  EMAIL_APPROVAL_REQUESTS_ENABLED=true
  NOTIFY_EMAIL, GMAIL_APP_PASSWORD
  PUBLIC_BASE_URL  (must match where /api/mail/* is deployed)
  MAIL_SIGNING_SECRET or SESSION_SECRET_KEY

Optional:
  TAILORED_RESUME_ENABLED=false  — faster if you do not want LaTeX during the send

Important:
  PUBLIC_BASE_URL must be the API that uses the **same database** as this script.
  If you use local SQLite here but point PUBLIC_BASE_URL at Railway, approve/reject
  will show “Not found” on production. Use --force-cross-env only if you know what
  you are doing.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from urllib.parse import urlparse

# Repo root (parent of scripts/)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from config import config  # noqa: E402
from notifier import Notifier  # noqa: E402
from parser import Job  # noqa: E402
from store import JobStore  # noqa: E402


def _public_host_is_local(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in ("localhost", "127.0.0.1", "::1", "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one synthetic approval-request email.")
    parser.add_argument(
        "--force-cross-env",
        action="store_true",
        help="Send even when local SQLite + remote PUBLIC_BASE_URL (links will not work on that host).",
    )
    args = parser.parse_args()

    missing: list[str] = []
    if not config.EMAIL_APPROVAL_REQUESTS_ENABLED:
        missing.append("EMAIL_APPROVAL_REQUESTS_ENABLED=true")
    if not (config.NOTIFY_EMAIL or "").strip() or not (config.GMAIL_APP_PASSWORD or "").strip():
        missing.append("NOTIFY_EMAIL and GMAIL_APP_PASSWORD")
    if not (config.PUBLIC_BASE_URL or "").strip():
        missing.append("PUBLIC_BASE_URL")
    secret = (config.MAIL_SIGNING_SECRET or "").strip() or (config.SESSION_SECRET_KEY or "").strip()
    if not secret:
        missing.append("MAIL_SIGNING_SECRET or SESSION_SECRET_KEY")

    if missing:
        print("Not sending — fix your environment:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 2

    pub = (config.PUBLIC_BASE_URL or "").strip()
    if (
        not args.force_cross_env
        and config.DB_BACKEND == "sqlite"
        and pub
        and not _public_host_is_local(pub)
    ):
        print(
            "Refusing to send: DB_BACKEND=sqlite (this machine’s jobs.db) but PUBLIC_BASE_URL "
            f"is remote ({pub!r}). Approve/reject hits that host’s DB, which will not contain "
            "this test job.\n\n"
            "Fix: use a tunnel URL to your local API + same jobs.db, or run on Railway:\n"
            "  railway run python scripts/send_test_approval_email.py\n\n"
            "Override: add --force-cross-env",
            file=sys.stderr,
        )
        return 3

    jid = f"test-approval-{int(time.time())}"
    job = Job(
        id=jid,
        company="TestCorp",
        title="Software Engineer (approval email smoke test)",
        location="Remote",
        apply_url="https://example.com/apply",
        source="test/manual",
        body="Python, React, distributed systems.",
        is_remote=True,
    )
    store = JobStore()
    owner_uid = store.get_worker_owner_user_id()
    store.save_job(job, score=0.88, match_reasons=["Synthetic test row for approval email."], owner_user_id=owner_uid)
    notifier = Notifier()
    recipients = store.resolve_scan_notification_recipients(None)
    notifier.send_match_approval_request_emails(
        [(job, 0.88, ["Synthetic test", "Heuristic-style reason"], owner_uid)],
        store=store,
        recipients=recipients,
    )
    print("Sent (or logged errors above). Job id:", jid)
    print("Inbox:", config.NOTIFY_EMAIL)
    print("Link base:", config.PUBLIC_BASE_URL.rstrip("/"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
