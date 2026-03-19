FROM python:3.11-slim

# Install Node.js (for Next.js build/start).
ENV NODE_VERSION=20
RUN apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates gnupg build-essential \
  && curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Backend deps (Python)
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

# Frontend deps + build
COPY frontend/package.json frontend/package-lock.json /app/frontend/
WORKDIR /app/frontend
RUN npm ci
COPY frontend /app/frontend
RUN npm run build

# Copy the backend code after frontend build (keeps Docker layers smaller).
WORKDIR /app
COPY web_backend /app/web_backend
COPY agent.py applier.py config.py matcher.py notifier.py parser.py resume_tailer.py review.py store.py watcher.py /app/
COPY resume /app/resume
COPY scripts /app/scripts
COPY README.md /app/README.md

# Resume tailoring writes job-specific PDFs into `tailored_resumes/<job_id>/...`.
# We intentionally do NOT bake local `tailored_resumes/` into the image (it can be huge
# and is excluded by `.dockerignore`). Create the directory for runtime writes.
RUN mkdir -p /app/tailored_resumes

# Optional: install Playwright browsers so Phase 2 can work out of the box.
# If this is too heavy for your use-case, you can remove this line later.
RUN python3 -m playwright install --with-deps chromium || true

EXPOSE 3000

# Run FastAPI in background, keep Next.js in foreground.
# Explicitly bind Next.js to 0.0.0.0 so Railway can reach the public port.
CMD ["sh", "-c", "python3 -m uvicorn web_backend.main:app --host 0.0.0.0 --port 8000 --log-level info & cd /app/frontend && npm run start -- -p 3000 -H 0.0.0.0"]

