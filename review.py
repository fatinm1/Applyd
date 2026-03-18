"""
review.py — CLI tool for reviewing pending jobs and managing applications.

Usage:
    python review.py          # Interactive review queue
    python review.py list     # List all jobs
    python review.py stats    # Show statistics
    python review.py cover <job_id>   # Generate cover letter for a job
"""

import sys
import json
from datetime import datetime

from store import JobStore
from matcher import generate_cover_letter
from parser import Job
from config import config

store = JobStore()


def cmd_review():
    """Interactive review of pending jobs."""
    jobs = store.get_pending()
    matches = [j for j in jobs if j["score"] >= config.MATCH_THRESHOLD]

    if not matches:
        print("✅ No pending jobs to review.")
        return

    print(f"\n{'='*60}")
    print(f"  REVIEW QUEUE — {len(matches)} jobs above {config.MATCH_THRESHOLD:.0%} threshold")
    print(f"{'='*60}")
    print("  Commands: [a]pprove · [s]kip · [r]eject · [c]over letter · [q]uit\n")

    for i, job in enumerate(matches, 1):
        reasons = json.loads(job.get("match_reasons") or "[]")
        loc = "🌐 Remote" if job["is_remote"] else job["location"] or "—"

        print(f"  [{i}/{len(matches)}] {job['company']} — {job['title']}")
        print(f"         Location : {loc}")
        print(f"         Score    : {job['score']:.0%}")
        print(f"         Source   : {job['source']}")
        print(f"         Apply    : {job['apply_url'] or '(no direct link)'}")
        for r in reasons[:4]:
            print(f"         • {r}")
        print()

        while True:
            cmd = input("  > ").strip().lower()
            if cmd in ("a", "approve"):
                store.update_status(job["id"], "approved")
                print("  ✅ Approved — will apply on next auto-apply run\n")
                break
            elif cmd in ("s", "skip", ""):
                store.update_status(job["id"], "skipped")
                print("  ⏭  Skipped\n")
                break
            elif cmd in ("r", "reject"):
                note = input("  Reason (optional): ").strip()
                store.update_status(job["id"], "rejected", notes=note)
                print("  ❌ Rejected\n")
                break
            elif cmd in ("c", "cover"):
                print("\n  Generating cover letter via Claude...\n")
                j = Job(
                    id=job["id"], company=job["company"], title=job["title"],
                    location=job["location"], apply_url=job["apply_url"],
                    source=job["source"], body=job.get("body", ""),
                    is_remote=bool(job["is_remote"]),
                )
                letter = generate_cover_letter(j)
                print("-"*50)
                print(letter)
                print("-"*50 + "\n")
                store.update_status(job["id"], job["status"], cover_letter=letter)
            elif cmd in ("q", "quit"):
                print("\n  👋 Exiting review.\n")
                return
            else:
                print("  Unknown command. Use: a / s / r / c / q")


def cmd_list():
    """Print all jobs in the database."""
    jobs = store.get_all()
    if not jobs:
        print("No jobs in database yet.")
        return

    STATUS_ICON = {
        "pending": "⏳", "approved": "✅", "applied": "📨",
        "rejected": "❌", "skipped": "⏭ ",
    }

    print(f"\n{'='*70}")
    print(f"  ALL JOBS ({len(jobs)} total)")
    print(f"{'='*70}")
    for j in jobs:
        icon = STATUS_ICON.get(j["status"], "?")
        score_str = f"{j['score']:.0%}" if j["score"] else "—"
        print(f"  {icon} [{score_str}] {j['company']} — {j['title']}  ({j['status']})")
    print()


def cmd_stats():
    """Print application statistics."""
    s = store.stats()
    print(f"""
  JOB AGENT STATS
  ───────────────
  Total seen   : {s.get('total', 0)}
  Applied      : {s.get('applied', 0)}
  Pending      : {s.get('pending', 0)}
  Skipped      : {s.get('skipped', 0)}
  Avg score    : {(s.get('avg_score') or 0):.0%}
""")


def cmd_cover(job_id: str):
    """Generate a cover letter for a specific job."""
    jobs = store.get_all()
    job_row = next((j for j in jobs if j["id"] == job_id), None)
    if not job_row:
        print(f"Job {job_id!r} not found.")
        return

    j = Job(
        id=job_row["id"], company=job_row["company"], title=job_row["title"],
        location=job_row["location"], apply_url=job_row["apply_url"],
        source=job_row["source"], body=job_row.get("body", ""),
        is_remote=bool(job_row["is_remote"]),
    )
    print(f"\nGenerating cover letter for {j.company} — {j.title}...\n")
    letter = generate_cover_letter(j)
    print(letter)
    store.update_status(j.id, job_row["status"], cover_letter=letter)
    print("\n[Saved to database]")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        cmd_review()
    elif args[0] == "list":
        cmd_list()
    elif args[0] == "stats":
        cmd_stats()
    elif args[0] == "cover" and len(args) > 1:
        cmd_cover(args[1])
    else:
        print(__doc__)
