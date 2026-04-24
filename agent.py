"""
Job Agent — Main Orchestrator
Watches GitHub job repos, matches against your profile, and queues applications.
"""

import schedule
import time
import logging
from datetime import datetime
from typing import Optional

from watcher import GitHubWatcher
from parser import JobParser
from matcher import JobMatcher
from store import JobStore
from notifier import Notifier
from config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("agent.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

REPOS = [
    {"owner": "SimplifyJobs", "repo": "New-Grad-Positions"},
    {"owner": "pittcsc",      "repo": "Summer2025-Internships"},
    {"owner": "speedyapply",  "repo": "2026-SWE-College-Jobs"},
]


def run_scan_cycle(*, notification_user_id: Optional[int] = None):
    log.info("=== Starting job scan cycle ===")

    store    = JobStore()
    owner_uid = store.resolve_scan_owner_user_id(notification_user_id)
    watcher  = GitHubWatcher(token=config.GITHUB_TOKEN)
    parser   = JobParser()
    matcher  = JobMatcher()
    notifier = Notifier()

    new_matches: list[tuple] = []

    for repo_info in REPOS:
        owner, repo = repo_info["owner"], repo_info["repo"]
        repo_key = f"{owner}/{repo}"
        # First-run per repo: index backlog into SQLite without calling Claude or notifying.
        repo_is_first_run = not store.is_repo_indexed(repo_key)
        if repo_is_first_run:
            log.info(f"First run for {repo_key} — indexing existing postings without notifications.")
        log.info(f"Scanning {owner}/{repo} ...")

        try:
            raw_jobs = watcher.fetch_new_jobs(owner, repo)
            log.info(f"  Found {len(raw_jobs)} new postings")
        except Exception as e:
            log.error(f"  Failed to fetch from {owner}/{repo}: {e}")
            continue

        for raw in raw_jobs:
            try:
                job = parser.parse(raw, source=f"{owner}/{repo}")
            except Exception as e:
                log.warning(f"  Could not parse job: {e}")
                continue

            if store.is_seen(owner_uid, job.id):
                continue

            # On first run for this repo, avoid expensive scoring/Claude calls.
            if repo_is_first_run:
                store.mark_seen(owner_uid, job.id)
                store.save_job(job, score=0.0, match_reasons=["[indexed on first run]"], owner_user_id=owner_uid)
                continue

            score, reasons = matcher.score(job)
            log.info(f"  [{score:.0%}] {job.company} — {job.title}")

            store.mark_seen(owner_uid, job.id)
            store.save_job(job, score=score, match_reasons=reasons, owner_user_id=owner_uid)

            if score >= config.MATCH_THRESHOLD:
                new_matches.append((job, score, reasons, owner_uid))

        if repo_is_first_run:
            store.mark_repo_indexed(repo_key)

    if new_matches:
        log.info(f"Sending digest: {len(new_matches)} matches found")
        recipients = store.resolve_scan_notification_recipients(notification_user_id)
        notifier.send_digest(new_matches, recipients=recipients)
        try:
            notifier.send_match_approval_request_emails(
                new_matches, store=store, recipients=recipients
            )
        except Exception as e:
            log.warning(f"Approval request emails failed: {e}")
    else:
        log.info("No new matches this cycle.")

    log.info("=== Cycle complete ===\n")


# Backwards-compatible alias: scan-only.
def run_cycle():
    run_scan_cycle()


def run_scan_cycle_and_apply(*, notification_user_id: Optional[int] = None):
    """
    Run one full scan cycle, then attempt Phase 2 auto-apply.

    This is the "worker" entrypoint that can be called repeatedly by the
    web backend's background loop.
    """
    run_scan_cycle(notification_user_id=notification_user_id)

    # Phase 2: try auto-apply (no-op if AUTO_APPLY_ENABLED is false).
    try:
        from applier import run_auto_apply

        run_auto_apply()
    except Exception as e:
        log.error(f"Auto-apply step failed: {e}")


def main():
    log.info("Job Agent starting up...")
    log.info(f"Polling every {config.POLL_INTERVAL_MINUTES} minutes")
    log.info(f"Match threshold: {config.MATCH_THRESHOLD:.0%}")

    # Run immediately on start, then on schedule
    run_scan_cycle_and_apply()

    schedule.every(config.POLL_INTERVAL_MINUTES).minutes.do(run_scan_cycle_and_apply)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
