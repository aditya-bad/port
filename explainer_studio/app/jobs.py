"""Background jobs, one at a time: a preview, a check, a render, a Claude
draft. Each keeps a log (the tools' own output) and says which phase it's
in and how far along; a job can be cancelled. Jobs live in memory with
their logs on disk: a restart marks a job that was running as
interrupted."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import signal
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from . import captions, config, store

KINDS = ("preview", "check", "render", "deck", "draft")


class Cancelled(Exception):
    pass


class Failed(Exception):
    pass


class Job:
    def __init__(self, kind: str, pid: str, opts: dict):
        self.id = secrets.token_hex(6)
        self.kind, self.pid, self.opts = kind, pid, opts
        self.state = "queued"           # queued, running, done, failed, cancelled
        self.phase, self.progress = "Waiting its turn", 0.0
        self.error: Optional[str] = None
        self.result: dict = {}
        self.created, self.started, self.finished = time.time(), None, None
        self.cancel = False
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.log_file = store.root(pid) / "jobs" / f"{self.id}.log"
        self.log_file.parent.mkdir(exist_ok=True)
        self.log_file.write_text("")

    def view(self, tail: int = 200) -> dict:
        lines = self.log_file.read_text(errors="replace").splitlines()[-tail:] if self.log_file.exists() else []
        return {"id": self.id, "kind": self.kind, "project": self.pid, "state": self.state, "phase": self.phase,
                "progress": round(self.progress, 3), "error": self.error, "result": self.result,
                "created": self.created, "started": self.started, "finished": self.finished, "log": lines}

    def log(self, line: str) -> None:
        with self.log_file.open("a") as f:
            f.write(line.rstrip("\n") + "\n")

    def at(self, phase: str, progress: Optional[float] = None) -> None:
        if self.cancel:
            raise Cancelled()
        self.phase = phase
        if progress is not None:
            self.progress = progress
        self.log(f"── {phase}")

    async def run(self, *cmd, cwd: Optional[Path] = None, env: Optional[dict] = None,
                  out: Optional[Path] = None, on_line: Optional[Callable[[str], None]] = None,
                  quiet: Optional[Callable[[str], bool]] = None) -> str:
        """Run a tool; its output goes to the log (or, with `out`, stdout to
        that file). Raises Failed with the last lines when it fails."""
        if self.cancel:
            raise Cancelled()
        self.log("$ " + " ".join(str(c) for c in cmd))
        self.proc = await asyncio.create_subprocess_exec(
            *[str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env or config.tool_env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        chunks: list[bytes] = []
        tail: list[str] = []

        async def pump(stream, keep: bool):
            async for raw in stream:
                if keep:
                    chunks.append(raw)
                    continue
                line = raw.decode(errors="replace").rstrip()
                if on_line:
                    on_line(line)
                if quiet and quiet(line):
                    continue
                tail.append(line)
                del tail[:-30]
                self.log(line)
        await asyncio.gather(pump(self.proc.stdout, out is not None), pump(self.proc.stderr, False))
        code = await self.proc.wait()
        self.proc = None
        if self.cancel:
            raise Cancelled()
        data = b"".join(chunks)
        if out is not None:
            out.write_bytes(data)
        if code != 0:
            raise Failed(f"{Path(str(cmd[1] if len(cmd) > 1 else cmd[0])).name} failed (exit {code})"
                         + (": " + " / ".join(t for t in tail[-4:] if t) if tail else ""))
        return data.decode(errors="replace")


JOBS: dict[str, Job] = {}
QUEUE: "asyncio.Queue[Job]" = None   # made on startup
RUNNERS: dict[str, Callable[[Job], Awaitable[None]]] = {}


def submit(kind: str, pid: str, opts: Optional[dict] = None) -> Job:
    if kind not in KINDS:
        raise ValueError(f"Unknown job {kind!r}")
    store.root(pid)
    busy = [j for j in JOBS.values() if j.pid == pid and j.state in ("queued", "running")]
    if busy:
        raise ValueError(f"This project already has a {busy[0].kind} {busy[0].state}; wait for it or cancel it")
    j = Job(kind, pid, opts or {})
    JOBS[j.id] = j
    QUEUE.put_nowait(j)
    return j


def cancel(jid: str) -> Job:
    j = JOBS[jid]
    if j.state in ("queued", "running"):
        j.cancel = True
        if j.state == "queued":
            j.state, j.phase, j.finished = "cancelled", "Cancelled", time.time()
        if j.proc is not None:
            try:
                os.killpg(j.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    return j


def project_jobs(pid: str) -> list[dict]:
    return [j.view(tail=0) for j in sorted(JOBS.values(), key=lambda j: -j.created) if j.pid == pid][:20]


async def worker() -> None:
    while True:
        j = await QUEUE.get()
        if j.state != "queued":
            continue
        j.state, j.started = "running", time.time()
        try:
            await RUNNERS[j.kind](j)
            j.state, j.phase, j.progress = "done", "Done", 1.0
        except Cancelled:
            j.state, j.phase = "cancelled", "Cancelled"
            j.log("Cancelled.")
        except Failed as e:
            j.state, j.error = "failed", str(e)
            j.log(f"Failed: {e}")
        except Exception as e:   # a bug: say what it was
            j.state, j.error = "failed", f"{type(e).__name__}: {e}"
            j.log(f"Failed: {type(e).__name__}: {e}")
        j.finished = time.time()


def start() -> None:
    global QUEUE
    QUEUE = asyncio.Queue()
    asyncio.get_running_loop().create_task(worker(), name="studio-jobs")


# ── the steps ─────────────────────────────────────────────────────────

def _tool(name: str) -> Path:
    return config.KIT / "tools" / name


async def outline(j: Job) -> list:
    """steps.mjs: the scenes and what's said, checked for mistakes."""
    p = store.root(j.pid)
    if not store.script(j.pid).strip():
        raise Failed("There's no script yet: write one, upload one, or draft one with Claude")
    await j.run(config.NODE, _tool("steps.mjs"), p / "scenes.js", out=p / "steps.json")
    return json.loads((p / "steps.json").read_text())


async def build(j: Job, voice: bool) -> Path:
    p = store.root(j.pid)
    await j.run(config.PY, _tool("build.py"), p, *([] if voice else ["--no-voice"]))
    meta = json.loads((p / "meta.json").read_text())
    return p / "out" / f"{meta['slug']}.html"


async def speak(j: Job, steps: list, lo: float, hi: float) -> None:
    p = store.root(j.pid)
    n = max(1, len(steps))
    done = [0]

    def line(s: str) -> None:
        if s.startswith("scene "):
            done[0] += 1
            j.progress = lo + (hi - lo) * done[0] / n
    config.VOICE_CACHE.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(p / "narr", ignore_errors=True)
    await j.run(config.PY, _tool("narr.py"), p, on_line=line)


async def run_preview(j: Job) -> None:
    j.at("Reading the script", 0.1)
    steps = await outline(j)
    j.at("Building the page (captions only)", 0.5)
    html = await build(j, voice=False)
    j.result = {"preview": str(html.relative_to(store.root(j.pid))), "scenes": len(steps)}


async def run_check(j: Job) -> None:
    p = store.root(j.pid)
    j.at("Reading the script", 0.05)
    steps = await outline(j)
    j.at("Speaking the narration", 0.1)
    await speak(j, steps, 0.1, 0.5)
    j.at("Building the page", 0.5)
    html = await build(j, voice=True)
    j.at("Checking every scene", 0.55)
    report: list[str] = []
    try:
        await j.run(config.NODE, _tool("check.mjs"), html, on_line=report.append)
        ok = True
    except Failed:
        ok = False
    frames = sorted(f.name for f in (p / "check").glob("*.png")) if (p / "check").exists() else []
    tail = [line for line in report if "warning" in line or "failure" in line or line.startswith("  ")]
    j.result = {"ok": ok, "preview": str(html.relative_to(p)), "frames": [f"check/{f}" for f in frames],
                "summary": tail[-40:]}
    if not ok:
        raise Failed("The check found problems: see the summary below and the log")


async def run_render(j: Job) -> None:
    p = store.root(j.pid)
    d = store.read(j.pid)
    s = d["settings"]
    want = set(j.opts.get("outputs") or s["outputs"])
    slug = store.slugify(d["title"])
    video = bool(want & {"mp4", "scenes", "youtube", "thumbnail"})
    j.at("Reading the script", 0.02)
    steps = await outline(j)
    j.at("Speaking the narration", 0.04)
    await speak(j, steps, 0.04, 0.2)
    j.at("Building the page", 0.2)
    html = await build(j, voice=True)
    out = clear_outputs(p, keep=lambda f: f.suffix == ".pptx")    # the PowerPoint is made on its own
    made: list[str] = []
    if "html" in want:
        shutil.copy(html, out / f"{slug}.html")
        made.append(f"{slug}.html")
    w, h = store.RESOLUTIONS[s["resolution"]]
    vertical = s["resolution"] == "vertical"
    scale = 1.2 if vertical else w / 1600
    env = config.tool_env() | {"FPS": str(s["fps"]), "SCALE": str(scale), "SCHEME": s["theme"],
                               "WORKERS": str(config.WORKERS)}
    if not s["burn_captions"]:
        env["NOCAP"] = "1"     # a clean picture: the captions are in the subtitle files
    j.at("Timing the scenes", 0.22)
    plans = json.loads(await j.run(config.NODE, _tool("plans.mjs"), html, env=env, out=p / "out" / "plans.json"))
    main = p / "out" / f"{slug}.mp4"
    if video:
        prog = p / "out" / "progress.txt"
        prog.write_text("")
        n = len(plans)

        def line(_s: str) -> None:
            k = len(prog.read_text().split())
            j.progress = 0.25 + 0.5 * k / max(1, n)
        j.at(f"Recording {n} scene(s) at {s['resolution']}, {s['fps']} fps", 0.25)
        shutil.rmtree(p / "out" / "vid", ignore_errors=True)
        await j.run(config.NODE, _tool("render.mjs"), html, env=env | {"PROGRESS": str(prog)}, on_line=line)
        j.at("Putting the voice under the picture", 0.76)
        await j.run(config.PY, _tool("mux.py"), p, env=env | ({} if s["sound"] else {"MUTE": "1"}))
        if "mp4" in want:
            shutil.copy(main, out / f"{slug}.mp4")
            made.append(f"{slug}.mp4")
        if "scenes" in want:
            for f in sorted((p / "out" / "vid").glob("[0-9][0-9]-*.mp4")):
                if not f.name.endswith("-v.mp4"):
                    shutil.copy(f, out / f"{slug}-scene-{f.name}")
                    made.append(f"{slug}-scene-{f.name}")
    narr = json.loads((p / "narr" / "narr.json").read_text())
    plain = captions.cues(narr, plans)
    if "srt" in want:
        (out / f"{slug}.srt").write_text(captions.srt(plain))
        made.append(f"{slug}.srt")
    if "vtt" in want:
        (out / f"{slug}.vtt").write_text(captions.vtt(plain))
        made.append(f"{slug}.vtt")
    intro = 0.0
    notes: list[str] = []
    if "youtube" in want:
        j.at("Making the YouTube file", 0.8)
        intro = await youtube(j, p, d, s, slug, main, plans, narr, env, out, made)
    if "chapters" in want:
        text, notes = captions.chapters(plans, intro)
        (out / f"{slug}-chapters.txt").write_text(text)
        made.append(f"{slug}-chapters.txt")
    if "thumbnail" in want:
        j.at("Taking the thumbnail", 0.97)
        first = plans[0]
        at = min(float(first["total"]) * 0.6, max(0.0, float(first["total"]) - 0.5))
        await j.run(config.FF, "-loglevel", "error", "-y", "-ss", f"{at:.2f}", "-i", main, "-frames:v", "1",
                    "-vf", "scale=1280:720", "-q:v", "3", out / f"{slug}-thumbnail.jpg")
        made.append(f"{slug}-thumbnail.jpg")
    j.result = {"files": made, "notes": notes, "minutes": round(sum(float(x["total"]) for x in plans) / 60, 2)}


def clear_outputs(p: Path, keep: Callable[[Path], bool]) -> Path:
    """Empty outputs/ of what this job remakes, keeping what the other one made."""
    out = p / "outputs"
    out.mkdir(exist_ok=True)
    for f in out.iterdir():
        if f.is_file() and not keep(f):
            f.unlink()
    return out


async def run_deck(j: Job) -> None:
    """The PowerPoint, with its own settings (settings.deck): a slide per step
    (or per scene) playing its animation, recorded for the deck at its own size
    and look, with or without the voice; the sentence as an editable caption,
    in the picture, or not at all; speaker notes; a title slide; the animation
    starting by itself or on a click, and the next slide following when it ends."""
    p = store.root(j.pid)
    d = store.read(j.pid)
    s, ds = d["settings"], d["settings"]["deck"]
    slug = store.slugify(d["title"])
    j.at("Reading the script", 0.02)
    steps = await outline(j)
    j.at("Speaking the narration", 0.04)
    await speak(j, steps, 0.04, 0.2)       # even without sound: the slides keep the narrated pacing
    j.at("Building the page", 0.2)
    html = await build(j, voice=True)
    w, _h = store.RESOLUTIONS[ds["resolution"]]
    per_scene = ds["per"] == "scene"
    # a scene slide's sentences change while it plays, so its captions go in the picture
    burn = ds["captions"] == "picture" or (per_scene and ds["captions"] == "text")
    vid = p / "out" / "deck-vid"
    env = config.tool_env() | {"FPS": str(ds["fps"]), "SCALE": str(w / 1600), "SCHEME": ds["theme"],
                               "WORKERS": str(config.WORKERS), "VID": str(vid)}
    if not burn:
        env["NOCAP"] = "1"
    j.at("Timing the scenes", 0.22)
    plans = json.loads(await j.run(config.NODE, _tool("plans.mjs"), html, env=env, out=p / "out" / "plans.json"))
    prog = p / "out" / "progress.txt"
    prog.write_text("")
    n = len(plans)

    def line(_s: str) -> None:
        j.progress = 0.25 + 0.5 * len(prog.read_text().split()) / max(1, n)
    j.at(f"Recording {n} scene(s) at {ds['resolution']}, {ds['fps']} fps", 0.25)
    shutil.rmtree(vid, ignore_errors=True)
    await j.run(config.NODE, _tool("render.mjs"), html, env=env | {"PROGRESS": str(prog)}, on_line=line)
    if ds["sound"]:
        j.at("Laying the voice under each scene", 0.76)
        await j.run(config.PY, _tool("mux.py"), p, env=env | {"WAV_ONLY": "1"})
    j.at("Cutting the slides' clips", 0.8)
    work = p / "out" / "deck"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    pieces = []      # (scene no., from, to, caption, notes)
    for si, plan in enumerate(plans, 1):
        sts, total = plan["steps"], float(plan["total"])
        said = [x.get("text", "") for x in (steps[si - 1]["steps"] if si - 1 < len(steps) else [])]
        if per_scene:
            pieces.append((si, 0.0, total, plan.get("title", ""), "\n\n".join(t for t in said if t)))
            continue
        for k in range(len(sts)):
            a = 0.0 if k == 0 else float(sts[k]["T0"])
            b = float(sts[k + 1]["T0"]) if k + 1 < len(sts) else total
            if b - a >= 0.2:
                text = said[k] if k < len(said) else ""
                pieces.append((si, a, b, text, text))
    slides = []
    for i, (si, a, b, caption, notes) in enumerate(pieces, 1):
        clip, poster = work / f"{i:03d}.mp4", work / f"{i:03d}.png"
        src = ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(vid / f"{si:02d}-v.mp4")]
        if ds["sound"]:
            src += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(vid / f"{si:02d}.wav")]
            audio = ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
        else:
            audio = ["-an"]
        await j.run(config.FF, "-loglevel", "error", "-y", *src, *audio, "-c:v", "libx264", "-preset", "medium",
                    "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", clip)
        await j.run(config.FF, "-loglevel", "error", "-y", "-sseof", "-0.25", "-i", clip, "-update", "1", "-frames:v", "1", poster)
        slides.append({"video": str(clip), "poster": str(poster), "dur": round(b - a, 3), "scene": si,
                       "caption": caption if ds["captions"] == "text" and not per_scene else "",
                       "notes": notes if ds["notes"] else ""})
        j.progress = 0.8 + 0.15 * i / max(1, len(pieces))
    card_png = None
    if ds["title_slide"]:
        card_png = work / "title.png"
        await j.run(config.NODE, _tool("card.mjs"), card_png, d["title"], d.get("description") or "", env=env)
    j.at("Making the PowerPoint", 0.96)
    out = clear_outputs(p, keep=lambda f: f.suffix != ".pptx")
    spec = {"out": str(out / f"{slug}.pptx"), "title": d["title"], "titleCard": str(card_png) if card_png else None,
            "theme": ds["theme"], "sound": ds["sound"], "start": ds["start"], "advance": ds["advance"],
            "notes": ds["notes"], "slides": slides}
    (work / "spec.json").write_text(json.dumps(spec, ensure_ascii=False))
    await j.run(config.NODE, _tool("deck.mjs"), work / "spec.json", env=env)
    j.result = {"files": [f"{slug}.pptx"], "slides": len(slides) + (1 if card_png else 0),
                "minutes": round(sum(float(x["total"]) for x in plans) / 60, 2)}


CARD_SECONDS = 3.0


async def card(j: Job, p: Path, name: str, title: str, sub: str, s: dict, env: dict) -> Path:
    png, mp4 = p / "out" / f"{name}.png", p / "out" / f"{name}.mp4"
    await j.run(config.NODE, _tool("card.mjs"), png, title, sub, env=env)
    await j.run(config.FF, "-loglevel", "error", "-y", "-loop", "1", "-framerate", str(s["fps"]), "-t", str(CARD_SECONDS),
                "-i", png, "-f", "lavfi", "-t", str(CARD_SECONDS), "-i", "anullsrc=r=48000:cl=stereo",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(s["fps"]), "-c:a", "aac", "-shortest", mp4)
    return mp4


async def youtube(j: Job, p: Path, d: dict, s: dict, slug: str, main: Path, plans: list, narr: list,
                  env: dict, out: Path, made: list) -> float:
    """The upload file: optional title cards, H.264 high / AAC 48 kHz stereo,
    the chosen size and frame rate, with its own .srt (the title card moves
    the timeline). Captions in the picture are the player's own, recorded
    with it (burn_captions). Returns the intro's length."""
    w, h = store.RESOLUTIONS[s["resolution"]]
    vertical = s["resolution"] == "vertical"
    parts = []
    intro = 0.0
    if s["intro"]:
        parts.append(await card(j, p, "intro", d["title"], d.get("description") or "", s, env))
        intro = CARD_SECONDS
    parts.append(main)
    if s["outro"]:
        parts.append(await card(j, p, "outro", s["outro_text"] or "Thanks for watching", d["title"], s, env))
    cues = captions.cues(narr, plans, offset=intro)
    srt_name = f"{slug}-youtube.srt"
    (out / srt_name).write_text(captions.srt(cues))
    made.append(srt_name)
    fps = s["fps"]
    base_w, base_h = (1920, 1080) if vertical else (w, h)
    chains, labels = [], []
    for i in range(len(parts)):
        chains.append(f"[{i}:v]scale={base_w}:{base_h},setsar=1,fps={fps},format=yuv420p[v{i}];"
                      f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(chains) + f";{''.join(labels)}concat=n={len(parts)}:v=1:a=1[vc][ac]"
    last = "vc"
    if vertical:
        paper = "0x1c1b19" if s["theme"] == "dark" else "0xf6f4ef"
        graph += f";[{last}]scale={w}:-2,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={paper}[vp]"
        last = "vp"
    target = out / f"{slug}-youtube.mp4"
    ins = []
    for f in parts:
        ins += ["-i", str(f)]
    dur = sum(float(x["total"]) for x in plans) + intro + (CARD_SECONDS if s["outro"] else 0)
    total_frames = dur * fps

    def line(l: str) -> None:
        if l.startswith("frame="):
            try:
                j.progress = 0.8 + 0.16 * min(1.0, int(l.split("=")[1].split()[0]) / total_frames)
            except (ValueError, IndexError):
                pass
    await j.run(config.FF, "-y", "-loglevel", "error", "-stats_period", "2", "-progress", "pipe:2", *ins,
                "-filter_complex", graph, "-map", f"[{last}]", "-map", "[ac]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-r", str(fps), "-g", str(fps * 2), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", target.name, cwd=out, on_line=line,
                quiet=lambda l: bool(re.match(r"^[a-z_0-9]+=", l)))   # ffmpeg's progress lines
    made.append(target.name)
    return intro


RUNNERS.update({"preview": run_preview, "check": run_check, "render": run_render, "deck": run_deck})

