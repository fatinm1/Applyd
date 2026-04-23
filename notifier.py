"""
notifier.py — Sends match digests via Slack webhook and/or email.
"""

import json
import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, List, Optional
from urllib.parse import quote

import requests

from config import config
from matcher import generate_cover_letter
from parser import Job
from resume_tailer import generate_tailored_resume_pdf

log = logging.getLogger(__name__)


class Notifier:
    @staticmethod
    def _smtp_configured() -> bool:
        return bool((config.NOTIFY_EMAIL or "").strip() and (config.GMAIL_APP_PASSWORD or "").strip())

    @staticmethod
    def normalize_email_recipients(recipients: Optional[List[str]]) -> List[str]:
        """
        If `recipients` is None, default to NOTIFY_EMAIL (single address when set).
        Dedupes case-insensitively while preserving the first spelling.
        """
        if recipients is None:
            raw = [config.NOTIFY_EMAIL] if (config.NOTIFY_EMAIL or "").strip() else []
        else:
            raw = list(recipients)
        out: dict[str, str] = {}
        for r in raw:
            e = (r or "").strip()
            if e:
                out.setdefault(e.lower(), e)
        return list(out.values())

    def send_digest(
        self,
        matches: list[tuple[Job, float, list[str]]],
        *,
        recipients: Optional[List[str]] = None,
    ):
        """Send a digest of new job matches. matches = [(job, score, reasons), ...]"""
        matches_sorted = sorted(matches, key=lambda x: x[1], reverse=True)

        if config.SLACK_WEBHOOK_URL:
            self._send_slack(matches_sorted)

        tos = self.normalize_email_recipients(recipients)
        email_sent = False
        if tos and self._smtp_configured():
            for to_addr in tos:
                self._send_email(matches_sorted, to_addr=to_addr)
            log.info("Email digest sent to %s recipient(s).", len(tos))
            email_sent = True

        if not config.SLACK_WEBHOOK_URL and not email_sent:
            log.warning("No notification channel configured. Set SLACK_WEBHOOK_URL or NOTIFY_EMAIL.")
            self._print_digest(matches_sorted)

    def send_match_approval_request_emails(
        self,
        matches: list[tuple[Job, float, list[str]]],
        store: Optional[Any] = None,
        *,
        recipients: Optional[List[str]] = None,
    ):
        """
        Send per-job approval emails with signed approve/reject links.

        This is intentionally separate from the digest email: digest is a quick scan;
        approval emails include the resume PDF + cover letter attachments.
        """
        if not config.EMAIL_APPROVAL_REQUESTS_ENABLED:
            return

        if not self._smtp_configured():
            log.warning("EMAIL_APPROVAL_REQUESTS_ENABLED is true, but Gmail creds are missing.")
            return

        if not (config.PUBLIC_BASE_URL or "").strip():
            log.warning("EMAIL_APPROVAL_REQUESTS_ENABLED is true, but PUBLIC_BASE_URL is missing.")
            return

        # Lazy import to avoid circular imports at module import time.
        if store is None:
            from store import JobStore

            store = JobStore()

        tos = self.normalize_email_recipients(recipients)
        if not tos:
            log.warning("No recipient addresses for approval-request emails (set NOTIFY_EMAIL or user notification emails).")
            return

        base = config.PUBLIC_BASE_URL.rstrip("/")

        for job, score, reasons in matches:
            try:
                # Avoid spamming duplicate approval emails for the same job.
                sent_at = store.get_mail_action_sent_at(job.id)
                if sent_at:
                    continue

                token, _exp_iso = store.issue_mail_action_token(job.id)
                tok_q = quote(token, safe="")

                approve_url = f"{base}/api/mail/approve?job_id={quote(job.id)}&token={tok_q}"
                reject_url = f"{base}/api/mail/reject?job_id={quote(job.id)}&token={tok_q}"

                # Ensure we have a cover letter for attachment/preview.
                row = store.get_job(job.id) or {}
                cover = (row.get("cover_letter") or "").strip()
                if not cover:
                    cover = generate_cover_letter(job)
                    store.update_status(job.id, status="pending", cover_letter=cover)

                # Ensure we have the resume PDF that would be submitted.
                resume_path = (row.get("resume_pdf_path") or "").strip()
                if not resume_path:
                    resume_path = generate_tailored_resume_pdf(job)
                    if resume_path:
                        store.set_job_resume_pdf(job.id, resume_path)

                pct = f"{score:.0%}"
                loc = "Remote" if job.is_remote else (job.location or "—")
                apply_link = job.apply_url or f"https://github.com/{job.source}"
                reasons_html = "<br>".join(f"• {r}" for r in (reasons or [])[:8])
                reasons_txt = "\n".join(f"- {r}" for r in (reasons or [])[:8])

                subject = f"Approve this match? {job.company} — {job.title} ({pct})"

                text_body = f"""Applyd found a new match.

Company: {job.company}
Role: {job.title}
Location: {loc}
Match score: {pct}
Apply link: {apply_link}

Why it matched:
{reasons_txt}

Approve (one click):
{approve_url}

Reject (one click):
{reject_url}

Attachments:
- proposed_resume.pdf (if present)
- cover_letter.txt
"""

                html_body = f"""
                <html><body style="font-family:system-ui,sans-serif;color:#111;max-width:760px;margin:0 auto;padding:24px;line-height:1.5">
                  <h2 style="margin-top:0">New match — approval requested</h2>
                  <p><strong>{job.company}</strong> — {job.title}<br>
                     <span style="color:#6b7280">{loc}</span><br>
                     <span style="display:inline-block;margin-top:8px;background:#dbeafe;color:#1d4ed8;padding:2px 10px;border-radius:9999px;font-size:13px;font-weight:700">Match: {pct}</span>
                  </p>

                  <p><strong>Apply link:</strong> <a href="{apply_link}">{apply_link}</a></p>

                  <h3 style="margin-bottom:8px">Why it matched</h3>
                  <div style="font-size:14px;color:#374151">{reasons_html}</div>

                  <div style="margin-top:18px;display:flex;gap:10px;flex-wrap:wrap">
                    <a href="{approve_url}" style="background:#16a34a;color:#fff;padding:10px 14px;border-radius:10px;text-decoration:none;font-weight:700">Approve</a>
                    <a href="{reject_url}" style="background:#ef4444;color:#fff;padding:10px 14px;border-radius:10px;text-decoration:none;font-weight:700">Reject</a>
                  </div>

                  <p style="color:#6b7280;font-size:12px;margin-top:18px">
                    These links expire after {config.MAIL_ACTION_EXPIRY_HOURS} hours. If you didn’t request this, ignore the email.
                  </p>
                </body></html>
                """

                all_ok = True
                for to_addr in tos:
                    msg = MIMEMultipart("mixed")
                    msg["Subject"] = subject
                    msg["From"] = config.NOTIFY_EMAIL
                    msg["To"] = to_addr
                    alt = MIMEMultipart("alternative")
                    alt.attach(MIMEText(text_body, "plain"))
                    alt.attach(MIMEText(html_body, "html"))
                    msg.attach(alt)
                    cl2 = MIMEText(cover, "plain", _charset="utf-8")
                    cl2.add_header("Content-Disposition", "attachment", filename="cover_letter.txt")
                    msg.attach(cl2)
                    if resume_path and os.path.exists(resume_path):
                        with open(resume_path, "rb") as f:
                            pdf = MIMEApplication(f.read(), _subtype="pdf")
                            pdf.add_header("Content-Disposition", "attachment", filename="proposed_resume.pdf")
                            msg.attach(pdf)
                    try:
                        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                            server.login(config.NOTIFY_EMAIL, config.GMAIL_APP_PASSWORD)
                            server.send_message(msg)
                    except Exception as send_exc:
                        all_ok = False
                        log.error("Approval request email failed for %s → %s: %s", job.display, to_addr, send_exc)

                if all_ok:
                    store.mark_mail_action_sent(job.id)
                    log.info("Approval request email sent for %s (%s recipient(s))", job.display, len(tos))
            except Exception as e:
                log.error(f"Approval request email failed for {job.display}: {e}")

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

    def _send_email(self, matches: list, *, to_addr: Optional[str] = None):
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

        dest = (to_addr or config.NOTIFY_EMAIL or "").strip()
        if not dest:
            log.error("Email digest skipped: no recipient address.")
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = config.NOTIFY_EMAIL
        msg["To"]      = dest
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
