"""
applier.py — Phase 2: Auto-apply to approved jobs using Playwright.

Only runs when AUTO_APPLY_ENABLED = true in config.
Handles both direct application links and email-to-apply jobs.

Install: pip install playwright && playwright install chromium
"""

import logging
import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import config
from store import JobStore
from matcher import generate_cover_letter
from parser import Job
from resume_tailer import generate_tailored_resume_pdf

log = logging.getLogger(__name__)


def run_auto_apply():
    """Process all approved jobs and attempt to apply."""
    store = JobStore()
    if not store.get_auto_apply_enabled():
        log.info("Auto-apply is disabled via agent settings.")
        return

    # IMPORTANT: `store.get_all()` is capped by a small LIMIT, which can cause
    # approved jobs to fall outside the window and never be processed.
    # Fetch by status with a large limit instead.
    approved = store.get_by_status("approved", limit=10000)

    if not approved:
        log.info("No approved jobs to apply to.")
        return

    log.info(f"Auto-applying to {len(approved)} approved jobs...")

    for job_row in approved:
        job = Job(
            id=job_row["id"], company=job_row["company"], title=job_row["title"],
            location=job_row["location"], apply_url=job_row["apply_url"],
            source=job_row["source"], body=job_row.get("body", ""),
            is_remote=bool(job_row["is_remote"]),
        )

        # Get or generate cover letter
        cover_letter = job_row.get("cover_letter") or generate_cover_letter(job)
        resume_path = job_row.get("resume_pdf_path") or ""
        if not resume_path:
            resume_path = generate_tailored_resume_pdf(job)

        success = False
        method  = "unknown"

        apply_url = job.apply_url or ""

        if _is_email_apply(apply_url):
            success = _apply_via_email(job, apply_url, cover_letter, resume_path)
            method  = "email"
        elif apply_url.startswith("http"):
            success = _apply_via_browser(job, apply_url, cover_letter, resume_path)
            method  = "browser"
        else:
            log.warning(f"No apply URL for {job.display} — skipping")
            store.update_status(job.id, "skipped", notes="No apply URL")
            continue

        if success:
            store.update_status(job.id, "applied", cover_letter=cover_letter,
                                notes=f"Applied via {method}")
            log.info(f"  ✅ Applied: {job.display}")
        else:
            store.update_status(job.id, "skipped", notes=f"Apply failed via {method}")
            log.warning(f"  ❌ Failed: {job.display}")

        time.sleep(3)  # Be polite


# ── Email apply ──────────────────────────────────────────────────────────────

def _is_email_apply(url: str) -> bool:
    return url.startswith("mailto:") or re.match(r'^[\w.+-]+@[\w-]+\.[\w.]+$', url)


def _apply_via_email(job: Job, address: str, cover_letter: str, resume_path: str) -> bool:
    if not config.NOTIFY_EMAIL or not config.GMAIL_APP_PASSWORD:
        log.error("Gmail credentials not configured for email apply.")
        return False

    to_addr = address.replace("mailto:", "").split("?")[0].strip()

    subject = f"Application: {job.title} — {config.YOUR_NAME}"

    # Build HTML body without putting escaped newlines inside an f-string
    # expression (Python forbids backslashes inside `{...}` in f-strings).
    paras = [p.strip() for p in cover_letter.split("\n\n") if p.strip()]
    paras_html = "".join(f"<p>{p}</p>" for p in paras)
    body_html = f"""
    <html><body style="font-family:Georgia,serif;max-width:640px;margin:0 auto;color:#1a1a1a;line-height:1.7">
      <p>Dear Hiring Team,</p>
      {paras_html}
      <p>Best regards,<br><strong>{config.YOUR_NAME}</strong><br>
      <a href="mailto:{config.YOUR_EMAIL}">{config.YOUR_EMAIL}</a></p>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{config.YOUR_NAME} <{config.YOUR_EMAIL}>"
    msg["To"]      = to_addr
    msg.attach(MIMEText(cover_letter, "plain"))
    msg.attach(MIMEText(body_html,    "html"))

    # Attach resume PDF if available
    try:
        import os
        from email.mime.application import MIMEApplication
        if resume_path and os.path.exists(resume_path):
            with open(resume_path, "rb") as f:
                pdf = MIMEApplication(f.read(), _subtype="pdf")
                pdf.add_header("Content-Disposition", "attachment",
                               filename=f"{config.YOUR_NAME.replace(' ', '_')}_Resume.pdf")
                msg.attach(pdf)
    except Exception as e:
        log.warning(f"Could not attach resume: {e}")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(config.NOTIFY_EMAIL, config.GMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        log.error(f"Email apply failed: {e}")
        return False


# ── Browser apply (Playwright) ────────────────────────────────────────────────

def _apply_via_browser(job: Job, url: str, cover_letter: str, resume_path: str) -> bool:
    """
    Attempt to fill out a web application form.

    This is inherently fragile — every job board has a different form.
    The strategy: detect which platform it is and use a known handler,
    or fall back to a generic "find the fields and fill them" heuristic.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page()
            # Use a shorter, more reliable readiness signal.
            # Many job boards keep long-polling / tracking connections open,
            # so `networkidle` frequently times out.
            page.goto(url, timeout=20_000, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5_000)
            except Exception:
                # If the signal isn't reached, still attempt to fill fields.
                pass
            # Small grace period for late DOM injections.
            page.wait_for_timeout(1500)

            # Detect platform
            domain = _extract_domain(url)
            success = False

            if "lever.co" in domain:
                success = _fill_lever(page, cover_letter, resume_path)
            elif "greenhouse.io" in domain or "boards.greenhouse" in domain:
                success = _fill_greenhouse(page, cover_letter, resume_path)
            elif "simplify.jobs" in domain:
                success = _fill_simplify(page, cover_letter, resume_path)
            elif "workday" in domain:
                log.warning("Workday forms are complex — manual apply recommended")
                success = False
            else:
                success = _fill_generic(page, cover_letter, resume_path)

            browser.close()
            return success

    except Exception as e:
        log.error(f"Browser apply error: {e}")
        return False


def _fill_lever(page, cover_letter: str, resume_path: str) -> bool:
    try:
        _safe_fill(page, 'input[name="name"]',       config.YOUR_NAME)
        _safe_fill(page, 'input[name="email"]',      config.YOUR_EMAIL)
        _safe_fill(page, 'textarea[name="comments"]', cover_letter)
        page.wait_for_timeout(1000)

        # Upload resume if field exists
        resume_input = page.query_selector('input[type="file"]')
        if resume_input and resume_path:
            import os
            if os.path.exists(resume_path):
                resume_input.set_input_files(resume_path)

        # Submit
        submit_btn = page.query_selector('button[type="submit"], input[type="submit"]')
        if submit_btn:
            submit_btn.click()
            page.wait_for_timeout(3000)
            return True
    except Exception as e:
        log.warning(f"Lever fill error: {e}")
    return False


def _fill_greenhouse(page, cover_letter: str, resume_path: str) -> bool:
    try:
        _safe_fill(page, '#first_name',  config.YOUR_NAME.split()[0])
        _safe_fill(page, '#last_name',   config.YOUR_NAME.split()[-1])
        _safe_fill(page, '#email',       config.YOUR_EMAIL)
        _safe_fill(page, '#cover_letter_text', cover_letter)
        page.wait_for_timeout(1000)

        resume_input = page.query_selector('input#resume')
        if resume_input and resume_path:
            import os
            if os.path.exists(resume_path):
                resume_input.set_input_files(resume_path)

        submit_btn = page.query_selector('#submit_app')
        if submit_btn:
            submit_btn.click()
            page.wait_for_timeout(3000)
            return True
    except Exception as e:
        log.warning(f"Greenhouse fill error: {e}")
    return False


def _fill_simplify(page, cover_letter: str, resume_path: str) -> bool:
    """
    targeted best-effort apply for `simplify.jobs` links.
    Unlike `_fill_generic`, we explicitly try to click the submit/apply button.
    """
    try:
        # Basic identity fields.
        for sel in [
            'input[name*="name" i]',
            'input[placeholder*="name" i]',
            '#name',
            'input[name="fullName" i]',
        ]:
            if _safe_fill(page, sel, config.YOUR_NAME):
                break

        for sel in [
            'input[type="email"]',
            'input[name*="email" i]',
            'input[placeholder*="email" i]',
        ]:
            if _safe_fill(page, sel, config.YOUR_EMAIL):
                break

        # Cover letter.
        for sel in [
            'textarea[name*="cover" i]',
            'textarea[name*="letter" i]',
            'textarea[name*="message" i]',
            'textarea[aria-label*="cover" i]',
            'textarea[aria-label*="letter" i]',
        ]:
            if _safe_fill(page, sel, cover_letter):
                break

        # Resume upload (if a file input exists).
        if resume_path:
            try:
                resume_input = page.query_selector('input[type="file"]')
                if resume_input and __import__("os").path.exists(resume_path):
                    resume_input.set_input_files(resume_path)
            except Exception:
                pass

        # Submit:
        old_url = page.url

        # Try multiple clickable element types.
        # Simplify pages frequently use non-standard markup, so we match on visible text,
        # aria-label, title, or value attributes.
        label_patterns = [
            r"(apply|submit)",
            r"(continue|next|send)",
        ]
        priority_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button',
            'a',
            '[role="button"]',
        ]

        clicked = False
        for sel in priority_selectors:
            elements = page.query_selector_all(sel)
            if not elements:
                continue
            for el in elements:
                label_parts: list[str] = []
                try:
                    label_parts.append(el.inner_text() or "")
                except Exception:
                    pass
                try:
                    label_parts.append(el.get_attribute("aria-label") or "")
                except Exception:
                    pass
                try:
                    label_parts.append(el.get_attribute("title") or "")
                except Exception:
                    pass
                try:
                    label_parts.append(el.get_attribute("value") or "")
                except Exception:
                    pass

                label = " ".join(p.strip() for p in label_parts if p and p.strip())
                if not label:
                    continue
                if any(re.search(pat, label, flags=re.IGNORECASE) for pat in label_patterns):
                    try:
                        el.scroll_into_view_if_needed()
                    except Exception:
                        pass
                    el.click()
                    clicked = True
                    break
            if clicked:
                break

        if not clicked:
            log.warning("Simplify apply: submit action not found (no matching button/link).")
            return False

        # Wait briefly for redirect / confirmation.
        try:
            page.wait_for_timeout(1500)
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass

        # Success heuristic: URL changed or confirmation text appears.
        new_url = page.url
        if new_url and new_url != old_url:
            return True

        content = page.content() or ""
        return bool(re.search(r"(application submitted|thank you|success|received)", content, flags=re.IGNORECASE))
    except Exception as e:
        log.warning(f"Simplify fill error: {e}")
        return False


def _fill_generic(page, cover_letter: str, resume_path: str) -> bool:
    """Best-effort heuristic for unknown forms."""
    try:
        # Name
        for sel in ['input[name*="name"]', 'input[placeholder*="name" i]', '#name']:
            if _safe_fill(page, sel, config.YOUR_NAME):
                break
        # Email
        for sel in ['input[type="email"]', 'input[name*="email"]']:
            if _safe_fill(page, sel, config.YOUR_EMAIL):
                break
        # Cover letter
        for sel in ['textarea[name*="cover"]', 'textarea[name*="letter"]',
                    'textarea[name*="message"]', 'textarea']:
            if _safe_fill(page, sel, cover_letter):
                break

        # Resume upload best-effort (do NOT auto-submit).
        if resume_path:
            try:
                resume_input = page.query_selector('input[type="file"]')
                if resume_input and __import__("os").path.exists(resume_path):
                    resume_input.set_input_files(resume_path)
            except Exception:
                pass

        log.info("Generic form fill attempted — no auto-submit (safety)")
        return False  # Don't auto-submit unknown forms
    except Exception as e:
        log.warning(f"Generic fill error: {e}")
        return False


def _safe_fill(page, selector: str, value: str) -> bool:
    try:
        el = page.query_selector(selector)
        if el:
            el.fill(value)
            return True
    except Exception:
        pass
    return False


def _extract_domain(url: str) -> str:
    m = re.search(r'https?://([^/]+)', url)
    return m.group(1) if m else url
