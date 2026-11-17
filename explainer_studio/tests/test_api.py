"""The API: projects, settings, uploads, the script, files."""
import io
import json
import zipfile


def test_a_project_from_start_to_settings(client):
    assert client.get("/api/projects").json() == []
    p = client.post("/api/projects", json={"title": "  How X works ", "brief": "for beginners"}).json()
    assert p["title"] == "How X works" and p["settings"]["resolution"] == "1080p" and not p["has_script"]
    pid = p["id"]
    assert [x["id"] for x in client.get("/api/projects").json()] == [pid]
    r = client.patch(f"/api/projects/{pid}", json={"settings": {"resolution": "vertical", "fps": 60, "voice": "bm_george",
                                                                "outputs": ["youtube", "srt", "nonsense"], "say": [["P&L", "P and L"]]}})
    s = r.json()["settings"]
    assert (s["resolution"], s["fps"], s["voice"], s["outputs"]) == ("vertical", 60, "bm_george", ["youtube", "srt"])
    from app import config
    meta = json.loads((config.PROJECTS / pid / "meta.json").read_text())
    assert meta == {"title": "How X works", "slug": "how-x-works", "description": "for beginners", "voice": "bm_george",
                    "speed": 0.95, "say": [["P&L", "P and L"]]}
    for bad in ({"fps": 24}, {"resolution": "8k"}, {"voice": "nobody"}, {"outputs": []}, {"speed": 3}):
        assert client.patch(f"/api/projects/{pid}", json={"settings": bad}).status_code == 422, bad
    assert client.patch(f"/api/projects/{pid}", json={"title": ""}).status_code == 422
    assert client.delete(f"/api/projects/{pid}").json() == {"ok": True}
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_uploads_and_files_stay_inside_the_project(client):
    pid = client.post("/api/projects", json={"title": "T"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/uploads", files=[("files", ("../../etc/notes.md", b"# hi", "text/markdown"))])
    assert r.json()["saved"] == ["notes.md"]
    assert client.get(f"/api/projects/{pid}/files/uploads/notes.md").content == b"# hi"
    assert client.get(f"/api/projects/{pid}/files/../project.json").status_code == 404
    assert client.get(f"/api/projects/{pid}/files/uploads/..%2Fproject.json").status_code == 404
    assert client.get(f"/api/projects/{pid}/files/meta.json").status_code == 404, "only the working folders"
    assert client.delete(f"/api/projects/{pid}/uploads/notes.md").json()["uploads"] == []
    assert client.get("/api/projects/nothere123/files/out/x").status_code == 404
    assert client.get("/api/projects/../../x").status_code == 404


def test_the_script_by_hand_by_example_and_imported(client):
    pid = client.post("/api/projects", json={"title": "T"}).json()["id"]
    assert client.get(f"/api/projects/{pid}/script").json() == {"text": "", "outline": None}
    client.put(f"/api/projects/{pid}/script", json={"text": "scene({});"})
    assert client.get(f"/api/projects/{pid}/script").json()["text"] == "scene({});"
    ex = client.post(f"/api/projects/{pid}/script/example").json()["text"]
    assert "scene({" in ex
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mine/scenes.js", "// mine\n")
        z.writestr("mine/meta.json", json.dumps({"title": "Mine", "voice": "am_adam", "say": [["SQL", "sequel"]]}))
    r = client.post(f"/api/projects/{pid}/import", files=[("files", ("x.zip", buf.getvalue(), "application/zip"))]).json()
    assert r["title"] == "Mine" and r["settings"]["voice"] == "am_adam" and r["settings"]["say"] == [["SQL", "sequel"]]
    assert client.get(f"/api/projects/{pid}/script").json()["text"] == "// mine\n"
    assert client.post(f"/api/projects/{pid}/import", files=[("files", ("a.txt", b"x", "text/plain"))]).status_code == 422


def test_claude_settings_never_show_the_key(client):
    s = client.get("/api/settings").json()
    assert s["has_key"] is False and s["model"] == "claude-opus-5-5"
    s = client.put("/api/settings", json={"anthropic_api_key": "sk-ant-test", "rounds": 9}).json()
    assert s["has_key"] is True and s["rounds"] == 5 and "sk-ant" not in json.dumps(s)
    assert client.put("/api/settings", json={"model": "gpt"}).status_code == 422
    s = client.put("/api/settings", json={"anthropic_api_key": " "}).json()
    assert s["has_key"] is False


def test_a_job_needs_a_script_and_one_at_a_time(client):
    import time
    pid = client.post("/api/projects", json={"title": "T"}).json()["id"]
    j = client.post(f"/api/projects/{pid}/jobs", json={"kind": "preview"}).json()
    for _ in range(50):
        v = client.get(f"/api/jobs/{j['id']}").json()
        if v["state"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert v["state"] == "failed" and "no script yet" in v["error"]
    assert client.post(f"/api/projects/{pid}/jobs", json={"kind": "nope"}).status_code == 422
    r = client.post(f"/api/projects/{pid}/jobs", json={"kind": "draft"}).json()
    for _ in range(50):
        v = client.get(f"/api/jobs/{r['id']}").json()
        if v["state"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert v["state"] == "failed" and "No Claude API key" in v["error"]
