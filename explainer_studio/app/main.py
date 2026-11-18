"""Explainer Studio: a small web app that turns material into a narrated,
animated explainer (HTML, MP4, a YouTube-ready file with subtitles).

    uvicorn app.main:app --host 0.0.0.0 --port 47391
"""

from __future__ import annotations

import io
import json
import zipfile
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, draft, jobs, store


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.PROJECTS.mkdir(parents=True, exist_ok=True)
    jobs.start()
    yield


app = FastAPI(title="Explainer Studio", lifespan=lifespan)


@app.exception_handler(store.NotFound)
async def not_found(_req: Request, e: store.NotFound):
    return JSONResponse({"detail": f"Not found: {e}"}, status_code=404)


@app.exception_handler(ValueError)
async def bad_value(_req: Request, e: ValueError):
    return JSONResponse({"detail": str(e)}, status_code=422)


# ── the app's own facts ──────────────────────────────────────────────

@app.get("/api/health")
async def health():
    import pathlib
    import shutil
    ok = {"voice_model": (config.EV_HOME / "models/kokoro-v1.0.int8.onnx").exists(),
          "python": pathlib.Path(config.PY).exists(), "node": bool(shutil.which(config.NODE) or pathlib.Path(config.NODE).exists()),
          "ffmpeg": bool(shutil.which(config.FF) or pathlib.Path(config.FF).exists()),
          "browser": bool(config.CHROMIUM and pathlib.Path(config.CHROMIUM).exists()) or pathlib.Path(config.PW).exists()}
    return {"ok": all(ok.values()), "parts": ok}


@app.get("/api/options")
async def options():
    return {"voices": [{"id": v, "label": l} for v, l in store.VOICES], "resolutions": list(store.RESOLUTIONS),
            "outputs": store.OUTPUTS, "defaults": store.DEFAULTS, "deck": {k: list(v) for k, v in store.DECK.items()},
            "claude": draft.public_settings()}


class SettingsIn(BaseModel):
    anthropic_api_key: Optional[str] = None
    model: Optional[str] = None
    rounds: Optional[int] = None


@app.get("/api/settings")
async def get_settings():
    return draft.public_settings()


@app.put("/api/settings")
async def put_settings(body: SettingsIn):
    return draft.save_settings(body.model_dump(exclude_none=True))


# ── projects ─────────────────────────────────────────────────────────

class ProjectIn(BaseModel):
    title: str = Field("", max_length=120)
    brief: str = Field("", max_length=4000)


class ProjectPatch(BaseModel):
    title: Optional[str] = None
    brief: Optional[str] = None
    description: Optional[str] = None
    settings: Optional[dict] = None


@app.get("/api/projects")
async def list_projects():
    return store.listing()


@app.post("/api/projects")
async def new_project(body: ProjectIn):
    return store.create(body.title, body.brief)


@app.get("/api/projects/{pid}")
async def get_project(pid: str):
    d = store.read(pid)
    d["jobs"] = jobs.project_jobs(pid)
    return d


@app.patch("/api/projects/{pid}")
async def patch_project(pid: str, body: ProjectPatch):
    return store.update(pid, body.model_dump(exclude_none=True))


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    if any(j.pid == pid and j.state in ("queued", "running") for j in jobs.JOBS.values()):
        raise HTTPException(409, "A job is running for this project: cancel it first")
    store.delete(pid)
    return {"ok": True}


# ── the script ───────────────────────────────────────────────────────

class ScriptIn(BaseModel):
    text: str


@app.get("/api/projects/{pid}/script")
async def get_script(pid: str):
    p = store.root(pid)
    steps, sj, st = None, p / "scenes.js", p / "steps.json"
    if sj.exists() and st.exists() and st.stat().st_mtime >= sj.stat().st_mtime:
        steps = json.loads(st.read_text())   # the outline of the script as it stands
    return {"text": store.script(pid), "outline": steps}


@app.put("/api/projects/{pid}/script")
async def put_script(pid: str, body: ScriptIn):
    store.save_script(pid, body.text)
    return {"ok": True}


@app.post("/api/projects/{pid}/script/example")
async def use_example(pid: str):
    store.save_script(pid, store.example_script())
    return {"text": store.script(pid)}


@app.post("/api/projects/{pid}/import")
async def import_script(pid: str, files: list[UploadFile] = File(...)):
    """Your own script: a scenes.js (and optionally its meta.json), or a .zip
    holding them."""
    got = {}
    for f in files:
        data = await f.read()
        name = (f.filename or "").lower()
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for n in z.namelist():
                    base = n.rsplit("/", 1)[-1].lower()
                    if base in ("scenes.js", "meta.json"):
                        got[base] = z.read(n)
        elif name.endswith(".js"):
            got["scenes.js"] = data
        elif name.endswith(".json"):
            got["meta.json"] = data
    if "scenes.js" not in got:
        raise HTTPException(422, "No scenes.js in what was uploaded")
    store.save_script(pid, got["scenes.js"].decode("utf-8", errors="replace"))
    if "meta.json" in got:
        m = json.loads(got["meta.json"])
        patch: dict = {"settings": {}}
        if m.get("title"):
            patch["title"] = m["title"]
        if m.get("description"):
            patch["description"] = m["description"]
        for k in ("voice", "speed", "say"):
            if k in m:
                patch["settings"][k] = m[k]
        store.update(pid, patch)
    return store.read(pid)


# ── uploads ──────────────────────────────────────────────────────────

@app.post("/api/projects/{pid}/uploads")
async def upload(pid: str, files: list[UploadFile] = File(...)):
    names = [store.save_upload(pid, f.filename or "file", await f.read()) for f in files]
    return {"saved": names, "uploads": store.uploads(pid)}


@app.delete("/api/projects/{pid}/uploads/{name}")
async def delete_upload(pid: str, name: str):
    store.delete_upload(pid, name)
    return {"uploads": store.uploads(pid)}


# ── jobs ─────────────────────────────────────────────────────────────

class JobIn(BaseModel):
    kind: str
    outputs: Optional[list[str]] = None
    instruction: Optional[str] = Field(None, max_length=4000)
    rounds: Optional[int] = None


@app.post("/api/projects/{pid}/jobs")
async def new_job(pid: str, body: JobIn):
    opts = body.model_dump(exclude_none=True)
    opts.pop("kind")
    if body.kind == "render" and body.outputs is not None:
        opts["outputs"] = store.clean_settings({"outputs": body.outputs})["outputs"]
    return jobs.submit(body.kind, pid, opts).view()


@app.get("/api/jobs/{jid}")
async def get_job(jid: str):
    if jid not in jobs.JOBS:
        raise HTTPException(404, "No such job")
    return jobs.JOBS[jid].view()


@app.post("/api/jobs/{jid}/cancel")
async def cancel_job(jid: str):
    if jid not in jobs.JOBS:
        raise HTTPException(404, "No such job")
    return jobs.cancel(jid).view()


# ── files: previews, check frames, outputs ───────────────────────────

@app.get("/api/projects/{pid}/files/{rel:path}")
async def project_file(pid: str, rel: str, download: bool = False):
    f = store.file_in(pid, rel)
    # only the working folders, judged on the resolved path ("uploads/../x" is x)
    if f.relative_to(store.root(pid).resolve()).parts[0] not in ("out", "check", "outputs", "uploads"):
        raise HTTPException(404, "Not found")
    return FileResponse(f, filename=f.name if download else None,
                        headers={"Cache-Control": "no-store"})


@app.get("/api/projects/{pid}/outputs.zip")
async def outputs_zip(pid: str):
    d = store.root(pid) / "outputs"
    files = [f for f in sorted(d.iterdir()) if f.is_file()] if d.exists() else []
    if not files:
        raise HTTPException(404, "Nothing rendered yet")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for f in files:
            z.write(f, f.name)
    buf.seek(0)
    slug = store.slugify(store.read(pid)["title"])
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'})


# ── the page ─────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=config.UI), name="static")


@app.get("/fonts.css")
async def fonts():
    """The explainers' own typefaces, for the app too."""
    return FileResponse(config.KIT / "engine" / "fonts.css", media_type="text/css",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/", response_class=HTMLResponse)
@app.get("/p/{pid}", response_class=HTMLResponse)
async def index(pid: Optional[str] = None):
    return (config.UI / "index.html").read_text()
