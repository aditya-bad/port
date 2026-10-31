"""
The narration, spoken offline: one clip per step, its sentences joined
by short pauses, and when each caption starts. Run in a process of its
own (app/video/jobs.py), so the voice model's memory (about 360 MB at
its peak) is handed back the moment it's done:

    python -m app.video.voice <job dir>      reads story.json and voice.txt
                                             writes narr/NN-MM.wav, narr.json

The voice is Kokoro (an open model, run on the CPU with onnxruntime, two
threads); its files sit in LINCK_VOICE_MODELS (the image puts them in
/opt/kokoro). Sentences go to it one at a time: memory grows with a
sentence's length, so a long one is spoken in parts (parts()).

LINCK_VIDEO_FAKE_VOICE=1 speaks nothing: a quiet tone as long as the
words would take to say, for tests on machines without the model.
Progress lines on stdout: 'p 0.42'.
"""

from __future__ import annotations

import array
import json
import math
import os
import re
import sys
import wave
from pathlib import Path

SR = 24000
GAP = 0.32          # between sentences
PART_GAP = 0.14     # where a long sentence was split at a comma
MAX_WORDS = 16      # longer sentences go to the voice in parts: its memory grows with length
SPEED = 1.08
WPS = 2.6           # the fake voice's words a second
VOICES = {"af_heart": "Heart (US, woman)", "am_michael": "Michael (US, man)",
          "bf_emma": "Emma (UK, woman)", "bm_george": "George (UK, man)"}
DEFAULT_VOICE = "af_heart"
MODEL_DIRS = [os.environ.get("LINCK_VOICE_MODELS") or "", "/opt/kokoro", str(Path.home() / ".cache/explainer-video/models")]


def sentences(s: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.?!])\s+(?=[A-Z0-9₹“\"(])", s or "") if x.strip()]


def model_dir() -> Path | None:
    for d in MODEL_DIRS:
        if d and (Path(d) / "kokoro-v1.0.int8.onnx").exists() and (Path(d) / "voices-v1.0.bin").exists():
            return Path(d)
    return None


def available() -> bool:
    """Whether this machine can speak: the model's files and its package."""
    if os.environ.get("LINCK_VIDEO_FAKE_VOICE"):
        return True
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    return model_dir() is not None


class Speaker:
    def __init__(self, voice: str):
        self.voice = voice if voice in VOICES else DEFAULT_VOICE
        self.fake = bool(os.environ.get("LINCK_VIDEO_FAKE_VOICE"))
        if not self.fake:
            import onnxruntime as ort
            from kokoro_onnx import Kokoro
            d = model_dir()
            if d is None:
                raise SystemExit("The voice model isn't installed (kokoro-v1.0.int8.onnx, voices-v1.0.bin); "
                                 "rebuild the image, or set LINCK_VOICE_MODELS.")
            so = ort.SessionOptions()
            so.intra_op_num_threads, so.inter_op_num_threads = 2, 1
            sess = ort.InferenceSession(str(d / "kokoro-v1.0.int8.onnx"), so, providers=["CPUExecutionProvider"])
            self.tts = Kokoro.from_session(sess, str(d / "voices-v1.0.bin"))

    def say(self, text: str) -> array.array:
        """16-bit samples at 24 kHz, the model's hush trimmed off both ends."""
        if self.fake:
            n = int(max(0.4, len(text.split()) / WPS) * SR)
            return array.array("h", (int(600 * math.sin(i * 2 * math.pi * 220 / SR)) for i in range(n)))
        a, sr = self.tts.create(text, voice=self.voice, speed=SPEED, lang="en-us" if self.voice[0] == "a" else "en-gb")
        import numpy as np
        nz = np.nonzero(np.abs(a) > 0.01)[0]
        if len(nz):
            a = a[max(0, nz[0] - 600): nz[-1] + 1200]
        return array.array("h", (np.clip(a, -1, 1) * 32000).astype("int16").tobytes())


def parts(sentence: str) -> list[str]:
    """A long sentence in pieces of at most about MAX_WORDS words, cut
    after a comma, colon or semicolon (where a speaker pauses anyway)."""
    words = sentence.split()
    if len(words) <= MAX_WORDS:
        return [sentence]
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= 6 and w[-1] in ",;:" and len(cur) >= MAX_WORDS // 2:
            out.append(" ".join(cur))
            cur = []
    if cur:
        if out and len(cur) < 4:
            out[-1] += " " + " ".join(cur)
        else:
            out.append(" ".join(cur))
    return out


def write_wav(path: Path, samples: array.array) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.tobytes())


def read_wav(path: Path) -> array.array:
    with wave.open(str(path), "rb") as w:
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
        return a


def speak(job: Path) -> list:
    story = json.loads((job / "story.json").read_text())
    voice = (job / "voice.txt").read_text().strip() if (job / "voice.txt").exists() else DEFAULT_VOICE
    out_dir = job / "narr"
    out_dir.mkdir(exist_ok=True)
    sp = Speaker(voice)
    total = sum(len(sc["steps"]) for sc in story["scenes"]) or 1
    done, out = 0, []
    gap = array.array("h", bytes(2 * int(GAP * SR)))
    for i, sc in enumerate(story["scenes"], 1):
        row = []
        for j, st in enumerate(sc["steps"], 1):
            caps, clip, t = [], array.array("h"), 0.0
            shown, said = sentences(st["text"]), sentences(st.get("say") or st["text"])
            if len(said) != len(shown):          # pair them only when they line up; else speak it whole
                said, shown = [" ".join(said)], [" ".join(shown)]
            pgap = array.array("h", bytes(2 * int(PART_GAP * SR)))
            for n, (s_show, s_say) in enumerate(zip(shown, said)):
                a = array.array("h")
                for q, piece in enumerate(parts(s_say)):
                    if q:
                        a.extend(pgap)
                    a.extend(sp.say(piece))
                if n:
                    clip.extend(gap)
                    t += GAP
                caps.append({"t": round(t, 3), "text": s_show})
                clip.extend(a)
                t += len(a) / SR
            name = f"{i:02d}-{j:02d}"
            write_wav(out_dir / f"{name}.wav", clip)
            row.append({"dur": round(len(clip) / SR, 3), "caps": caps, "file": name})
            done += 1
            print(f"p {done / total:.3f}", flush=True)
        out.append(row)
    (job / "narr.json").write_text(json.dumps(out, ensure_ascii=False))
    return out


if __name__ == "__main__":
    speak(Path(sys.argv[1]))
