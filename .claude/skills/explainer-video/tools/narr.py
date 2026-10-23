"""Speak a project's narration: one clip per step (its sentences joined by
short pauses), cached by sentence, so re-running after an edit only speaks
what changed. Writes <project>/narr/NN-MM.wav and .mp3 and narr.json (each
step's length and when each of its captions starts).

    $EV_PY narr.py <project>          (reads <project>/steps.json, meta.json)
"""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

import imageio_ffmpeg
import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

P = pathlib.Path(sys.argv[1])
EV = pathlib.Path(os.environ.get("EV_HOME", pathlib.Path.home() / ".cache/explainer-video"))
meta = json.loads((P / "meta.json").read_text()) if (P / "meta.json").exists() else {}
VOICE, SPEED, GAP, SR = meta.get("voice", "af_heart"), meta.get("speed", 0.95), 0.3, 24000
SAY = sorted(meta.get("say", []), key=lambda x: -len(x[0]))   # [["P&L", "P and L"], …]: how to speak a spelling
OUT, CACHE = P / "narr", EV / "cache"
OUT.mkdir(exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)
FF = imageio_ffmpeg.get_ffmpeg_exe()
tts = Kokoro(str(EV / "models/kokoro-v1.0.int8.onnx"), str(EV / "models/voices-v1.0.bin"))


def sentences(s):
    return [x for x in re.split(r"(?<=[.?!])\s+(?=[A-Z0-9₹“\"(])", s) if x]


def spoken(s):
    for a, b in SAY:
        s = s.replace(a, b)
    return s


def speak(text):
    key = hashlib.sha1(f"{VOICE}|{SPEED}|{text}".encode()).hexdigest()[:16]
    f = CACHE / f"{key}.wav"
    if not f.exists():
        a, sr = tts.create(text, voice=VOICE, speed=SPEED, lang="en-us" if VOICE[0] == "a" else "en-gb")
        nz = np.nonzero(np.abs(a) > 0.01)[0]                 # trim the model's leading and trailing hush
        a = a[max(0, nz[0] - 600): nz[-1] + 1200] if len(nz) else a
        sf.write(f, a, sr)
    return sf.read(f)[0]


scenes = json.loads((P / "steps.json").read_text())
out = []
for i, sc in enumerate(scenes, 1):
    row = []
    for j, st in enumerate(sc["steps"], 1):
        caps, parts, t = [], [], 0.0
        said = sentences(st["say"]) if st.get("say") else None
        for n, s in enumerate(sentences(st["text"])):
            a = speak(spoken(said[n] if said and n < len(said) else s))
            caps.append({"t": round(t, 3), "text": s})
            parts += [a, np.zeros(int(GAP * SR))]
            t += len(a) / SR + GAP
        clip = np.concatenate(parts[:-1])
        base = OUT / f"{i:02d}-{j:02d}"
        sf.write(f"{base}.wav", clip, SR)
        subprocess.run([FF, "-loglevel", "error", "-y", "-i", f"{base}.wav", "-ac", "1", "-b:a", "40k", f"{base}.mp3"], check=True)
        row.append({"dur": round(len(clip) / SR, 3), "caps": caps, "file": base.name})
    out.append(row)
    print(f"scene {i}: {sum(s['dur'] for s in row):.1f} s of voice", flush=True)
(OUT / "narr.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
print(f"total {sum(s['dur'] for r in out for s in r) / 60:.1f} min")
