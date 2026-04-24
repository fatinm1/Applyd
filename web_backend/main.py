"""
FastAPI backend for the Applyd web dashboard.

Provides session-authenticated APIs backed by `jobs.db` (JobStore) and runs a
background worker that triggers the existing scan/auto-apply loop.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from agent import REPOS, run_scan_cycle_and_apply
from config import config
from notifier import Notifier
from matcher import JobMatcher, generate_cover_letter, _fallback_cover_letter
from parser import Job, JobParser
from watcher import GitHubWatcher
from store import JobStore
from resume_tailer import generate_tailored_resume_pdf

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


app = FastAPI(title="Applyd Dashboard API")

# Use the project-wide DB.
store = JobStore()

# CORS so the Next.js dashboard can call this API from a different port.
allowed_origins = [o.strip() for o in config.CORS_ALLOW_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET_KEY,
    same_site="lax",
)

_worker_stop = threading.Event()
_worker_thread: Optional[threading.Thread] = None
_worker_lock = threading.Lock()

_cycle_lock = threading.Lock()
_manual_run_lock = threading.Lock()
_last_manual_run_ts = 0.0


def _require_auth(request: Request) -> str:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return str(user)


def _session_user_id(request: Request) -> Optional[int]:
    raw = request.session.get("user_id")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    un = request.session.get("user")
    if un:
        row = store.get_user_by_username(str(un))
        if row:
            try:
                return int(row["id"])
            except (TypeError, ValueError, KeyError):
                pass
    return None


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    notification_email: str = ""
    invite_code: str = ""


class MePatchRequest(BaseModel):
    notification_email: str = ""


class DeleteAccountRequest(BaseModel):
    password: str


class RejectRequest(BaseModel):
    notes: str = ""


class AgentStateUpdate(BaseModel):
    agent_enabled: Optional[bool] = None
    auto_apply_enabled: Optional[bool] = None


class DemoRunRequest(BaseModel):
    # How many example jobs to sample per repo.
    sample_per_source: int = 5
    # Keep responses small; we include only a short excerpt.
    body_excerpt_chars: int = 300


def _job_row_to_response(job_row: dict[str, Any]) -> dict[str, Any]:
    # Normalize types for the frontend.
    match_reasons_raw = job_row.get("match_reasons") or "[]"
    try:
        match_reasons = json.loads(match_reasons_raw)
    except Exception:
        match_reasons = []

    return {
        **job_row,
        "match_reasons": match_reasons,
        "is_remote": bool(job_row.get("is_remote")),
    }


def _background_worker():
    poll_seconds = max(int(config.POLL_INTERVAL_MINUTES * 60), 30)
    idle_seconds = 15

    log.info("Background worker started.")
    while not _worker_stop.is_set():
        try:
            settings = store.get_agent_settings()
            if settings.get("agent_enabled", True):
                # Serialize cycles so manual runs can't overlap the worker.
                with _cycle_lock:
                    run_scan_cycle_and_apply()
                _worker_stop.wait(timeout=poll_seconds)
            else:
                log.info("Agent is disabled; sleeping.")
                _worker_stop.wait(timeout=idle_seconds)
        except Exception as e:
            log.exception(f"Background worker error: {e}")
            _worker_stop.wait(timeout=60)

    log.info("Background worker stopped.")


def _ensure_worker_running():
    global _worker_thread
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _worker_stop.clear()
        _worker_thread = threading.Thread(target=_background_worker, daemon=True)
        _worker_thread.start()


@app.on_event("startup")
def _startup():
    _ensure_worker_running()


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request):
    if store.count_users() == 0:
        raise HTTPException(
            status_code=401,
            detail=(
                "No accounts yet. Either set DASHBOARD_USERNAME and DASHBOARD_PASSWORD on the server and restart "
                "once to bootstrap an admin account, or enable self-service sign-up (ALLOW_OPEN_REGISTRATION=true "
                "or REGISTRATION_INVITE_CODE) and use /register."
            ),
        )

    uid = store.verify_user_password(payload.username, payload.password)
    if uid is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    row = store.get_user_by_id(uid)
    un = (row or {}).get("username") or payload.username
    request.session["user"] = str(un)
    request.session["user_id"] = uid
    return {"ok": True, "user_id": uid, "username": str(un)}


@app.get("/api/auth/registration")
def registration_status():
    """Public: whether /register is usable and what the client should collect."""
    open_reg = bool(config.ALLOW_OPEN_REGISTRATION)
    invite = (config.REGISTRATION_INVITE_CODE or "").strip()
    invite_set = bool(invite)
    allowed = open_reg or invite_set
    return {
        "allowed": allowed,
        "open_registration": open_reg,
        "invite_required": invite_set and not open_reg,
    }


@app.post("/api/auth/register")
def register(payload: RegisterRequest):
    open_reg = bool(config.ALLOW_OPEN_REGISTRATION)
    expected = (config.REGISTRATION_INVITE_CODE or "").strip()

    if open_reg:
        pass
    elif expected:
        if payload.invite_code.strip() != expected:
            raise HTTPException(status_code=403, detail="Invalid invite code.")
    else:
        raise HTTPException(
            status_code=403,
            detail="Registration is disabled. Set ALLOW_OPEN_REGISTRATION=true or REGISTRATION_INVITE_CODE in server env.",
        )

    u = payload.username.strip()
    if len(u) < 2 or len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Username (min 2 chars) and password (min 6 chars) required.")

    notif = _notification_email_for_new_user(u, payload.notification_email)

    try:
        uid = store.create_user(u, payload.password, notif)
    except Exception as e:
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(status_code=409, detail="Username already taken")
        raise HTTPException(status_code=400, detail=str(e))

    welcome_sent = False
    welcome_status = "skipped"
    try:
        welcome_sent, welcome_status = Notifier().send_registration_welcome(to_email=notif, username=u)
    except Exception as e:
        log.error("Registration welcome unexpected error: %s", e, exc_info=True)
        welcome_status = "smtp_failed"

    return {
        "ok": True,
        "user_id": uid,
        "welcome_email_sent": welcome_sent,
        "welcome_email_status": welcome_status,
    }


def _validate_notification_email(raw: str) -> str:
    em = (raw or "").strip()
    if not em:
        return ""
    if "@" not in em or "." not in em.split("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="Invalid notification_email")
    return em


# If the user only fills "username" with their campus/personal email, still persist and mail it.
_REGISTRATION_EMAILISH = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", re.IGNORECASE)


def _notification_email_for_new_user(username: str, notification_email: str) -> str:
    n = (notification_email or "").strip()
    if n:
        return n
    u = (username or "").strip()
    if _REGISTRATION_EMAILISH.match(u):
        return u
    return ""


@app.get("/api/me")
def get_me(request: Request, _user: str = Depends(_require_auth)):
    uid = _session_user_id(request)
    if uid is None:
        raise HTTPException(status_code=401, detail="Session missing user id; log in again.")
    row = store.get_user_by_id(uid)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "user_id": uid,
        "username": row.get("username") or "",
        "notification_email": (row.get("notification_email") or "").strip(),
        "is_admin": store.user_is_admin(uid),
    }


@app.patch("/api/me")
def patch_me(payload: MePatchRequest, request: Request, _user: str = Depends(_require_auth)):
    uid = _session_user_id(request)
    if uid is None:
        raise HTTPException(status_code=401, detail="Session missing user id; log in again.")
    em = _validate_notification_email(payload.notification_email)
    store.update_user_notification_email(uid, em)
    row = store.get_user_by_id(uid)
    return {
        "user_id": uid,
        "username": (row or {}).get("username") or "",
        "notification_email": em,
        "is_admin": store.user_is_admin(uid),
    }


@app.post("/api/me/delete")
def delete_my_account(payload: DeleteAccountRequest, request: Request, _user: str = Depends(_require_auth)):
    uid = _session_user_id(request)
    if uid is None:
        raise HTTPException(status_code=401, detail="Session missing user id; log in again.")
    row = store.get_user_by_id(uid)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if store.user_is_admin(uid):
        raise HTTPException(status_code=403, detail="The administrator account cannot be deleted.")
    username = str(row.get("username") or "")
    if not (payload.password or "").strip():
        raise HTTPException(status_code=400, detail="Password is required to delete your account.")
    if store.verify_user_password(username, payload.password) is None:
        raise HTTPException(status_code=400, detail="Incorrect password.")

    notif_em = _notification_email_for_new_user(username, str(row.get("notification_email") or ""))
    deletion_sent = False
    deletion_status = "skipped"
    try:
        deletion_sent, deletion_status = Notifier().send_account_deleted(to_email=notif_em, username=username)
    except Exception as e:
        log.error("Account-deletion email unexpected error: %s", e, exc_info=True)
        deletion_status = "smtp_failed"

    if not store.delete_user_by_id(uid):
        raise HTTPException(status_code=403, detail="Could not delete account.")
    request.session.clear()
    return {
        "ok": True,
        "deletion_email_sent": deletion_sent,
        "deletion_email_status": deletion_status,
    }


@app.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/jobs")
def list_jobs(
    status: str = "pending",
    limit: int = 200,
    _user: str = Depends(_require_auth),
):
    allowed = {"pending", "approved", "applied", "rejected", "skipped"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid status. Allowed: {sorted(allowed)}")

    jobs = store.get_by_status(status, limit=limit)
    return {"jobs": [_job_row_to_response(j) for j in jobs]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, _user: str = Depends(_require_auth)):
    job_row = store.get_job(job_id)
    if not job_row:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": _job_row_to_response(job_row)}


def _update_status(job_id: str, status: str, notes: str = "", cover_letter: str = "") -> dict[str, Any]:
    if not store.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    store.update_status(job_id, status=status, notes=notes, cover_letter=cover_letter)
    job_row = store.get_job(job_id)
    if not job_row:
        raise HTTPException(status_code=404, detail="Job not found after update")
    return _job_row_to_response(job_row)


def _approve_job_core(job_id: str) -> dict[str, Any]:
    """
    Shared approval path used by the dashboard and email one-click links.
    """
    _update_status(job_id, status="approved")

    # Generate and cache a job-specific resume PDF at approval time.
    # This keeps Phase 2 simple: it just attaches/uploads the cached PDF.
    try:
        job_row = store.get_job(job_id)
        if job_row:
            resume_path = job_row.get("resume_pdf_path") or ""
            if not resume_path:
                job = Job(
                    id=job_row["id"],
                    company=job_row["company"],
                    title=job_row["title"],
                    location=job_row.get("location") or "",
                    apply_url=job_row.get("apply_url") or "",
                    source=job_row.get("source") or "",
                    date_posted=job_row.get("date_posted") or "",
                    body=job_row.get("body") or "",
                    is_remote=bool(job_row.get("is_remote")),
                )
                resume_path = generate_tailored_resume_pdf(job)
                store.set_job_resume_pdf(job_id, resume_path)
    except Exception as e:
        log.exception(f"Failed to generate tailored resume for {job_id}: {e}")

    job_row = store.get_job(job_id)
    if not job_row:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_row_to_response(job_row)


# Shown when /api/mail/* is opened but this server has no row for job_id (common
# mistake: approval email generated against local SQLite while PUBLIC_BASE_URL
# pointed at production, which uses a different database).
_MAIL_JOB_MISSING_HELP = """
<p>That job id is not in <strong>this</strong> server’s database.</p>
<p>Approval links always talk to the app behind <code>PUBLIC_BASE_URL</code>. That app must use the
<strong>same database</strong> where the job and signed token were written (usually your deployed
worker on Railway, not a one-off script on your laptop).</p>
<p><strong>Typical fixes:</strong> run the agent / scan worker on the deployment that owns your
production DB; or, for local testing, set <code>PUBLIC_BASE_URL</code> to a tunnel to your machine
and run the API with the same <code>jobs.db</code> you used when sending the email.</p>
"""


def _mail_action_page(title: str, body: str, ok: bool = True) -> HTMLResponse:
    color = "#16a34a" if ok else "#dc2626"
    html = f"""
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1" />
      <title>{title}</title>
    </head>
    <body style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:24px;color:#111">
      <h2 style="margin:0 0 12px;color:{color}">{title}</h2>
      <div style="line-height:1.6">{body}</div>
      <p style="margin-top:22px;color:#6b7280;font-size:12px">Applyd mail action</p>
    </body></html>
    """
    return HTMLResponse(content=html, status_code=200 if ok else 400)


def _verify_mail_token(job_id: str, token: str) -> None:
    row = store.get_mail_action_token_row(job_id)
    if not row or not row.get("mail_action_token"):
        raise HTTPException(status_code=400, detail="Missing mail token for job")

    if row.get("mail_action_token") != token:
        raise HTTPException(status_code=400, detail="Invalid mail token")

    if not store.verify_mail_action_token(job_id, token):
        raise HTTPException(status_code=400, detail="Invalid/expired mail token")


@app.get("/api/mail/approve", response_class=HTMLResponse)
def mail_approve(job_id: str, token: str):
    try:
        job_row = store.get_job(job_id)
        if not job_row:
            return _mail_action_page("Not found", _MAIL_JOB_MISSING_HELP, ok=False)
        if (job_row.get("status") or "pending") != "pending":
            return _mail_action_page("Already handled", f"This job is no longer pending (status={job_row.get('status')}).", ok=False)

        _verify_mail_token(job_id, token)
        _approve_job_core(job_id)
        return _mail_action_page("Approved", "This job was approved. If auto-apply is enabled, it will be processed on the next run.")
    except HTTPException as e:
        return _mail_action_page("Could not approve", str(e.detail), ok=False)


@app.get("/api/mail/reject", response_class=HTMLResponse)
def mail_reject(job_id: str, token: str):
    try:
        job_row = store.get_job(job_id)
        if not job_row:
            return _mail_action_page("Not found", _MAIL_JOB_MISSING_HELP, ok=False)
        if (job_row.get("status") or "pending") != "pending":
            return _mail_action_page("Already handled", f"This job is no longer pending (status={job_row.get('status')}).", ok=False)

        _verify_mail_token(job_id, token)
        store.update_status(job_id, status="rejected", notes="Rejected via email link")
        return _mail_action_page("Rejected", "This job was rejected.")
    except HTTPException as e:
        return _mail_action_page("Could not reject", str(e.detail), ok=False)


@app.post("/api/jobs/{job_id}/approve")
def approve_job(job_id: str, _user: str = Depends(_require_auth)):
    return {"job": _approve_job_core(job_id)}


@app.post("/api/jobs/{job_id}/skip")
def skip_job(job_id: str, _user: str = Depends(_require_auth)):
    return {"job": _update_status(job_id, status="skipped")}


@app.post("/api/jobs/{job_id}/reject")
def reject_job(job_id: str, payload: RejectRequest, _user: str = Depends(_require_auth)):
    return {"job": _update_status(job_id, status="rejected", notes=payload.notes)}


@app.post("/api/jobs/{job_id}/cover-letter")
def generate_cover_letter_endpoint(job_id: str, _user: str = Depends(_require_auth)):
    job_row = store.get_job(job_id)
    if not job_row:
        raise HTTPException(status_code=404, detail="Job not found")

    job = Job(
        id=job_row["id"],
        company=job_row["company"],
        title=job_row["title"],
        location=job_row.get("location") or "",
        apply_url=job_row.get("apply_url") or "",
        source=job_row.get("source") or "",
        date_posted=job_row.get("date_posted") or "",
        body=job_row.get("body") or "",
        is_remote=bool(job_row.get("is_remote")),
    )

    letter = generate_cover_letter(job)
    # Preserve current status; only update cover letter.
    store.update_status(job_id, status=job_row.get("status") or "pending", cover_letter=letter)
    job_row = store.get_job(job_id)
    return {"job": _job_row_to_response(job_row), "cover_letter": letter}


@app.get("/api/stats")
def stats(_user: str = Depends(_require_auth)):
    return store.stats()


@app.get("/api/agent/state")
def agent_state(_user: str = Depends(_require_auth)):
    return store.get_agent_settings()


@app.post("/api/agent/state")
def agent_state_update(payload: AgentStateUpdate, _user: str = Depends(_require_auth)):
    if payload.agent_enabled is not None:
        store.set_agent_enabled(bool(payload.agent_enabled))
    if payload.auto_apply_enabled is not None:
        store.set_auto_apply_enabled(bool(payload.auto_apply_enabled))
    return store.get_agent_settings()


@app.post("/api/agent/run")
def run_now(request: Request, _user: str = Depends(_require_auth)):
    # Simple in-memory debounce (prevents spamming Claude/GitHub).
    global _last_manual_run_ts
    now = __import__("time").time()

    with _manual_run_lock:
        if now - _last_manual_run_ts < 60:
            raise HTTPException(status_code=429, detail="Run requested too soon; wait 60s.")
        _last_manual_run_ts = now

    nid = _session_user_id(request)
    with _cycle_lock:
        run_scan_cycle_and_apply(notification_user_id=nid)
    return {"ok": True}


def _estimate_auto_apply_handler(apply_url: str) -> str:
    u = (apply_url or "").lower()
    if "simplify.jobs" in u:
        return "simplify.jobs (targeted Apply/Submit click)"
    if "lever.co" in u:
        return "lever.co (generic submit)"
    if "greenhouse.io" in u or "boards.greenhouse" in u:
        return "greenhouse.io (generic submit)"
    if "workday" in u:
        return "workday (often requires manual steps)"
    return "generic/manual (no domain-specific automation)"


@app.post("/api/demo/run")
def demo_run(payload: DemoRunRequest):
    """
    Public demo endpoint:
    - Fetches a small sample of postings from configured GitHub repos
    - Parses them and runs HEURISTIC scoring only (offline / no Claude calls)
    - Estimates what Phase 2 auto-apply handler would be used
    """
    sample_per_source = max(int(payload.sample_per_source or 0), 1)
    body_excerpt_chars = max(int(payload.body_excerpt_chars or 0), 0)
    # Independent cap so the cover letter preview always looks substantial.
    cover_letter_excerpt_chars = max(min(body_excerpt_chars * 3, 1600), 300) if body_excerpt_chars else 900

    # Demo-only compilation simulation:
    # - If tailoring is enabled, approval would attempt to compile with pdflatex.
    # - We do NOT compile in this endpoint.
    pdflatex_available = shutil.which(config.RESUME_TEX_COMPILER) is not None

    watcher = GitHubWatcher(token=config.GITHUB_TOKEN)
    parser = JobParser()
    matcher = JobMatcher()

    samples: list[dict[str, Any]] = []

    for repo_info in REPOS:
        owner, repo = repo_info["owner"], repo_info["repo"]
        source = f"{owner}/{repo}"

        raw_jobs = watcher.fetch_new_jobs(owner, repo)
        parsed_jobs: list[dict[str, Any]] = []

        for raw in (raw_jobs or [])[:sample_per_source]:
            try:
                job = parser.parse(raw, source=source)
            except Exception:
                continue

            h_score, h_reasons = matcher._heuristic_score(job)

            cover_letter_excerpt = _fallback_cover_letter(job)[:cover_letter_excerpt_chars]

            # Resume tailoring simulation:
            # - On approval, real backend compiles LaTeX (if available) and caches a per-job PDF.
            # - In this demo endpoint we only describe the expected behavior.
            if config.TAILORED_RESUME_ENABLED:
                expected_tailored_resume_path = f"{config.TAILORED_RESUME_DIR}/{job.id}/resume.pdf"
                if pdflatex_available:
                    expected_attachment_path = expected_tailored_resume_path
                    tailoring_note = (
                        "Real flow would compile LaTeX with pdflatex and cache the result, then Phase 2 attaches/uploads the cached PDF."
                    )
                else:
                    expected_attachment_path = config.RESUME_PATH
                    tailoring_note = (
                        "Tailoring is enabled but pdflatex is not available, so real flow would fall back to the base resume PDF."
                    )
            else:
                expected_tailored_resume_path = None
                expected_attachment_path = config.RESUME_PATH
                tailoring_note = "Tailoring is disabled, so real flow attaches/uploads the base resume at `RESUME_PATH`."

            parsed_jobs.append(
                {
                    "job_id": job.id,
                    "company": job.company,
                    "title": job.title,
                    "location": job.location,
                    "apply_url": job.apply_url,
                    "date_posted": job.date_posted,
                    "match_score": round(h_score, 3),
                    "match_reasons": h_reasons,
                    "estimated_auto_apply_handler": _estimate_auto_apply_handler(job.apply_url),
                    "body_excerpt": (job.body or "")[:body_excerpt_chars] if body_excerpt_chars else "",
                    "simulated_cover_letter_excerpt": cover_letter_excerpt,
                    "resume_tailer_simulation": {
                        "tailoring_enabled": bool(config.TAILORED_RESUME_ENABLED),
                        "pdflatex_available": bool(pdflatex_available),
                        "resume_tex_path": config.RESUME_TEX_PATH,
                        "tailored_resume_cache_path": expected_tailored_resume_path,
                        "expected_attachment_path": expected_attachment_path,
                        "note": tailoring_note,
                    },
                }
            )

        samples.append({"source": source, "jobs": parsed_jobs})

    agent_settings = store.get_agent_settings()

    return {
        "ok": True,
        "note": "Demo simulation only. No applications are submitted.",
        "agent_settings": agent_settings,
        "demo": {
            "tailored_resume_enabled": bool(config.TAILORED_RESUME_ENABLED),
            "anthropic_key_configured": bool(config.ANTHROPIC_API_KEY),
            "resume_tex_compiler": config.RESUME_TEX_COMPILER,
            "resume_tailer_dir": config.TAILORED_RESUME_DIR,
            "scoring_mode": "heuristic_only",
            "safety": {
                "anthropic_calls": False,
                "no_email_sent": True,
                "no_playwright_used": True,
                "no_resume_compilation": True,
            },
            "phase2": {
                "agent_enabled": bool(agent_settings.get("agent_enabled", True)),
                "auto_apply_enabled": bool(agent_settings.get("auto_apply_enabled", False)),
                "approval_required": True,
            },
        },
        "samples": samples,
    }

