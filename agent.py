"""
Job Agent — Main Orchestrator
Watches GitHub job repos, matches against your profile, and queues applications.
"""

import schedule
import time
import logging
from datetime import datetime

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
]


def run_cycle():
    log.info("=== Starting job scan cycle ===")

    store    = JobStore()
    watcher  = GitHubWatcher(token=config.GITHUB_TOKEN)
    parser   = JobParser()
    matcher  = JobMatcher()
    notifier = Notifier()

    new_matches = []

    for repo_info in REPOS:
        owner, repo = repo_info["owner"], repo_info["repo"]
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

            if store.is_seen(job.id):
                continue

            score, reasons = matcher.score(job)
            log.info(f"  [{score:.0%}] {job.company} — {job.title}")

            store.mark_seen(job.id)
            store.save_job(job, score=score, match_reasons=reasons)

            if score >= config.MATCH_THRESHOLD:
                new_matches.append((job, score, reasons))

    if new_matches:
        log.info(f"Sending digest: {len(new_matches)} matches found")
        notifier.send_digest(new_matches)
    else:
        log.info("No new matches this cycle.")

    log.info("=== Cycle complete ===\n")


def main():
    log.info("Job Agent starting up...")
    log.info(f"Polling every {config.POLL_INTERVAL_MINUTES} minutes")
    log.info(f"Match threshold: {config.MATCH_THRESHOLD:.0%}")

    # Run immediately on start, then on schedule
    run_cycle()

    schedule.every(config.POLL_INTERVAL_MINUTES).minutes.do(run_cycle)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
