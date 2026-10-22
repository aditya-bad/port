"""
live_deploy — starts the end-of-day check (custom_scripts/day_check.py) as
its own process, and notices when it dies without reporting.

The check replays deployments with the app's Kite client and clock pointed
at the past, so it never runs inside the app. One run per day at a time;
its progress and result are in day_check_runs / day_checks.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger("live_deploy.daycheck")

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "custom_scripts" / "day_check.py"


class DayCheckLauncher:
    def __init__(self, pool, script: Path = SCRIPT, python: str = sys.executable):
        self.pool = pool
        self.script, self.python = script, python
        self.tasks: dict[int, asyncio.Task] = {}

    async def running(self, day: date) -> Optional[int]:
        return await self.pool.fetchval(
            "SELECT id FROM day_check_runs WHERE day = $1 AND status = 'running' "
            "AND started_at > now() - interval '3 hours' ORDER BY id DESC LIMIT 1", day)

    async def start(self, day: date, trigger: str = "auto", deployment: Optional[str] = None) -> int:
        """Start a run (or return the one already running for that day)."""
        rid = await self.running(day)
        if rid is not None:
            return rid
        rid = await self.pool.fetchval(
            "INSERT INTO day_check_runs (day, trigger) VALUES ($1, $2) RETURNING id", day, trigger)
        self.tasks[rid] = asyncio.create_task(self._run(rid, day, deployment), name=f"day-check-{rid}")
        return rid

    async def _run(self, rid: int, day: date, deployment: Optional[str]) -> None:
        cmd = [self.python, str(self.script), "--date", day.isoformat(), "--run", str(rid)]
        if deployment:
            cmd += ["--deployment", str(deployment)]
        tail = ""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(ROOT), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await proc.communicate()
            text = out.decode(errors="replace") if out else ""
            tail = "\n".join(text.strip().splitlines()[-5:])
            for line in text.splitlines():
                logger.info("day check %s: %s", day, line)
            rc = proc.returncode
        except Exception as e:
            rc, tail = -1, f"{type(e).__name__}: {e}"
        if rc != 0:
            # the worker records its own failures; this catches one that died before it could
            await self.pool.execute(
                "UPDATE day_check_runs SET status = 'failed', finished_at = now(), "
                "error = COALESCE(error, $2) WHERE id = $1 AND status = 'running'",
                rid, f"The check stopped (exit {rc}). {tail}".strip())
        self.tasks.pop(rid, None)


WORKER = ROOT / "custom_scripts" / "repair_worker.py"


class RepairWorkerLauncher:
    """Keeps one repair worker (custom_scripts/repair_worker.py) running
    while the Repair page has jobs for it: started on demand, it stops by
    itself when idle. A worker that dies mid-job has that job marked failed
    with its last words."""

    def __init__(self, pool, script: Path = WORKER, python: str = sys.executable, idle: float = 600):
        self.pool = pool
        self.script, self.python, self.idle = script, python, idle
        self.task: Optional[asyncio.Task] = None

    async def ensure(self) -> None:
        if self.task is not None and not self.task.done():
            return
        self.task = asyncio.create_task(self._run(), name="repair-worker")

    async def _run(self) -> None:
        cmd = [self.python, str(self.script), "--idle", str(self.idle)]
        tail = ""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(ROOT), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await proc.communicate()
            text = out.decode(errors="replace") if out else ""
            for line in text.splitlines():
                logger.info("repair worker: %s", line)
            tail = "\n".join(text.strip().splitlines()[-5:])
            rc = proc.returncode
        except Exception as e:
            rc, tail = -1, f"{type(e).__name__}: {e}"
        if rc != 0:
            await self.pool.execute(
                "UPDATE repair_jobs SET status = 'failed', finished_at = now(), error = $1 WHERE status = 'running'",
                f"The repair worker stopped (exit {rc}). {tail}".strip())
        # a job queued while it was stopping: start again
        if await self.pool.fetchval("SELECT 1 FROM repair_jobs WHERE status = 'queued' LIMIT 1") and rc == 0:
            self.task = asyncio.create_task(self._run(), name="repair-worker")
