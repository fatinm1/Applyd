"""
notifier.py — Sends match digests via Slack webhook and/or email.
"""

import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import requests

from config import config
from parser import Job

log = logging.getLogger(__name__)


class Notifier:
    def send_digest(self, matches: list[tuple[Job, float, list[str]]]):
        """Send a digest of new job matches. matches = [(job, score, reasons), ...]"""
        matches_sorted = sorted(matches, key=lambda x: x[1], reverse=True)

        if config.SLACK_WEBHOOK_URL:
            self._send_slack(matches_sorted)

        if config.NOTIFY_EMAIL and config.GMAIL_APP_PASSWORD:
            self._send_email(matches_sorted)

        if not config.SLACK_WEBHOOK_URL and not config.NOTIFY_EMAIL:
            log.warning("No notification channel configured. Set SLACK_WEBHOOK_URL or NOTIFY_EMAIL.")
            self._print_digest(matches_sorted)

    def send_apply_notification(
        self,
        *,
        job: Job,
        status: str,
        resume_pdf_path: str = "",
        method: str = "",
        notes: str = "",
    ):
        """
        Send an email when a job successfully transitions to `status='applied'`.

        We intentionally do not send on failure to avoid notification spam.
        """
        if status != "applied":
            return

        if not config.NOTIFY_EMAIL or not config.GMAIL_APP_PASSWORD:
            return

        try:
            safe_resume = (resume_pdf_path or "").strip()
            resume_file = safe_resume.split("/")[-1] if safe_resume else ""
            subject = f"✅ Applied: {job.company} — {job.title}"

            job_link = job.apply_url or f"https://github.com/{job.source}"
            notes_html = f"<p><strong>Notes:</strong> {notes}</p>" if notes else ""

            body_html = f"""
            <html><body style="font-family:system-ui,sans-serif;color:#111;max-width:720px;margin:0 auto;padding:24px;line-height:1.5">
              <h2 style="margin-top:0">Application submitted</h2>
              <p><strong>Company:</strong> {job.company}</p>
              <p><strong>Role:</strong> {job.title}</p>
              <p><strong>Method:</strong> {method or 'browser'}</p>
              <p><strong>Resume used:</strong> {resume_file or '(not recorded)'}
                 <br><span style="color:#6b7280;font-size:13px">{safe_resume}</span></p>
              <p><strong>Job link:</strong> <a href="{job_link}">{job_link}</a></p>
              {notes_html}
              <p style="color:#6b7280;font-size:12px;margin-top:24px">Sent by Applyd · jobs.db keeps an application log.</p>
            </body></html>
            """

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = config.NOTIFY_EMAIL
            msg["To"] = config.NOTIFY_EMAIL
            msg.attach(MIMEText(body_html, "html"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(config.NOTIFY_EMAIL, config.GMAIL_APP_PASSWORD)
                server.send_message(msg)

            log.info("Apply success email sent.")
        except Exception as e:
            log.error(f"Apply notification email failed: {e}")

    # ── Slack ─────────────────────────────────────────────────────────────

    def _send_slack(self, matches: list):
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"🎯 {len(matches)} new job match{'es' if len(matches) != 1 else ''}"},
            }
        ]

        for job, score, reasons in matches[:10]:  # Slack has block limits
            pct = f"{score:.0%}"
            loc = "🌐 Remote" if job.is_remote else job.location or "—"
            top_reasons = " · ".join(reasons[:2]) if reasons else ""

            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{job.company}* — {job.title}\n"
                        f"{loc}  |  Match: *{pct}*\n"
                        f"_{top_reasons}_"
                    ),
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View job"},
                    "url": job.apply_url or f"https://github.com/{job.source}",
                    "action_id": f"view_{job.id}",
                },
            })

        if len(matches) > 10:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_...and {len(matches) - 10} more in jobs.db_"}],
            })

        payload = {"blocks": blocks}

        try:
            r = requests.post(config.SLACK_WEBHOOK_URL, json=payload, timeout=10)
            r.raise_for_status()
            log.info("Slack digest sent.")
        except Exception as e:
            log.error(f"Slack notification failed: {e}")

    # ── Email ─────────────────────────────────────────────────────────────

    def _send_email(self, matches: list):
        subject = f"🎯 Job Agent: {len(matches)} new match{'es' if len(matches) != 1 else ''}"

        html_rows = ""
        for job, score, reasons in matches:
            pct = f"{score:.0%}"
            loc = "Remote" if job.is_remote else job.location or "—"
            reasons_str = "<br>".join(f"• {r}" for r in reasons[:4])
            link = job.apply_url or f"https://github.com/{job.source}"
            html_rows += f"""
            <tr>
              <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb">
                <strong>{job.company}</strong><br>
                <span style="color:#374151">{job.title}</span><br>
                <span style="color:#6b7280;font-size:13px">{loc}</span>
              </td>
              <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb;text-align:center">
                <span style="background:#dbeafe;color:#1d4ed8;padding:2px 8px;border-radius:9999px;font-size:13px;font-weight:600">{pct}</span>
              </td>
              <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;color:#4b5563">
                {reasons_str}
              </td>
              <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb">
                <a href="{link}" style="background:#2563eb;color:#fff;padding:6px 14px;border-radius:6px;text-decoration:none;font-size:13px">Apply →</a>
              </td>
            </tr>"""

        html = f"""
        <html><body style="font-family:system-ui,sans-serif;color:#111;max-width:800px;margin:0 auto;padding:24px">
          <h2 style="margin-bottom:4px">🎯 {len(matches)} new job match{'es' if len(matches) != 1 else ''}</h2>
          <p style="color:#6b7280;margin-top:0">Sorted by match score. Review and apply below.</p>
          <table style="width:100%;border-collapse:collapse">
            <thead>
              <tr style="background:#f9fafb">
                <th style="padding:8px;text-align:left;font-size:13px;color:#6b7280">Company / Role</th>
                <th style="padding:8px;font-size:13px;color:#6b7280">Score</th>
                <th style="padding:8px;text-align:left;font-size:13px;color:#6b7280">Why matched</th>
                <th style="padding:8px;font-size:13px;color:#6b7280">Action</th>
              </tr>
            </thead>
            <tbody>{html_rows}</tbody>
          </table>
          <p style="color:#9ca3af;font-size:12px;margin-top:24px">Sent by your Job Agent · jobs.db has full history</p>
        </body></html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = config.NOTIFY_EMAIL
        msg["To"]      = config.NOTIFY_EMAIL
        msg.attach(MIMEText(html, "html"))

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(config.NOTIFY_EMAIL, config.GMAIL_APP_PASSWORD)
                server.send_message(msg)
            log.info("Email digest sent.")
        except Exception as e:
            log.error(f"Email notification failed: {e}")

    # ── Console fallback ──────────────────────────────────────────────────

    def _print_digest(self, matches: list):
        print("\n" + "="*60)
        print(f"  JOB DIGEST — {len(matches)} new match{'es' if len(matches) != 1 else ''}")
        print("="*60)
        for job, score, reasons in matches:
            loc = "Remote" if job.is_remote else job.location or "—"
            print(f"\n  [{score:.0%}] {job.company} — {job.title}")
            print(f"         {loc}")
            print(f"         {job.apply_url}")
            for r in reasons[:3]:
                print(f"         • {r}")
        print("\n" + "="*60 + "\n")
