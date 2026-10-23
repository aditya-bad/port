"""Put each scene's voice under its picture, and join the scenes.

    $EV_PY mux.py <project>   → out/vid/NN-<title>.mp4 and out/<slug>.mp4
"""
import json
import pathlib
import re
import subprocess
import sys

import imageio_ffmpeg
import numpy as np
import soundfile as sf

P = pathlib.Path(sys.argv[1]).resolve()
meta = json.loads((P / "meta.json").read_text())
slug = meta.get("slug") or P.name
V, FF, SR = P / "out" / "vid", imageio_ffmpeg.get_ffmpeg_exe(), 24000
outs = []
for plan_f in sorted(V.glob("[0-9][0-9].json")):
    nn, plan = plan_f.stem, json.loads(plan_f.read_text())
    track = np.zeros(int(plan["total"] * SR) + SR)
    for st in plan["steps"]:
        if not st.get("file"):
            continue
        a = sf.read(P / "narr" / f"{st['file']}.wav")[0]
        i = int(round(st["T0"] * SR))
        track[i:i + len(a)] += a
    sf.write(V / f"{nn}.wav", track, SR)
    name = re.sub(r"[^a-z0-9]+", "-", plan.get("title", nn).lower()).strip("-")[:48]
    out = V / f"{nn}-{name}.mp4"
    subprocess.run([FF, "-loglevel", "error", "-y", "-i", V / f"{nn}-v.mp4", "-i", V / f"{nn}.wav", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-shortest", "-movflags", "+faststart", out], check=True)
    outs.append(out)
(V / "list.txt").write_text("".join(f"file '{o.name}'\n" for o in outs))
full = P / "out" / f"{slug}.mp4"
subprocess.run([FF, "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", V / "list.txt", "-c", "copy",
                "-movflags", "+faststart", full], check=True)
print("\n".join(str(o) for o in outs + [full]))
