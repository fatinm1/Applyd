"""
config.py — All agent settings in one place.
Copy this to .env or edit directly.
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    # ── GitHub ─────────────────────────────────────────────────────────────
    # Create a token at https://github.com/settings/tokens (no special scopes needed for public repos)
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

    # How often to poll repos (minutes)
    POLL_INTERVAL_MINUTES: int = int(os.getenv("POLL_INTERVAL_MINUTES", "15"))

    # ── Anthropic / Claude ─────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # ── Matching ───────────────────────────────────────────────────────────
    # Jobs scoring below this are silently skipped (0.0 – 1.0)
    MATCH_THRESHOLD: float = float(os.getenv("MATCH_THRESHOLD", "0.65"))

    # ── Your Profile ───────────────────────────────────────────────────────
    # Edit everything in this block to match your background.

    YOUR_NAME: str = "Alex Johnson"
    YOUR_EMAIL: str = "alex@example.com"

    # One-paragraph summary used in cover letter generation
    BIO: str = (
        "I'm a CS senior graduating in May 2025 with a 3.8 GPA from Georgia Tech. "
        "I've interned at two startups (full-stack React/Node and Python ML pipelines) "
        "and I'm looking for new-grad SWE roles at high-growth companies."
    )

    # Skills — used for matching AND resume tailoring
    SKILLS: List[str] = field(default_factory=lambda: [
        "Python", "TypeScript", "React", "Node.js",
        "SQL", "PostgreSQL", "Docker", "AWS", "Machine Learning",
        "REST APIs", "Git", "Linux",
    ])

    # Roles you want (keywords matched against job title)
    TARGET_ROLES: List[str] = field(default_factory=lambda: [
        "Software Engineer", "SWE", "Backend Engineer", "Full Stack",
        "ML Engineer", "Data Engineer",
    ])

    # Locations you'll consider (case-insensitive substring match)
    # Add "Remote" to accept remote roles
    TARGET_LOCATIONS: List[str] = field(default_factory=lambda: [
        "Remote", "New York", "San Francisco", "Seattle", "Austin",
    ])

    # Minimum salary (USD). Set to 0 to disable filter.
    MIN_SALARY_USD: int = int(os.getenv("MIN_SALARY_USD", "0"))

    # ── Notification ───────────────────────────────────────────────────────
    # At least one of these should be filled in.

    # Email (uses Gmail SMTP by default)
    NOTIFY_EMAIL: str = os.getenv("NOTIFY_EMAIL", "")
    GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "")

    # Slack incoming webhook URL (optional)
    SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")

    # ── Storage ────────────────────────────────────────────────────────────
    DB_PATH: str = os.getenv("DB_PATH", "jobs.db")

    # ── Application (Phase 2 — auto-apply) ────────────────────────────────
    # Leave False until you've reviewed several manual cycles and trust the agent.
    AUTO_APPLY_ENABLED: bool = os.getenv("AUTO_APPLY_ENABLED", "false").lower() == "true"

    # Path to your base resume PDF (used for upload when applying)
    RESUME_PATH: str = os.getenv("RESUME_PATH", "resume.pdf")


config = Config()
