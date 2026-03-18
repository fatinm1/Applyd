"""
parser.py — Job data model and normalisation

This project expects a `Job` dataclass and a `JobParser` that turns raw job
dicts (from `watcher.py`) into a normalized representation used by:
- `matcher.py` (heuristics + Claude scoring)
- `store.py` (persistence)
- `review.py` / `applier.py` (cover letters + auto-apply)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class Job:
    id: str
    company: str
    title: str
    location: str = ""
    apply_url: str = ""
    source: str = ""
    date_posted: str = ""
    body: str = ""

    # Signals used by the matcher / filters
    is_remote: bool = False
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None

    # Not currently stored in SQLite, but useful for scoring context
    visa_sponsorship: Optional[str] = None

    @property
    def display(self) -> str:
        # Used by applier/debug logs.
        return f"{self.company} — {self.title}"


class JobParser:
    def parse(self, raw: dict[str, Any], source: str) -> Job:
        """
        Convert a raw job dict from `watcher.py` into a normalized `Job`.

        The watcher provides (depending on repo format):
        - readme-row jobs: company/title/location/apply_url/date_posted
        - issues fallback jobs: company/title/apply_url/date_posted/body
        """
        job_id = str(raw.get("id") or "")
        if not job_id:
            # As a last resort (shouldn't happen), fall back to a stable-ish id.
            # The watcher is intended to provide a stable hash id.
            job_id = self._fallback_id(raw, source)

        company = str(raw.get("company") or "").strip()
        title = str(raw.get("title") or "").strip()
        location = str(raw.get("location") or "").strip()
        apply_url = str(raw.get("apply_url") or "").strip()
        date_posted = str(raw.get("date_posted") or "").strip()
        body = str(raw.get("body") or "").strip()
        if not body:
            # README-row jobs often don't include a full description; we still
            # pass along the raw row so tailoring/scoring has some context.
            raw_line = str(raw.get("raw_line") or "").strip()
            if raw_line:
                body = raw_line

        # Combine available text for extraction signals.
        combined_text = " ".join(x for x in [title, body, location] if x).strip()
        is_remote = self._detect_remote(combined_text)
        salary_min, salary_max = self._parse_salary_range(combined_text)
        visa = self._detect_visa_sponsorship(combined_text)

        # Make sponsorship intent visible to Claude even if we only got
        # sparse data (e.g. a README row without a full description).
        if visa and (not body or visa.lower() not in body.lower()):
            if body:
                body = f"{body}\n\nVisa sponsorship: {visa}"
            else:
                body = f"Visa sponsorship: {visa}"

        return Job(
            id=job_id,
            company=company,
            title=title,
            location=location,
            apply_url=apply_url,
            source=source,
            date_posted=date_posted,
            body=body,
            is_remote=is_remote,
            salary_min=salary_min,
            salary_max=salary_max,
            visa_sponsorship=visa,
        )

    def _fallback_id(self, raw: dict[str, Any], source: str) -> str:
        # Simple deterministic fallback (only used if watcher didn't provide `id`).
        company = str(raw.get("company") or "").strip().lower()
        title = str(raw.get("title") or "").strip().lower()
        location = str(raw.get("location") or "").strip().lower()
        key = f"{company}|{title}|{location}|{source}"
        import hashlib

        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _detect_remote(text: str) -> bool:
        t = (text or "").lower()
        # Keep it intentionally simple: remote detection is a substring match.
        return any(
            token in t
            for token in [
                "remote",
                "work from home",
                "wfh",
                "telecommute",
                "distributed team",
            ]
        )

    @staticmethod
    def _parse_amount_to_usd(amount_text: str) -> int:
        s = amount_text.strip()
        s = s.replace("$", "").replace("USD", "").replace("usd", "").strip()
        s = s.replace(",", "")
        multiplier = 1
        if s.lower().endswith("k"):
            multiplier = 1000
            s = s[:-1]
        # Some postings use decimals like 120.5k.
        value = float(s)
        return int(round(value * multiplier))

    def _parse_salary_range(self, text: str) -> tuple[Optional[int], Optional[int]]:
        """
        Extract salary range from text.

        Supports common formats:
        - "$120k–$160k", "$120k-$160k", "$120,000 - $160,000"
        - "up to $180k", "over $120k", "from $100k"
        """
        if not text:
            return None, None

        t = text.replace("\u2013", "-").replace("\u2014", "-")  # normalize dashes

        # Range: amount (dash/to) amount
        range_pat = re.compile(
            r"(?P<a>\$?\s*\d[\d,]*(?:\.\d+)?\s*[kK]?)\s*(?:-|to)\s*(?P<b>\$?\s*\d[\d,]*(?:\.\d+)?\s*[kK]?)"
        )
        m = range_pat.search(t)
        if m:
            a = self._parse_amount_to_usd(m.group("a"))
            b = self._parse_amount_to_usd(m.group("b"))
            return (min(a, b), max(a, b))

        # Up to: "up to $180k"
        up_to_pat = re.compile(
            r"(?:up to|≤|=<)\s*(?P<a>\$?\s*\d[\d,]*(?:\.\d+)?\s*[kK]?)",
            flags=re.IGNORECASE,
        )
        m = up_to_pat.search(t)
        if m:
            mx = self._parse_amount_to_usd(m.group("a"))
            return None, mx

        # Over / from: "over $120k" / "from $100k"
        from_pat = re.compile(
            r"(?:over|above|from|>=|=>)\s*(?P<a>\$?\s*\d[\d,]*(?:\.\d+)?\s*[kK]?)",
            flags=re.IGNORECASE,
        )
        m = from_pat.search(t)
        if m:
            mn = self._parse_amount_to_usd(m.group("a"))
            return mn, None

        # Single amount: "$150k"
        single_pat = re.compile(r"\$?\s*(?P<a>\d[\d,]*(?:\.\d+)?\s*[kK]?)")
        m = single_pat.search(t)
        if m:
            v = self._parse_amount_to_usd(m.group("a"))
            return None, v

        return None, None

    @staticmethod
    def _detect_visa_sponsorship(text: str) -> Optional[str]:
        t = (text or "").lower()

        # Negative first.
        if any(phrase in t for phrase in ["no visa sponsorship", "no sponsorship", "without sponsorship"]):
            return "No visa sponsorship"

        # Common positive/allow cases.
        if any(
            phrase in t
            for phrase in [
                "h-1b sponsored",
                "h1b sponsored",
                "h-1b",
                "h1b",
                "visa sponsorship",
                "sponsorship available",
                "will sponsor",
                "sponsor",
            ]
        ):
            # Keep the string generic; avoids overclaiming.
            return "Visa sponsorship possible"

        if "requires sponsorship" in t or "need sponsorship" in t:
            return "Visa sponsorship required"

        return None

