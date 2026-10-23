#!/usr/bin/env bash
# One-time setup per machine (a cloud session is a fresh machine): a Python
# venv with the voice (Kokoro, runs offline on the CPU) and a full ffmpeg,
# the voice model, and Playwright for checking and recording. Everything
# goes in ~/.cache/explainer-video (EV_HOME); nothing goes in the repo.
# Prints the variables the other tools read.
set -euo pipefail
EV_HOME="${EV_HOME:-$HOME/.cache/explainer-video}"
mkdir -p "$EV_HOME/models" "$EV_HOME/node"
if [ ! -x "$EV_HOME/venv/bin/python" ]; then
  python3 -m venv "$EV_HOME/venv"
fi
"$EV_HOME/venv/bin/python" -c "import kokoro_onnx, soundfile, imageio_ffmpeg, numpy" 2>/dev/null ||
  "$EV_HOME/venv/bin/pip" install -q kokoro-onnx soundfile imageio-ffmpeg numpy
REL=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
for f in kokoro-v1.0.int8.onnx voices-v1.0.bin; do
  [ -s "$EV_HOME/models/$f" ] || curl -fsSL -o "$EV_HOME/models/$f" "$REL/$f"
done
if [ ! -f "$EV_HOME/node/node_modules/playwright/index.mjs" ]; then
  (cd "$EV_HOME/node" && [ -f package.json ] || echo '{"private":true}' > package.json
   cd "$EV_HOME/node" && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --silent --no-audit --no-fund playwright@1.56.1)
fi
FF="$("$EV_HOME/venv/bin/python" -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
echo "EV_HOME=$EV_HOME"
echo "EV_PY=$EV_HOME/venv/bin/python"
echo "FF=$FF"
echo "ready"
