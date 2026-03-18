# Job Agent 🤖

An AI agent that watches GitHub job repos 24/7, scores postings against your
profile using Claude, and sends you digests of the best matches. Apply manually
first — enable auto-apply once you trust the match quality.

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
This is what Claude uses to score and write cover letters. The more specific, the better.

### 3. Set environment variables

```bash
cp .env.example .env
# Edit .env with your keys
export $(cat .env | xargs)
```

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
2. Put your resume PDF at the path in `RESUME_PATH`
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
├── store.py          # SQLite persistence (jobs.db)
├── notifier.py       # Slack + email digests
├── applier.py        # Phase 2: Playwright form submission
├── review.py         # CLI review tool
├── requirements.txt
└── .env.example
```

---

## Tips

- **Start with a high threshold (0.75+)** and lower it once you see what Claude catches.
- **Check `jobs.db`** with any SQLite viewer (TablePlus, DB Browser) for the full history.
- **The first run indexes existing jobs** without notifying — you'll only hear about NEW postings.
- Add more repos by appending to the `REPOS` list in `agent.py`.
