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
# back on the next run (via env vars, a mounted file, or — for an AWS
# deployment — AWS Systems Manager Parameter Store; see RUN_GUIDE.md's
# "AWS deployment: SSM Parameter Store" section), and Postgres already
# has everything else.
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

WORKDIR /app

# Dependencies first, separately from app code, so `docker build` only
# re-installs them when requirements.txt actually changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/
COPY run.py ./run.py
COPY config.example.json ./config.example.json

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
