# Explainer Studio: one image with everything a render needs: the web app,
# the voice (Kokoro, offline), a browser to record with (Chromium via
# Playwright) and ffmpeg. Projects live in /data (mount a volume there).
FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    EV_HOME=/opt/ev \
    STUDIO_PY=/opt/ev/venv/bin/python \
    STUDIO_KIT=/app/kit \
    STUDIO_DATA=/data \
    STUDIO_PORT=47391 \
    STUDIO_WORKERS=2 \
    PW=/opt/ev/node/node_modules/playwright/index.mjs \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw \
    FF=/usr/bin/ffmpeg \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Python: the app and the voice, one environment
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/ev/venv \
 && /opt/ev/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

# the voice model (Apache-2.0), from the kokoro-onnx releases
ARG KOKORO=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
RUN mkdir -p /opt/ev/models \
 && curl -fsSL -o /opt/ev/models/kokoro-v1.0.int8.onnx "$KOKORO/kokoro-v1.0.int8.onnx" \
 && curl -fsSL -o /opt/ev/models/voices-v1.0.bin "$KOKORO/voices-v1.0.bin"

# the recorder: Playwright and its Chromium, with the system libraries it needs;
# pptxgenjs for the PowerPoint
RUN mkdir -p /opt/ev/node && cd /opt/ev/node \
 && echo '{"private":true,"type":"module"}' > package.json \
 && npm install --no-audit --no-fund --silent playwright@1.56.1 pptxgenjs@3.12.0 \
 && npx playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/* /root/.npm

WORKDIR /app
COPY app ./app
COPY kit ./kit
COPY ui ./ui

VOLUME /data
EXPOSE 47391
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fs "http://127.0.0.1:${STUDIO_PORT}/api/health" || exit 1
CMD ["sh", "-c", "exec /opt/ev/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${STUDIO_PORT}"]
