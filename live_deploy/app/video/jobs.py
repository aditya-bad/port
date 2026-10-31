"""
The videos queue: one video at a time, made in processes of their own at
the lowest CPU priority, so the strategies always come first.

  queued   waiting its turn, or for memory (note says which)
  voicing  python -m app.video.voice   the narration (about 360 MB at its peak)
  drawing  python -m app.video.render  the frames and the MP4 (Chromium and ffmpeg, about 510 MB)
  done     video.mp4 in the videos folder; seconds, bytes, chapters stored
  failed   note says why

A video starts only with memory to spare: at least MIN_FREE_MB free
(RAM and swap together, which is what a swap file is for) and
MIN_RAM_MB of real RAM. A video caught half made by a restart or a crash
starts again from its stored story when the app comes back.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("live_deploy.video")

ROOT = Path(__file__).resolve().parent.parent.parent       # live_deploy/ (the image's /app)
MIN_FREE_MB = 1100
MIN_RAM_MB = 220
POLL_S = 5.0
WAIT_MEMORY_S = 30.0


def video_dir() -> Path:
    return Path(os.environ.get("LINCK_VIDEO_DIR") or ROOT / "videos")


def memory_mb() -> tuple[int, int]:
    """(available RAM, free swap) in MB, from /proc/meminfo; generous
    where it can't be read."""
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0]) // 1024
        return info.get("MemAvailable", 0), info.get("SwapFree", 0)
    except (OSError, ValueError):
        return 10_000, 0


def can_make() -> tuple[bool, str]:
    """Whether this server has what a video needs: the voice, a browser
    for the frames, and ffmpeg."""
    from . import voice
    if not voice.available():
        return False, "The voice model isn't installed on this server. Rebuild the image (it's in the Dockerfile) to add it."
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False, "The browser that draws the frames isn't installed on this server. Rebuild the image to add it."
    try:
        from .render import ffmpeg
        ffmpeg()
    except SystemExit as e:
        return False, str(e)
    return True, ""


class VideoJobs:
    def __init__(self, pool, folder: Optional[Path] = None):
        self.pool = pool
        self.folder = folder        # None: LINCK_VIDEO_DIR, read when each video is made
        self.wake = asyncio.Event()
        self.current: Optional[str] = None
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.killed: set[str] = set()

    def poke(self) -> None:
        self.wake.set()

    async def requeue(self) -> int:
        """Videos caught half made by a stop: made again from the start."""
        r = await self.pool.execute(
            "UPDATE videos SET status = 'queued', progress = 0, note = 'Starting again: the app restarted while it was being made.' "
            "WHERE status IN ('voicing', 'drawing')")
        return int(r.split()[-1])

    async def loop(self) -> None:
        await self.requeue()
        while True:
            try:
                ran = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("video queue")
                ran = False
            if not ran:
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), POLL_S)
                except asyncio.TimeoutError:
                    pass

    async def run_once(self, check_memory: bool = True) -> bool:
        """Make the oldest queued video, if there's memory for it. True if
        one was made (or failed)."""
        row = await self.pool.fetchrow("SELECT * FROM videos WHERE status = 'queued' ORDER BY created_at LIMIT 1")
        if row is None:
            return False
        if check_memory:
            ram, swap = memory_mb()
            if ram + swap < MIN_FREE_MB or ram < MIN_RAM_MB:
                await self.pool.execute("UPDATE videos SET note = $2 WHERE id = $1", row["id"],
                                        f"Waiting for memory: {ram} MB free, {swap} MB of swap. It starts when "
                                        f"{MIN_FREE_MB} MB is free in all.")
                await asyncio.sleep(WAIT_MEMORY_S)
                return False
        await self.make(row)
        return True

    async def make(self, row) -> None:
        vid = str(row["id"])
        job = (self.folder or video_dir()) / vid
        shutil.rmtree(job, ignore_errors=True)
        job.mkdir(parents=True, exist_ok=True)
        story = row["story"] if isinstance(row["story"], dict) else json.loads(row["story"])
        (job / "story.json").write_text(json.dumps(story, ensure_ascii=False))
        (job / "voice.txt").write_text(row["voice"])
        self.current = vid
        t0 = time.monotonic()
        try:
            await self.pool.execute("UPDATE videos SET status = 'voicing', progress = 0.01, note = NULL, started_at = now() WHERE id = $1", row["id"])
            await self._step(row["id"], "app.video.voice", job, 0.01, 0.30)
            await self.pool.execute("UPDATE videos SET status = 'drawing' WHERE id = $1", row["id"])
            await self._step(row["id"], "app.video.render", job, 0.31, 0.99)
            res = json.loads((job / "result.json").read_text())
            for f in job.iterdir():
                if f.name != "video.mp4":
                    if f.is_dir():
                        shutil.rmtree(f, ignore_errors=True)
                    else:
                        f.unlink(missing_ok=True)
            await self.pool.execute(
                "UPDATE videos SET status = 'done', progress = 1, note = NULL, seconds = $2, bytes = $3, chapters = $4::jsonb, "
                "finished_at = now() WHERE id = $1", row["id"], res["seconds"], res["bytes"], json.dumps(res["chapters"]))
            logger.info("video %s made in %.0f s: %.0f s long, %.1f MB", vid, time.monotonic() - t0, res["seconds"], res["bytes"] / 1e6)
        except Exception as e:
            if vid in self.killed:
                self.killed.discard(vid)
                shutil.rmtree(job, ignore_errors=True)
            else:
                logger.warning("video %s failed: %s", vid, e)
                await self.pool.execute("UPDATE videos SET status = 'failed', note = $2, finished_at = now() WHERE id = $1",
                                        row["id"], str(e)[:600] or type(e).__name__)
                shutil.rmtree(job, ignore_errors=True)
        finally:
            self.current, self.proc = None, None

    async def _step(self, vid, module: str, job: Path, lo: float, hi: float) -> None:
        def lowest():
            os.nice(19)            # the strategies come first; the browser and ffmpeg inherit it
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", module, str(job), cwd=str(ROOT), preexec_fn=lowest,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        tail: deque = deque(maxlen=40)

        async def errs():
            async for line in self.proc.stderr:
                tail.append(line.decode(errors="replace").rstrip())
        reader = asyncio.create_task(errs())
        last = 0.0
        async for line in self.proc.stdout:
            s = line.decode(errors="replace").strip()
            if s.startswith("p ") and time.monotonic() - last > 2:
                last = time.monotonic()
                try:
                    p = lo + (hi - lo) * float(s[2:])
                except ValueError:
                    continue
                await self.pool.execute("UPDATE videos SET progress = $2 WHERE id = $1", vid, p)
        code = await self.proc.wait()
        await reader
        if code:
            why = next((x for x in reversed(tail) if x and not x.startswith(" ")), "") or f"{module} stopped ({code})"
            raise RuntimeError(why.split(": ", 1)[-1] if why.startswith(("SystemExit", "RuntimeError")) else why)

    async def cancel(self, vid: str) -> None:
        """Stop a video being made (it's being deleted)."""
        if self.current == vid and self.proc is not None and self.proc.returncode is None:
            self.killed.add(vid)
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass

    async def stop(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass


def file_of(vid: str) -> Path:
    return video_dir() / vid / "video.mp4"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
