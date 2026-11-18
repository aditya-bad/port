#!/usr/bin/env bash
# Run Explainer Studio without Docker: one Python environment for the app and
# the voice, the voice model, and Playwright's Chromium, all under
# ~/.cache/explainer-video (EV_HOME). Needs Python 3.12+ (set PYTHON=python3.13 to pick one), node (18+) and ffmpeg.
#   bash scripts/setup-local.sh && bash scripts/run-local.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
EV_HOME="${EV_HOME:-$HOME/.cache/explainer-video}"
mkdir -p "$EV_HOME/models" "$EV_HOME/node"
command -v ffmpeg >/dev/null || { echo "ffmpeg is needed (apt install ffmpeg / brew install ffmpeg)"; exit 1; }
command -v node >/dev/null || { echo "Node.js 18+ is needed"; exit 1; }
PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
  || { echo "Python 3.12 or later is needed (numpy 2.5); $PYTHON is $("$PYTHON" -V 2>&1). Run with PYTHON=python3.13 (or 3.12)."; exit 1; }
[ -x "$EV_HOME/venv/bin/python" ] || "$PYTHON" -m venv "$EV_HOME/venv"
"$EV_HOME/venv/bin/pip" install -q -r "$HERE/requirements.txt" -r "$HERE/requirements-dev.txt"
REL=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
for f in kokoro-v1.0.int8.onnx voices-v1.0.bin; do
  [ -s "$EV_HOME/models/$f" ] || curl -fsSL -o "$EV_HOME/models/$f" "$REL/$f"
done
cd "$EV_HOME/node"
[ -f package.json ] || echo '{"private":true,"type":"module"}' > package.json
[ -f node_modules/pptxgenjs/package.json ] || npm install --silent --no-audit --no-fund playwright@1.56.1 pptxgenjs@3.12.0
npx playwright install chromium
echo "ready: bash scripts/run-local.sh"
