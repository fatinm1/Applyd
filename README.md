# Applyd 

An agent that watches GitHub job repos 24/7, scores postings against your
profile (heuristics by default; optional local/open-source LLM), and sends you
digests of the best matches. Apply manually first — enable auto-apply once you
trust the match quality.

## Repos watched
- [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions)
- [pittcsc/Summer2025-Internships](https://github.com/pittcsc/Summer2025-Internships)

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium   # only needed for Phase 2 auto-apply
```

### 2. Configure your profile

Edit `config.py` — fill in your **name, bio, skills, target roles, and locations**.
This is what the scoring logic (and optional LLM) uses to score and write cover letters.
The more specific, the better.

### 2.5 (Optional) Free local AI via Ollama (recommended for you)

If you want AI scoring/cover letters/resume tailoring **for free** while running locally 24/7:

1. Install Ollama and pull a model (example):

```bash
ollama pull qwen2.5:14b
```

2. In `.env`, set:

```bash
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:14b
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

If `LLM_PROVIDER=none` (default), Applyd runs **heuristics-only** (no LLM calls), so other people can use it without paying for inference.

### 3. Set environment variables

```bash
cp .env.example .env
# Edit .env with your keys
export $(cat .env | xargs)
```

### 4. Database (SQLite vs MySQL)
By default, Applyd uses local SQLite (`jobs.db`).

If you deploy on Railway (or anywhere with ephemeral disks), switch to MySQL:
1. In `.env`, set `DB_BACKEND=mysql` and fill in:
   - `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`
2. Run a one-time migration from your local SQLite history:
```bash
python scripts/migrate_sqlite_to_mysql.py --sqlite-path jobs.db
```
3. Re-deploy the backend with the same MySQL env vars.

**GitHub token**: Create at https://github.com/settings/tokens
No special scopes needed for public repos — the token just raises rate limits.

**Gmail app password**: Go to Google Account → Security → 2-Step Verification → App passwords.
Generate one for "Mail". This is NOT your login password.

**Slack webhook**: https://api.slack.com/messaging/webhooks

---

## Running

### Start the agent (runs forever, polls every 15 min)

```bash
python agent.py
```

Run it in the background with `nohup` or a process manager:

```bash
# tmux
tmux new -s job-agent
python agent.py

# Or as a background process
nohup python agent.py > agent.log 2>&1 &
```

### Review pending jobs

```bash
python review.py              # Interactive review queue
python review.py list         # See all jobs
python review.py stats        # Application stats
python review.py cover <id>   # Generate cover letter for a job ID
```

---

## How matching works

1. **Heuristic pre-filter** (fast, free): checks role keywords, location, skills overlap.
   Jobs scoring below 25% skip the AI step entirely.

2. **Claude scoring** (70% weight): Claude reads the full job description against your
   bio and skills, returns a 0–1 score and reasons.

3. **Threshold gate**: Only jobs above `MATCH_THRESHOLD` (default 65%) are notified.

---

## Phase 2: Auto-apply

Once you've reviewed ~20 cycles and the matches look right:

1. Set `AUTO_APPLY_ENABLED=true` in `.env`
2. Put your resume PDF at the path in `RESUME_PATH` (fallback)
3. (New) Enable resume tailoring in `.env` and provide LaTeX:
   - `TAILORED_RESUME_ENABLED=true`
   - `RESUME_TEX_PATH=resume/resume_template.tex`
   - This generates a job-specific PDF on `approve` and Phase 2 attaches/uploads it.
   - Requires a working LaTeX toolchain (typically `pdflatex`) on the machine.
3. Approve jobs in `python review.py` — they'll be applied on the next cycle

Supported platforms: **Lever**, **Greenhouse**, **email/mailto links**.
Workday and other complex platforms fall back to a logged "needs manual" status.

---

## File structure

```
job_agent/
├── agent.py          # Main orchestrator (run this)
├── config.py         # All settings — edit your profile here
├── watcher.py        # GitHub README/Issues polling
├── parser.py         # Job data model + normalisation
├── matcher.py        # Heuristic + Claude scoring, cover letter gen
├── store.py          # Persistence (SQLite or MySQL)
├── notifier.py       # Slack + email digests
├── applier.py        # Phase 2: Playwright form submission
├── review.py         # CLI review tool
├── requirements.txt
└── .env.example

├── web_backend/       # FastAPI dashboard API + background worker
└── resume_tailer.py   # Tailors resume and compiles job-specific PDFs
```

---

## Tips

- **Start with a high threshold (0.75+)** and lower it once you see what Claude catches.
- **Check your DB** (SQLite `jobs.db` or MySQL tables) for the full history.
- **The first run indexes existing jobs** without notifying — you'll only hear about NEW postings.
- Add more repos by appending to the `REPOS` list in `agent.py`.
