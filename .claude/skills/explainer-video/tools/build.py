"""Build a project into one self-contained page: fonts, colours, the
engine, the effects, the scenes, and the narration clips embedded.

    python3 build.py <project> [--no-voice]   → <project>/out/<slug>.html

Without narr/narr.json (or with --no-voice) the page times each step from
its word count and has captions but no sound: for a quick look first.
"""
import base64
import json
import pathlib
import sys
from html import escape

SKILL = pathlib.Path(__file__).resolve().parent.parent
P = pathlib.Path(sys.argv[1]).resolve()
meta = json.loads((P / "meta.json").read_text())
slug = meta.get("slug") or P.name
voice = (P / "narr" / "narr.json").exists() and "--no-voice" not in sys.argv

css = "\n".join((SKILL / "engine" / f).read_text() for f in ("fonts.css", "tokens.css", "base.css"))
markers = "".join(
    f'<marker id="ah-{c}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" '
    f'orient="auto-start-reverse" markerUnits="strokeWidth"><path class="ah-{c}" d="M0 0L10 5L0 10z"/></marker>'
    for c in ("ink", "b", "g", "pu", "wt", "r"))
narr = []
if voice:
    for row in json.loads((P / "narr" / "narr.json").read_text()):
        narr.append([{**st, "src": "data:audio/mpeg;base64," + base64.b64encode((P / "narr" / f"{st['file']}.mp3").read_bytes()).decode()}
                     for st in row])
js = (f"const NARR = {json.dumps(narr, ensure_ascii=False) if voice else 'null'};\n"
      + "\n".join((SKILL / "engine" / f).read_text() for f in ("engine.js", "life.js", "diagram.js", "beats.js")) + "\n"
      + (P / "scenes.js").read_text())
title, desc = meta["title"], meta.get("description", "")
page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="{escape(desc)}">
<style>
{css}
</style>
</head>
<body>
<svg id="stage" viewBox="0 0 1600 900" role="img" aria-label="{escape(title)}: an animated explainer">
<defs>{markers}</defs>
<g id="cam"><g id="layer"></g></g>
<g id="hud"></g>
</svg>
<div class="turn" aria-hidden="true"><svg viewBox="0 0 30 30"><rect x="9" y="3" width="12" height="24" rx="2" fill="none" stroke="currentColor" stroke-width="2"/></svg>turn sideways</div>
<div class="band"><p id="cap" aria-live="polite"></p>
<nav class="ctl" aria-label="Scenes"><button id="prev" aria-label="Previous scene">◀</button><button id="pp" aria-label="Pause or play">❚❚</button><button id="next" aria-label="Next scene">▶</button><button id="snd" aria-label="Turn narration on">🔇</button><div class="dots"></div></nav></div>
<div id="start" hidden><div class="card"><h1>{escape(title)}</h1><p>{escape(desc)} About <span id="mins">1</span> min{'' if voice else ', captions only for now'}. ← and → move between scenes; space pauses.</p>
<div class="row"><button id="go">▶ Play{' with sound' if voice else ''}</button><button id="go-muted">Play with captions only</button></div></div></div>
<script>
{js}
</script>
</body>
</html>
"""
(P / "out").mkdir(exist_ok=True)
f = P / "out" / f"{slug}.html"
f.write_text(page)
print(f"{f}  {len(page) / 1e6:.1f} MB  {'with voice' if voice else 'captions only'}")
