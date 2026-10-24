#!/usr/bin/env bash
# Build this explainer: joins parts/ into scenes.js, reads out the steps,
# voices them (unless --no-voice) and builds out/<slug>.html.
#   ./make.sh --no-voice      captions only, quick, for a look
#   ./make.sh                 with the voice
# S is the skill's folder; set it if you copied this folder elsewhere.
set -euo pipefail
cd "$(dirname "$0")"
S="${S:-$(cd .. && pwd)}"
cat parts/*.js > scenes.js
node "$S/tools/steps.mjs" scenes.js > steps.json
if [[ " $* " != *" --no-voice "* ]]; then
  eval "$(bash "$S/tools/setup.sh" | grep '=')"; export EV_HOME FF
  "$EV_PY" "$S/tools/narr.py" .
fi
python3 "$S/tools/build.py" . "$@"
