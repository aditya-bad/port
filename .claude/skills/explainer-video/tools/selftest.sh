#!/usr/bin/env bash
# The skill testing itself: builds the example (with its voice) and checks
# it passes, which includes the camera holding still once it has framed
# its subject (a creeping camera makes everything look like it's shaking);
# checks the still fixture fails (so check.mjs really catches a pause);
# renders one scene of the example to video with its voice. Run after
# changing the engine or the tools. Also checks the diagram helpers:
# beats() and flowScene() steps are extracted, and a service, a flow and
# close rows spotlit with a small pad check clean.
set -euo pipefail
S="$(cd "$(dirname "$0")/.." && pwd)"
eval "$(bash "$S/tools/setup.sh" | grep '=')"
export EV_HOME FF
W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT
cp -r "$S/example" "$W/example"; cp -r "$S/tools/fixture-still" "$W/still"; cp -r "$S/tools/fixture-spot" "$W/spot"; cp -r "$S/tools/fixture-diagram" "$W/diagram"
cd "$W/example"
node "$S/tools/steps.mjs" scenes.js > steps.json
"$EV_PY" "$S/tools/narr.py" . 2>/dev/null | tail -1
python3 "$S/tools/build.py" .
node "$S/tools/check.mjs" out/example.html > check.log || { tail -20 check.log; echo "FAIL: the example has failures"; exit 1; }
tail -3 check.log
cd "$W/still"
node "$S/tools/steps.mjs" scenes.js > steps.json
python3 "$S/tools/build.py" . --no-voice >/dev/null
if node "$S/tools/check.mjs" out/still.html --fast > check.log; then
  echo "FAIL: check.mjs passed a scene that stops moving"; exit 1
fi
need() { if ! eval "$1"; then echo "FAIL: $2"; exit 1; fi; echo "ok: $2"; }
need 'grep -q "nothing moves from" check.log' "a scene that stops is caught"
need 'grep -q "hold" check.log' "the long hold is reported"
need 'grep -q "^0 warning(s)" "$W/example/check.log"' "the example has no warnings"
cd "$W/spot"
node "$S/tools/steps.mjs" scenes.js > steps.json
python3 "$S/tools/build.py" . --no-voice >/dev/null
node "$S/tools/check.mjs" out/spot.html --fast > check.log || true
need 'grep -q "scene 1 .*spotlight.s edge cuts through the \"Server\" box" check.log' "a spotlight typed by hand that cuts a box is caught"
need '! grep -q "scene 2 .*spotlight.s edge" check.log' "a spotlight by name fits what it names"
need 'grep -q "nothing is named \"nope\"" check.log' "a name nothing has fails, saying which"
need 'grep -q "scene 4 .*\"message\" crosses the spotlight.s edge" check.log' "a message travelling out of the spotlight is caught"
cd "$W/diagram"
node "$S/tools/steps.mjs" scenes.js > steps.json
need '[[ "$(python3 -c "import json; print([len(s[\"steps\"]) for s in json.load(open(\"steps.json\"))])")" == "[2, 2, 3]" ]]' "beats() and flowScene() steps are read without drawing"
python3 "$S/tools/build.py" . --no-voice >/dev/null
node "$S/tools/check.mjs" out/diagram.html --fast > check.log || { tail -20 check.log; echo "FAIL: the diagram fixture has failures"; exit 1; }
need 'grep -q "^0 warning(s)" check.log' "services, flows, a 1 s loop and close rows spotlit with pad: no warnings"
cd "$W/example"
node "$S/tools/render.mjs" out/example.html 1 >/dev/null
"$EV_PY" "$S/tools/mux.py" . >/dev/null
info="$("$FF" -hide_banner -i out/example.mp4 2>&1 || true)"     # ffmpeg with no output always exits 1
need '[[ "$info" == *"Video: h264"* && "$info" == *"Audio: aac"* ]]' "a video with its voice, $(du -h out/example.mp4 | cut -f1)"
echo "selftest passed"
