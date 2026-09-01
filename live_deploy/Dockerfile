# live_deploy — paper-trading service.
#
# Stateless by design: the ONLY files this app reads are config.json and
# tokens.json, both read ONCE at startup (config.json at import time,
# tokens.json inside the FastAPI startup event) — never written to, and
# nothing else under the image's filesystem is read or written at
# runtime (no local log files, no cache directories, no SQLite). Every
# genuinely durable thing — deployments, positions, fills, the daily
# Kite session — lives in Postgres. A container built from this image
# can be killed and replaced at any time without losing anything that
# matters; config.json/tokens.json are the only inputs it needs handed
# back on the next run (via env vars or a mounted file — see below),
# and Postgres already has everything else.
#
# Build:
#   docker build -t live-deploy .
#
# Run (config.json mounted read-only — see RUN_GUIDE.md for the
# env-var-only alternative that needs no bind mount at all):
#   docker run -d --name live-deploy -p 8000:8000 \
#     -v $(pwd)/config.json:/app/config.json:ro \
#     -v $(pwd)/tokens.json:/app/tokens.json:ro \
#     live-deploy

FROM python:3.11-slim

# Every time-of-day config value in this app (entry_time, force_exit_time,
# market_open_time, ...) means "NSE time", i.e. IST — and Kite's own tick
# timestamps are naive datetimes built via Python's datetime.fromtimestamp()
# with no tz argument, which means naive LOCAL SYSTEM TIME of whatever
# machine runs this (see app/deployments/runner.py's own comments on this).
# python:3.11-slim (Debian) has NO tzdata installed and defaults to UTC —
# without this, entry_time="10:00" doesn't mean 10am IST at all, it means
# 10am UTC = 3:30pm IST: every trade fires 5.5 hours later, in real
# clock-on-the-wall terms, than the config says. This was found and fixed
# only after it silently mistimed real trades — set explicitly, not left
# to the base image's default.
ENV TZ=Asia/Kolkata
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, separately from app code, so `docker build` only
# re-installs them when requirements.txt actually changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/
COPY run.py ./run.py
COPY config.example.json ./config.example.json
# custom_scripts/ — one-off admin scripts (see its own README.md).
# Needed IN the image (not just the git checkout) so any of them that
# depend on this app's own code/dependencies and Docker-network-only
# DB access can be run via `docker exec live-deploy python3
# custom_scripts/<script>.py` directly, without a rebuild just to add
# one file — see fix_strangle_instrument_tokens.py for a concrete
# example of exactly why this is needed (Kite REST access + the local
# Postgres container, both only reachable from inside a running
# container, not the bare host).
COPY custom_scripts/ ./custom_scripts/

# config.json / tokens.json are DELIBERATELY not copied in — see the
# header above. Provide them at `docker run` time via -v bind mounts
# (read-only, as in the example above) or by generating config.json
# from environment variables before launch (see RUN_GUIDE.md).

EXPOSE 8000

# run.py == `python -m app.main` == uvicorn on 0.0.0.0:8000 with no
# --reload — see run.py's own docstring for exactly why this exists
# instead of `python app/main.py` (which fails with a relative-import
# ImportError when executed as a standalone script).
CMD ["python", "run.py"]
