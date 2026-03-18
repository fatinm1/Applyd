"""
matcher.py — Scores jobs against your profile using Claude.

Returns a (score, reasons) tuple where score is 0.0–1.0.
Fast heuristic pre-filter runs first; Claude is only called for promising jobs.
"""

import json
import logging
import re
from typing import Any

import anthropic

from config import config
from parser import Job

log = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

# Minimum heuristic score to bother calling Claude (saves API cost)
_HEURISTIC_FLOOR = 0.25


class JobMatcher:
    def score(self, job: Job) -> tuple[float, list[str]]:
        """Return (score 0–1, list of reason strings)."""

        # ── 1. Fast heuristic pre-filter ─────────────────────────────────
        h_score, h_reasons = self._heuristic_score(job)

        if h_score < _HEURISTIC_FLOOR:
            return h_score, h_reasons + ["[skipped AI scoring — heuristic too low]"]

        # ── 2. Claude scoring ─────────────────────────────────────────────
        try:
            ai_score, ai_reasons = self._claude_score(job)
            # Blend: 30% heuristic, 70% Claude
            final = 0.3 * h_score + 0.7 * ai_score
            return round(final, 3), h_reasons + ai_reasons
        except Exception as e:
            log.warning(f"Claude scoring failed for {job.display}: {e}")
            return h_score, h_reasons + [f"[AI scoring failed: {e}]"]

    # ── Heuristic scorer ─────────────────────────────────────────────────

    def _heuristic_score(self, job: Job) -> tuple[float, list[str]]:
        score = 0.0
        reasons = []
        combined = f"{job.title} {job.body} {job.location}".lower()

        # Role match
        matched_roles = [r for r in config.TARGET_ROLES if r.lower() in combined]
        if matched_roles:
            score += 0.35
            reasons.append(f"Role match: {', '.join(matched_roles)}")
        else:
            reasons.append("No role keyword match")

        # Location match
        if job.is_remote:
            score += 0.25
            reasons.append("Remote position")
        else:
            matched_locs = [l for l in config.TARGET_LOCATIONS if l.lower() in combined]
            if matched_locs:
                score += 0.25
                reasons.append(f"Location match: {', '.join(matched_locs)}")
            else:
                reasons.append("Location not in preferences")

        # Skills overlap
        matched_skills = [s for s in config.SKILLS if s.lower() in combined]
        skill_ratio = len(matched_skills) / max(len(config.SKILLS), 1)
        score += 0.3 * skill_ratio
        if matched_skills:
            reasons.append(f"Skills: {', '.join(matched_skills[:5])}")

        # Salary filter
        if config.MIN_SALARY_USD and job.salary_max and job.salary_max < config.MIN_SALARY_USD:
            score *= 0.2
            reasons.append(f"Salary ${job.salary_max:,} below minimum")

        return min(score, 1.0), reasons

    # ── Claude scorer ────────────────────────────────────────────────────

    def _claude_score(self, job: Job) -> tuple[float, list[str]]:
        prompt = f"""You are evaluating a job posting for a candidate. Score the fit from 0.0 to 1.0.

CANDIDATE PROFILE:
Name: {config.YOUR_NAME}
Bio: {config.BIO}
Skills: {', '.join(config.SKILLS)}
Target roles: {', '.join(config.TARGET_ROLES)}
Target locations: {', '.join(config.TARGET_LOCATIONS)}

JOB POSTING:
Company: {job.company}
Title: {job.title}
Location: {job.location} {'(Remote)' if job.is_remote else ''}
Description: {job.body[:1500] if job.body else 'Not available'}

Respond ONLY with valid JSON, no markdown, no explanation:
{{
  "score": <float 0.0-1.0>,
  "reasons": ["reason 1", "reason 2", "reason 3"]
}}

Score guidelines:
0.9+ = Near-perfect match, strong apply
0.7-0.9 = Good match, worth applying
0.5-0.7 = Moderate fit, borderline
<0.5 = Poor fit, skip"""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()
        # Strip any accidental markdown fences
        raw = re.sub(r"^```[a-z]*\n?|```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)

        return float(data["score"]), data.get("reasons", [])


# ── Cover letter generator ───────────────────────────────────────────────────

def generate_cover_letter(job: Job) -> str:
    """Generate a tailored cover letter for a job using Claude."""
    # If the API key isn't configured, fall back to a deterministic,
    # locally-generated letter so the dashboard workflow can still be tested.
    if not config.ANTHROPIC_API_KEY:
        return _fallback_cover_letter(job)

    prompt = f"""Write a concise, authentic cover letter for this job application.

CANDIDATE:
Name: {config.YOUR_NAME}
Email: {config.YOUR_EMAIL}
Bio: {config.BIO}
Skills: {', '.join(config.SKILLS)}

JOB:
Company: {job.company}
Role: {job.title}
Location: {job.location}
Description: {job.body[:2000] if job.body else 'Not provided'}

Guidelines:
- 3 paragraphs max, no fluff
- Mention 2-3 specific skills relevant to THIS job
- Sound human, not AI-generated
- End with a clear call to action
- Do NOT include "Dear Hiring Manager" boilerplate header — just the body paragraphs

Return only the letter body, no subject line, no salutation header."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()


def _fallback_cover_letter(job: Job) -> str:
    combined = f"{job.title} {job.body} {job.location}".lower()
    matched = [s for s in config.SKILLS if s.lower() in combined]
    if not matched:
        matched = config.SKILLS[:3]
    matched = matched[:3]

    p1 = (
        f"I’m excited to apply for the {job.title} role at {job.company}. "
        f"My background aligns closely with {', '.join(matched)}, and I enjoy turning ambiguous requirements into clean, reliable software."
    )
    p2 = (
        f"Based on my experience described in my bio, I’ve worked on full-stack products and Python ML pipelines—"
        f"building features end-to-end, collaborating with teams, and iterating quickly. "
        f"I’m especially interested in how your team applies {matched[0]} and delivers measurable outcomes."
    )
    p3 = (
        f"I’d welcome the chance to discuss how I can contribute to {job.company}. "
        f"Thank you for your time and consideration—I look forward to hearing from you."
    )

    # Applier/email code splits on blank lines into paragraphs.
    return f"{p1}\n\n{p2}\n\n{p3}"
