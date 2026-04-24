# Applyd

Applyd polls public GitHub job boards, scores listings against a configurable profile (heuristics plus an optional LLM), persists results, and can notify you by email or Slack. Optional Phase 2 uses Playwright to submit applications after you approve matches.

## Default sources

Repositories are listed in `agent.py` (`REPOS`) By default:

- [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions)
- [pittcsc/Summer2025-Internships](https://github.com/pittcsc/Summer2025-Internships)
- [speedyapply/2026-SWE-College-Jobs](https://github.com/speedyapply/2026-SWE-College-Jobs)

## Installation

```bash
pip install -r requirements.txt
```

For Phase 2 (browser apply): `playwright install chromium`.

## Configuration

**Profile and defaults** — Edit `config.py` (name, bio, skills, target roles, locations, match threshold, and related defaults).

**Secrets and overrides** — Copy the template and load variables into your shell (or use a process manager / platform env UI):

```bash
cp env.example .env
# edit .env, then:
set -a && source .env && set +a   # POSIX shells
```

| Variable | Purpose |
|----------|---------|
| `GITHUB_TOKEN` | GitHub API token (public repo access; improves rate limits) |
| `NOTIFY_EMAIL`, `GMAIL_APP_PASSWORD` | Gmail SMTP for outbound mail (app password, not account password) |
| `LLM_PROVIDER` | `none` (default), `ollama`, or `anthropic` — see `env.example` |
| `DB_BACKEND` | `sqlite` (default `jobs.db`) or `mysql` for hosted deploys |

**MySQL (e.g. Railway):** Set `DB_BACKEND=mysql` and the `MYSQL_*` variables. Migrate existing SQLite data once:

```bash
python scripts/migrate_sqlite_to_mysql.py --sqlite-path jobs.db
```

## Web dashboard and accounts

The FastAPI app (`web_backend`) and Next.js UI provide authentication, job review, and a background scan worker when deployed with Docker.

- **Bootstrap admin:** If `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` are set and the `users` table is empty, the first row is created on startup and marked administrator.
- **Self-service sign-up:** `ALLOW_OPEN_REGISTRATION=true` enables `/register` without an invite. Otherwise set `REGISTRATION_INVITE_CODE` and require it at registration.
- **Per-user notification email:** Stored per account; used as the **To** address for digests and approval mail when that user runs a manual scan. The scheduled worker sends to all configured user addresses plus `NOTIFY_EMAIL` (deduplicated).
- **Account deletion:** Non-admin users may delete their own account from the dashboard (password required). The admin user (matches `DASHBOARD_USERNAME`, or lowest user id if unset) cannot be removed this way.

## Optional: inbox approval links

Set `EMAIL_APPROVAL_REQUESTS_ENABLED=true`, `PUBLIC_BASE_URL` (the public URL of the **same** deployment that owns the database), and `MAIL_SIGNING_SECRET`. Each new match can trigger a separate email with signed Approve/Reject links and attachments (resume PDF and cover letter when available).

`PUBLIC_BASE_URL` must point at the environment that wrote the job and token (e.g. do not point production URLs at jobs created only in local SQLite).

## Running

**CLI agent (polls on an interval):**

```bash
python agent.py
```

**Review CLI:**

```bash
python review.py              # interactive queue
python review.py list
python review.py stats
python review.py cover <job_id>
```

**Docker / combined UI + API:** See `Dockerfile` — FastAPI on port 8000 and Next.js on 3000, with `/api` proxied to the backend.

## Scoring pipeline

1. **Heuristics** — Role, location, skills, and optional salary checks produce a 0–1 score and reasons.
2. **Optional LLM** — If `LLM_PROVIDER` enables a model and the heuristic score is above an internal floor, an LLM score is blended in (see `matcher.py`). With `LLM_PROVIDER=none`, only heuristics run.
3. **Threshold** — Jobs at or above `MATCH_THRESHOLD` (default `0.65`) are queued for notification.

## Phase 2: Auto-apply

1. Set `AUTO_APPLY_ENABLED=true` after you trust match quality.
2. Set `RESUME_PATH` to a base PDF; optional LaTeX tailoring uses `TAILORED_RESUME_ENABLED`, `RESUME_TEX_PATH`, and a local `pdflatex` install.
3. Approve jobs in the dashboard or CLI; the worker attempts apply via Playwright (and mailto where applicable). Complex ATS flows may still require manual steps.

## Repository layout

```
agent.py            # CLI scan loop
applier.py          # Phase 2 apply
config.py           # Profile + env-backed settings
matcher.py          # Scoring and cover-letter hooks
notifier.py         # Email / Slack
store.py            # SQLite or MySQL persistence
web_backend/        # FastAPI API and worker
frontend/           # Next.js dashboard
resume/             # LaTeX template (when tailoring is enabled)
scripts/            # e.g. SQLite → MySQL migration
```

## Operations notes

- The first index pass per repository ingests existing rows **without** sending notifications; later runs only notify on **new** postings.
- Raising `MATCH_THRESHOLD` reduces noise; lower it once you validate quality.
- Add or change sources by editing `REPOS` in `agent.py`
