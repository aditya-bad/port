"""
The stage (static/linck/video/stage.html) recorded into an MP4: each
frame seeked on the page's own clock and screenshotted (nothing dropped,
so the voice lines up to the frame), piped into ffmpeg; then each step's
spoken clip laid under it where the step begins, and the scenes marked
as chapters. Run in a process of its own (app/video/jobs.py):

    python -m app.video.render <job dir>     reads story.json, narr.json, narr/*.wav
                                             writes video.mp4, result.json

The page is opened from disk with the story handed in (window.__STORY),
so it needs neither the app's address nor a login. 1280×720 at 20
frames a second, H.264 (veryfast, two threads) and AAC.
Progress lines on stdout: 'p 0.42'.
"""

from __future__ import annotations

import array
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .voice import SR, read_wav, write_wav

FPS = int(os.environ.get("LINCK_VIDEO_FPS") or 20)   # charts and counters need no more (tests use fewer)
SIZE = (1280, 720)
STAGE = Path(__file__).resolve().parent.parent.parent / "static" / "linck" / "video" / "stage.html"


def ffmpeg() -> str:
    exe = os.environ.get("LINCK_FFMPEG") or shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise SystemExit("No ffmpeg: install it, or the imageio-ffmpeg package, or set LINCK_FFMPEG.")


def record(job: Path) -> dict:
    from playwright.sync_api import sync_playwright
    story = json.loads((job / "story.json").read_text())
    narr = json.loads((job / "narr.json").read_text()) if (job / "narr.json").exists() else None
    ff = ffmpeg()
    silent = job / "picture.mp4"
    enc = subprocess.Popen([ff, "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "2", "-pix_fmt", "yuv420p",
                            "-r", str(FPS), str(silent)], stdin=subprocess.PIPE)
    plans, frames_at = [], []
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--disable-gpu", "--disable-dev-shm-usage", "--renderer-process-limit=1",
                                     "--disable-extensions", "--mute-audio"])
        page = b.new_page(viewport={"width": SIZE[0], "height": SIZE[1]}, device_scale_factor=1)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"window.__STORY = {json.dumps(story)}; window.__NARR = {json.dumps(narr)};")
        page.goto(f"{STAGE.as_uri()}?record=1")
        n = page.evaluate("window.__ready")
        plans = [page.evaluate("n => window.__plan(n)", s) for s in range(1, n + 1)]
        total = sum(math.ceil(p["total"] * FPS) for p in plans) or 1
        done = 0
        for s, p in enumerate(plans, 1):
            frames_at.append(done)
            for f in range(math.ceil(p["total"] * FPS)):
                page.evaluate("([s, t]) => window.__seek(s, t)", [s, f / FPS])
                enc.stdin.write(page.screenshot(type="jpeg", quality=88))
                done += 1
                if done % 50 == 0:
                    print(f"p {done / total * 0.95:.3f}", flush=True)
            if errors:
                raise SystemExit(f"The stage failed in scene {s}: {errors[0]}")
        b.close()
    enc.stdin.close()
    if enc.wait():
        raise SystemExit("ffmpeg couldn't encode the frames")

    # the voice: each step's clip where its step begins, on frame-exact scene starts
    length = sum(math.ceil(p["total"] * FPS) for p in plans) / FPS
    track = array.array("h", bytes(2 * (int(length * SR) + SR)))
    for s, p in enumerate(plans):
        base = frames_at[s] / FPS
        for st in p["steps"]:
            if not st.get("file") or not (job / "narr" / f"{st['file']}.wav").exists():
                continue
            a = read_wav(job / "narr" / f"{st['file']}.wav")
            i = int(round((base + st["T0"]) * SR))
            track[i:i + len(a)] = a[:max(0, len(track) - i)]
    write_wav(job / "voice.wav", track)

    # the scenes as chapters (a player lists them)
    chapters = [{"t": round(frames_at[s] / FPS, 2), "title": p["title"]} for s, p in enumerate(plans)]
    meta = [";FFMETADATA1", f"title={_esc(story['title'])}"]
    for c, nxt in zip(chapters, chapters[1:] + [{"t": length}]):
        meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(c['t'] * 1000)}", f"END={int(nxt['t'] * 1000)}", f"title={_esc(c['title'])}"]
    (job / "chapters.txt").write_text("\n".join(meta) + "\n")
    out = job / "video.mp4"
    subprocess.run([ff, "-loglevel", "error", "-y", "-i", str(silent), "-i", str(job / "voice.wav"), "-i", str(job / "chapters.txt"),
                    "-map", "0:v", "-map", "1:a", "-map_metadata", "2", "-map_chapters", "2", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(out)], check=True)
    for f in (silent, job / "voice.wav", job / "chapters.txt"):
        f.unlink(missing_ok=True)
    shutil.rmtree(job / "narr", ignore_errors=True)
    res = {"seconds": round(length, 2), "bytes": out.stat().st_size, "chapters": chapters}
    (job / "result.json").write_text(json.dumps(res))
    print("p 1.000", flush=True)
    return res


def _esc(s: str) -> str:
    return "".join("\\" + ch if ch in "=;#\\\n" else ch for ch in s)


if __name__ == "__main__":
    record(Path(sys.argv[1]))
