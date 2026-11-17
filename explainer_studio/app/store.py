"""Projects, on disk: one folder each under data/projects/<id>/.

    project.json   title, brief, settings, when made and changed
    meta.json      what the kit reads: title, slug, description, voice, speed, say
    scenes.js      the script: scenes, their steps (what's said) and drawing
    uploads/       the material it's made from
    narr/ out/ check/   the kit's own working folders
    outputs/       the finished files, one set per render
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Optional

from . import config

OUTPUTS = ["html", "mp4", "scenes", "youtube", "srt", "vtt", "chapters", "thumbnail"]
RESOLUTIONS = {"720p": (1280, 720), "1080p": (1920, 1080), "4k": (3840, 2160), "vertical": (1080, 1920)}
VOICES = [
    ("af_heart", "Heart (US, female)"), ("af_bella", "Bella (US, female)"), ("af_nicole", "Nicole (US, female)"),
    ("af_sarah", "Sarah (US, female)"), ("am_adam", "Adam (US, male)"), ("am_michael", "Michael (US, male)"),
    ("am_eric", "Eric (US, male)"), ("bf_emma", "Emma (UK, female)"), ("bf_isabella", "Isabella (UK, female)"),
    ("bm_george", "George (UK, male)"), ("bm_lewis", "Lewis (UK, male)"),
]
DEFAULTS = {
    "voice": "af_heart", "speed": 0.95, "theme": "light", "resolution": "1080p", "fps": 30,
    "outputs": ["html", "mp4", "youtube", "srt", "chapters", "thumbnail"],
    "burn_captions": False, "intro": True, "outro": False, "outro_text": "Thanks for watching",
    "say": [],
}
ID = re.compile(r"^[a-z0-9]{10}$")
UPLOAD_MAX = 50 * 1024 * 1024


class NotFound(Exception):
    pass


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:48] or "explainer"


def root(pid: str) -> Path:
    if not ID.match(pid or ""):
        raise NotFound(pid)
    p = config.PROJECTS / pid
    if not (p / "project.json").exists():
        raise NotFound(pid)
    return p


def clean_settings(s: dict, base: Optional[dict] = None) -> dict:
    out = dict(base or DEFAULTS)
    for k, v in (s or {}).items():
        if k not in DEFAULTS:
            continue
        if k == "voice" and v not in dict(VOICES):
            raise ValueError(f"Unknown voice {v!r}")
        if k == "speed":
            v = float(v)
            if not 0.7 <= v <= 1.3:
                raise ValueError("Speed is between 0.7 and 1.3")
        if k == "theme" and v not in ("light", "dark"):
            raise ValueError("Theme is light or dark")
        if k == "resolution" and v not in RESOLUTIONS:
            raise ValueError(f"Resolution is one of {', '.join(RESOLUTIONS)}")
        if k == "fps":
            v = int(v)
            if v not in (25, 30, 60):
                raise ValueError("Frame rate is 25, 30 or 60")
        if k == "outputs":
            v = [o for o in OUTPUTS if o in set(v or [])]
            if not v:
                raise ValueError("Pick at least one output")
        if k in ("burn_captions", "intro", "outro"):
            v = bool(v)
        if k == "outro_text":
            v = str(v)[:120]
        if k == "say":
            v = [[str(a)[:60], str(b)[:120]] for a, b in v if str(a).strip()]
        out[k] = v
    return out


def read(pid: str) -> dict:
    p = root(pid)
    d = json.loads((p / "project.json").read_text())
    d["settings"] = clean_settings({}, {**DEFAULTS, **d.get("settings", {})})
    d["has_script"] = (p / "scenes.js").exists() and bool((p / "scenes.js").read_text().strip())
    d["uploads"] = uploads(pid)
    d["outputs"] = outputs(pid)
    return d


def write(pid: str, d: dict) -> None:
    p = root(pid) if (config.PROJECTS / pid / "project.json").exists() else config.PROJECTS / pid
    keep = {k: d[k] for k in ("id", "title", "brief", "description", "created", "settings") if k in d}
    keep["updated"] = time.time()
    (p / "project.json").write_text(json.dumps(keep, indent=1, ensure_ascii=False))
    write_meta(pid, keep)


def write_meta(pid: str, d: dict) -> None:
    """The kit's meta.json, from the project."""
    s = d["settings"]
    meta = {"title": d["title"], "slug": slugify(d["title"]), "description": d.get("description") or d.get("brief", "")[:200],
            "voice": s["voice"], "speed": s["speed"], "say": s["say"]}
    (config.PROJECTS / pid / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))


def create(title: str, brief: str = "") -> dict:
    config.PROJECTS.mkdir(parents=True, exist_ok=True)
    pid = secrets.token_hex(5)
    p = config.PROJECTS / pid
    (p / "uploads").mkdir(parents=True)
    (p / "outputs").mkdir()
    d = {"id": pid, "title": (title or "Untitled explainer").strip()[:120], "brief": (brief or "").strip()[:4000],
         "description": "", "created": time.time(), "settings": dict(DEFAULTS)}
    write(pid, d)
    return read(pid)


def update(pid: str, patch: dict) -> dict:
    d = json.loads((root(pid) / "project.json").read_text())
    for k in ("title", "brief", "description"):
        if k in patch:
            d[k] = str(patch[k] or "").strip()[:4000 if k == "brief" else 300]
    if not d.get("title"):
        raise ValueError("A project needs a title")
    if "settings" in patch:
        d["settings"] = clean_settings(patch["settings"], {**DEFAULTS, **d.get("settings", {})})
    write(pid, d)
    return read(pid)


def listing() -> list[dict]:
    out = []
    for p in sorted(config.PROJECTS.glob("*/project.json"), key=lambda f: -f.stat().st_mtime) if config.PROJECTS.exists() else []:
        try:
            d = json.loads(p.read_text())
            out.append({"id": d["id"], "title": d["title"], "updated": d.get("updated"), "created": d.get("created"),
                        "has_script": (p.parent / "scenes.js").exists(), "outputs": len(outputs(d["id"]))})
        except Exception:
            continue
    return out


def delete(pid: str) -> None:
    shutil.rmtree(root(pid))


# ── the script ─────────────────────────────────────────────────────────

def script(pid: str) -> str:
    f = root(pid) / "scenes.js"
    return f.read_text() if f.exists() else ""


def save_script(pid: str, text: str) -> None:
    if len(text) > 2_000_000:
        raise ValueError("The script is over 2 MB")
    (root(pid) / "scenes.js").write_text(text)


def example_script() -> str:
    return (config.KIT / "example" / "scenes.js").read_text()


# ── uploads ────────────────────────────────────────────────────────────

def safe_name(name: str) -> str:
    n = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name or "file").name).strip("._") or "file"
    return n[:120]


def save_upload(pid: str, name: str, data: bytes) -> str:
    if len(data) > UPLOAD_MAX:
        raise ValueError(f"{name}: over {UPLOAD_MAX // 1024 // 1024} MB")
    d = root(pid) / "uploads"
    d.mkdir(exist_ok=True)
    n = safe_name(name)
    (d / n).write_bytes(data)
    return n


def uploads(pid: str) -> list[dict]:
    d = root(pid) / "uploads"
    return [{"name": f.name, "size": f.stat().st_size} for f in sorted(d.iterdir()) if f.is_file()] if d.exists() else []


def delete_upload(pid: str, name: str) -> None:
    f = root(pid) / "uploads" / safe_name(name)
    if not f.exists():
        raise NotFound(name)
    f.unlink()


def outputs(pid: str) -> list[dict]:
    d = root(pid) / "outputs"
    return [{"name": f.name, "size": f.stat().st_size, "at": f.stat().st_mtime}
            for f in sorted(d.iterdir()) if f.is_file()] if d.exists() else []


def file_in(pid: str, rel: str) -> Path:
    """A file inside the project, never outside it."""
    base = root(pid).resolve()
    f = (base / rel).resolve()
    if base not in f.parents or not f.is_file():
        raise NotFound(rel)
    return f
