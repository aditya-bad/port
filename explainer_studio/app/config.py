"""Where everything is: set by the environment (the Docker image sets all
of it), with defaults that work on a developer's machine after
`scripts/setup-local.sh`."""

from __future__ import annotations

import os
import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
KIT = pathlib.Path(os.environ.get("STUDIO_KIT", ROOT / "kit"))
UI = ROOT / "ui"
DATA = pathlib.Path(os.environ.get("STUDIO_DATA", ROOT / "data"))
PROJECTS = DATA / "projects"
PORT = int(os.environ.get("STUDIO_PORT", "47391"))

# the voice model and its Python (Kokoro), and where spoken sentences are kept
EV_HOME = pathlib.Path(os.environ.get("EV_HOME", pathlib.Path.home() / ".cache/explainer-video"))
PY = os.environ.get("STUDIO_PY") or str(EV_HOME / "venv/bin/python")
VOICE_CACHE = pathlib.Path(os.environ.get("VOICE_CACHE", DATA / "voice-cache"))
# the browser and the recorder
NODE = os.environ.get("STUDIO_NODE") or shutil.which("node") or "node"
PW = os.environ.get("PW") or str(EV_HOME / "node/node_modules/playwright/index.mjs")
CHROMIUM = os.environ.get("CHROMIUM", "")
FF = os.environ.get("FF") or shutil.which("ffmpeg") or "ffmpeg"
# how many scenes are recorded at once (the container's CPU limit is the real cap)
WORKERS = int(os.environ.get("STUDIO_WORKERS", "2"))

SETTINGS = DATA / "settings.json"


def tool_env() -> dict:
    """The environment every kit tool runs with."""
    env = dict(os.environ)
    env.update({"EV_HOME": str(EV_HOME), "PW": PW, "FF": FF, "VOICE_CACHE": str(VOICE_CACHE)})
    if CHROMIUM:
        env["CHROMIUM"] = CHROMIUM
    return env
