"""
watcher.py — Polls GitHub repos for new job postings.

Both target repos (SimplifyJobs/New-Grad-Positions and pittcsc/Summer2025-Internships)
store jobs as rows in a README.md table. We fetch the README, diff it against our
last-seen snapshot, and return only new rows.

For repos that use GitHub Issues for postings, there's also an issues-based fallback.
"""

from __future__ import annotations

import hashlib
import re
import logging
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

HEADERS_BASE = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


class GitHubWatcher:
    def __init__(self, token: str):
        self.session = requests.Session()
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        self.session.headers.update(HEADERS_BASE)
        self._readme_cache: dict[str, str] = {}  # repo_key -> last seen raw content

    # ── Public ──────────────────────────────────────────────────────────────

    def fetch_new_jobs(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """Return list of raw job dicts that we haven't seen before."""
        # Try README table first (both target repos use this format)
        jobs = self._fetch_from_readme(owner, repo)
        if jobs is not None:
            return jobs

        # Fallback: GitHub Issues
        return self._fetch_from_issues(owner, repo)

    # ── README table parsing ────────────────────────────────────────────────

    def _fetch_from_readme(self, owner: str, repo: str) -> list[dict] | None:
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/README.md"
        r = self.session.get(url)
        if r.status_code != 200:
            log.warning(f"README not found for {owner}/{repo} (status {r.status_code})")
            return None

        import base64
        content = base64.b64decode(r.json()["content"]).decode("utf-8", errors="replace")

        cache_key = f"{owner}/{repo}"
        old_content = self._readme_cache.get(cache_key, "")
        self._readme_cache[cache_key] = content

        # On first run, parse entire table so we build the seen-store without applying
        if not old_content:
            log.info(f"  First run for {owner}/{repo} — indexing existing jobs (not applying)")
            rows = self._parse_table(content)
            # Mark all as seen immediately so we only act on future additions
            return []   # caller will mark_seen each one

        # Diff: find lines present in new content but not in old
        old_lines = set(old_content.splitlines())
        new_lines  = [l for l in content.splitlines() if l not in old_lines]

        jobs = []
        for line in new_lines:
            job = self._row_to_job(line)
            if job:
                jobs.append(job)

        return jobs

    def _parse_table(self, content: str) -> list[dict]:
        jobs = []
        for line in content.splitlines():
            job = self._row_to_job(line)
            if job:
                jobs.append(job)
        return jobs

    def _row_to_job(self, line: str) -> dict | None:
        """Parse a markdown table row like: | Company | Role | Location | Apply | Date |"""
        line = line.strip()
        if not line.startswith("|") or line.startswith("| ---") or line.startswith("| Company"):
            return None

        cols = [c.strip() for c in line.split("|") if c.strip()]
        if len(cols) < 3:
            return None

        # Extract plain text + first URL from markdown cells
        def extract(cell: str) -> tuple[str, str]:
            url_match = re.search(r'\[([^\]]+)\]\((https?://[^)]+)\)', cell)
            if url_match:
                return url_match.group(1), url_match.group(2)
            plain = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cell).strip()
            return plain, ""

        company_text, company_url = extract(cols[0])
        title_text,   apply_url  = extract(cols[1])
        location = cols[2] if len(cols) > 2 else ""
        apply_link = apply_url or (extract(cols[3])[1] if len(cols) > 3 else "")
        date_text  = cols[4] if len(cols) > 4 else ""

        if not company_text or not title_text:
            return None

        job_id = hashlib.sha256(f"{company_text}|{title_text}|{location}".encode()).hexdigest()[:16]

        return {
            "id": job_id,
            "company": company_text,
            "title": title_text,
            "location": location,
            "apply_url": apply_link,
            "date_posted": date_text,
            "raw_line": line,
        }

    # ── Issues fallback ─────────────────────────────────────────────────────

    def _fetch_from_issues(self, owner: str, repo: str) -> list[dict]:
        """For repos that post jobs as GitHub Issues."""
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        params = {"state": "open", "sort": "created", "direction": "desc", "per_page": 30}
        r = self.session.get(url, params=params)
        if r.status_code != 200:
            log.error(f"Issues fetch failed for {owner}/{repo}: {r.status_code}")
            return []

        jobs = []
        for issue in r.json():
            if issue.get("pull_request"):
                continue  # skip PRs

            job_id = hashlib.sha256(str(issue["id"]).encode()).hexdigest()[:16]
            jobs.append({
                "id": job_id,
                "company": self._extract_company_from_title(issue["title"]),
                "title": issue["title"],
                "location": "",
                "apply_url": issue["html_url"],
                "date_posted": issue["created_at"],
                "body": issue.get("body", ""),
            })

        return jobs

    @staticmethod
    def _extract_company_from_title(title: str) -> str:
        """Heuristic: 'Software Engineer at Stripe' → 'Stripe'"""
        for sep in [" at ", " @ ", " - ", " | "]:
            if sep in title:
                return title.split(sep)[-1].strip()
        return title.split()[0] if title else "Unknown"
