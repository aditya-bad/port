#!/usr/bin/env bash
# The skill testing itself: builds the example (with its voice) and checks
# it passes; checks the still fixture fails without the camera's drift
# (so check.mjs really catches a pause) and passes with it; renders one
# scene of the example to video with its voice. Run after changing the
# engine or the tools.
set -euo pipefail
S="$(cd "$(dirname "$0")/.." && pwd)"
eval "$(bash "$S/tools/setup.sh" | grep '=')"
export EV_HOME FF
W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT
cp -r "$S/example" "$W/example"; cp -r "$S/tools/fixture-still" "$W/still"
cd "$W/example"
node "$S/tools/steps.mjs" scenes.js > steps.json
"$EV_PY" "$S/tools/narr.py" . 2>/dev/null | tail -1
python3 "$S/tools/build.py" .
node "$S/tools/check.mjs" out/example.html --fast | tail -3
cd "$W/still"
node "$S/tools/steps.mjs" scenes.js > steps.json
python3 "$S/tools/build.py" . --no-voice >/dev/null
if node "$S/tools/check.mjs" out/still.html --fast --nodrift > check.log; then
  echo "FAIL: check.mjs passed a scene that stops moving"; exit 1
fi
need() { if ! eval "$1"; then echo "FAIL: $2"; exit 1; fi; echo "ok: $2"; }
need 'grep -q "doesn.t move" check.log' "a scene that stops is caught"
need 'grep -q "hold" check.log' "the long hold is reported"
need 'node "$S/tools/check.mjs" out/still.html --fast > /dev/null' "with the camera's drift it never stops"
cd "$W/example"
node "$S/tools/render.mjs" out/example.html 1 >/dev/null
"$EV_PY" "$S/tools/mux.py" . >/dev/null
info="$("$FF" -hide_banner -i out/example.mp4 2>&1 || true)"     # ffmpeg with no output always exits 1
need '[[ "$info" == *"Video: h264"* && "$info" == *"Audio: aac"* ]]' "a video with its voice, $(du -h out/example.mp4 | cut -f1)"
echo "selftest passed"
