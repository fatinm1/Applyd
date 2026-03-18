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
import html as html_lib
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
        # NOTE: We intentionally do not keep an in-memory README diff cache here.
        # The orchestrator constructs a new watcher per cycle, so any in-memory
        # cache would reset and break change detection. Instead, we parse the
        # full table each run and rely on SQLite (`seen_jobs`) for dedupe.

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

        data = r.json()
        content = ""

        # Contents API may omit `content` for large files. Prefer `download_url` when present.
        download_url = data.get("download_url") or ""
        encoding = data.get("encoding") or ""
        raw_content = data.get("content") or ""

        if download_url:
            rr = self.session.get(download_url)
            if rr.status_code == 200:
                content = rr.text
        elif encoding == "base64" and raw_content:
            import base64

            content = base64.b64decode(raw_content).decode("utf-8", errors="replace")

        if not content:
            log.warning(f"README content empty for {owner}/{repo}")
            return []

        # Parse markdown pipe tables and/or embedded HTML tables.
        jobs = self._parse_table(content)
        if jobs:
            return jobs
        return self._parse_html_tables(content)

    def _parse_table(self, content: str) -> list[dict]:
        jobs = []
        for line in content.splitlines():
            job = self._row_to_job(line)
            if job:
                jobs.append(job)
        return jobs

    def _parse_html_tables(self, content: str) -> list[dict]:
        """
        Some job repos embed HTML tables in README (no markdown `|` rows).
        We extract <tr> rows with <td> cells and attempt to map them to:
        company, title, location, apply_url, date_posted.
        """
        jobs: list[dict] = []
        if "<table" not in content.lower():
            return jobs

        # Extract rows. Keep it regex-based to avoid adding heavy deps.
        row_re = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>", flags=re.IGNORECASE)
        cell_re = re.compile(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", flags=re.IGNORECASE)
        href_re = re.compile(r'href=[\"\\\'](https?://[^\"\\\']+)[\"\\\']', flags=re.IGNORECASE)

        def strip_tags(html: str) -> str:
            # Replace <br> with spaces, remove remaining tags.
            s = re.sub(r"<br\\s*/?>", " ", html, flags=re.IGNORECASE)
            s = re.sub(r"<[^>]+>", "", s)
            s = s.replace("&amp;", "&").replace("&nbsp;", " ").strip()
            return re.sub(r"\\s+", " ", s).strip()

        for row_html in row_re.findall(content):
            cells = [c.strip() for c in cell_re.findall(row_html)]
            if len(cells) < 3:
                continue

            cell_text = [strip_tags(c) for c in cells]
            # Heuristic mapping:
            # [0]=company, [1]=role/title, [2]=location, [3]=apply, [4]=date (if present)
            company = cell_text[0]
            title = cell_text[1]
            location = cell_text[2] if len(cell_text) > 2 else ""
            date_text = cell_text[4] if len(cell_text) > 4 else (cell_text[3] if len(cell_text) > 3 else "")

            # Apply URL: first link in row (prefer links in role/app columns).
            links = href_re.findall(row_html)
            apply_link = links[0] if links else ""

            if not company or not title:
                continue
            # Skip header rows commonly present in HTML tables.
            if company.strip().lower() == "company" and title.strip().lower() in {"role", "position", "title"}:
                continue

            job_id = hashlib.sha256(f"{company}|{title}|{location}".encode()).hexdigest()[:16]
            jobs.append(
                {
                    "id": job_id,
                    "company": company,
                    "title": title,
                    "location": location,
                    "apply_url": apply_link,
                    "date_posted": date_text,
                    "raw_line": strip_tags(row_html)[:500],
                }
            )

        return jobs

    def _row_to_job(self, line: str) -> dict | None:
        """Parse a markdown table row like: | Company | Role | Location | Apply | Date |"""
        line = line.strip()
        if not line.startswith("|") or line.startswith("| ---") or line.startswith("| Company"):
            return None

        cols = [c.strip() for c in line.split("|") if c.strip()]
        if len(cols) < 3:
            return None

        # Skip markdown divider rows like: |---|---|---|
        if all(c.replace("-", "").strip() == "" for c in cols):
            return None

        # Extract plain text + first URL from markdown cells
        def extract(cell: str) -> tuple[str, str]:
            """
            Returns (plain_text, first_url).
            Handles:
            - markdown links: [Text](https://...)
            - HTML links: <a href="https://...">Text</a>
            """
            cell = cell.strip()

            # Markdown link
            url_match = re.search(r'\[([^\]]+)\]\((https?://[^)]+)\)', cell)
            if url_match:
                return url_match.group(1), url_match.group(2)

            # HTML href
            href_match = re.search(r'href=["\'](https?://[^"\']+)["\']', cell, flags=re.IGNORECASE)
            if href_match:
                plain = re.sub(r"<br\\s*/?>", " ", cell, flags=re.IGNORECASE)
                plain = re.sub(r"<[^>]+>", "", plain)
                plain = html_lib.unescape(plain)
                plain = re.sub(r"\\s+", " ", plain).strip()
                return plain, href_match.group(1)

            # Plain text: strip markdown link wrappers if present.
            plain = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cell).strip()
            plain = html_lib.unescape(plain)
            plain = re.sub(r"<[^>]+>", "", plain)
            plain = re.sub(r"\\s+", " ", plain).strip()
            return plain, ""

        company_text, _ = extract(cols[0])
        title_text, _ = extract(cols[1])
        location, _ = extract(cols[2]) if len(cols) > 2 else ("", "")

        # Apply URL extraction:
        # - Most repos: [Company, Role, Location, Apply, Date]  -> apply URL is typically in column 3
        # - speedyapply: [Company, Position, Location, Salary, Posting, Age] -> apply URL is typically in column 4
        # To support both, search for the first URL in columns >= 3 (avoid picking up the company URL in column 0).
        apply_link = ""
        for idx in range(3, len(cols)):
            _, url = extract(cols[idx])
            if url:
                apply_link = url
                break
        if not apply_link:
            # Fallback: if no URL was found in the expected apply columns, try column 1 (role/position).
            _, url = extract(cols[1]) if len(cols) > 1 else ("", "")
            apply_link = url

        date_text = cols[5] if len(cols) > 5 else (cols[4] if len(cols) > 4 else "")

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
