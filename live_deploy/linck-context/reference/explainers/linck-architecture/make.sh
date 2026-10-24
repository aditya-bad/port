#!/usr/bin/env bash
# Rebuild the Linck architecture explainer with the explainer-video skill
# (.claude/skills/explainer-video). Everything it makes stays out of git.
#   ./make.sh --no-voice      captions only, quick, for a look
#   ./make.sh                 with the voice (about 30 minutes the first time)
#   then: node $S/tools/check.mjs out/linck-architecture.html
#         node $S/tools/render.mjs out/linck-architecture.html && $EV_PY $S/tools/mux.py .   (MP4s)
set -euo pipefail
cd "$(dirname "$0")"
S="$(git rev-parse --show-toplevel)/.claude/skills/explainer-video"
cat parts/*.js > scenes.js
node "$S/tools/steps.mjs" scenes.js > steps.json
if [[ " $* " != *" --no-voice "* ]]; then
  eval "$(bash "$S/tools/setup.sh" | grep '=')"; export EV_HOME FF
  "$EV_PY" "$S/tools/narr.py" .
fi
python3 "$S/tools/build.py" . "$@"
