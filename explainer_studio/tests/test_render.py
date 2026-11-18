"""The whole pipeline on the bundled two-scene example: voice, check,
recording, and every kind of file. Needs the voice model, Node, a browser
and ffmpeg (the Docker image has them all); skipped where they're missing."""
import json
import subprocess
import time

import pytest

from tests.conftest import can_render

pytestmark = pytest.mark.skipif(not can_render(), reason="needs the voice model, Node, a browser and ffmpeg")


def wait(client, jid, limit=900):
    t0 = time.time()
    while time.time() - t0 < limit:
        v = client.get(f"/api/jobs/{jid}").json()
        if v["state"] not in ("queued", "running"):
            return v
        time.sleep(1)
    raise AssertionError("the job took too long")


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


def test_a_vertical_youtube_file_with_everything(client):
    from app import config
    pid = client.post("/api/projects", json={"title": "How the example works", "brief": "demo"}).json()["id"]
    client.post(f"/api/projects/{pid}/script/example")
    # check first: voice, page, every scene played through
    c = wait(client, client.post(f"/api/projects/{pid}/jobs", json={"kind": "check"}).json()["id"])
    assert c["state"] == "done", c["error"]
    assert c["result"]["ok"] and c["result"]["frames"], "frames to look at"
    client.patch(f"/api/projects/{pid}", json={"settings": {
        "resolution": "vertical", "fps": 25, "theme": "dark", "burn_captions": True, "intro": True, "outro": True,
        "outputs": ["html", "mp4", "scenes", "youtube", "srt", "vtt", "chapters", "thumbnail"]}})
    r = wait(client, client.post(f"/api/projects/{pid}/jobs", json={"kind": "render"}).json()["id"])
    assert r["state"] == "done", (r["error"], r["log"][-20:])
    out = config.PROJECTS / pid / "outputs"
    names = sorted(f.name for f in out.iterdir())
    assert names == sorted([
        "how-the-example-works.html", "how-the-example-works.mp4", "how-the-example-works.srt", "how-the-example-works.vtt",
        "how-the-example-works-youtube.mp4", "how-the-example-works-youtube.srt", "how-the-example-works-chapters.txt",
        "how-the-example-works-thumbnail.jpg",
        "how-the-example-works-scene-01-a-message-from-your-phone-to-the-server.mp4",
        "how-the-example-works-scene-02-two-copies-one-stamp.mp4"])
    yt = probe(out / "how-the-example-works-youtube.mp4")
    assert (yt["video"]["codec_name"], yt["video"]["width"], yt["video"]["height"], yt["video"]["r_frame_rate"]) == ("h264", 1080, 1920, "25/1")
    assert (yt["audio"]["codec_name"], yt["audio"]["sample_rate"], yt["audio"]["channels"]) == ("aac", "48000", 2)
    plain = probe(out / "how-the-example-works.mp4")
    assert (plain["video"]["width"], plain["video"]["height"]) == (1920, 1080), "16:9 at 1080p besides the vertical file"
    srt = (out / "how-the-example-works.srt").read_text()
    ysrt = (out / "how-the-example-works-youtube.srt").read_text()
    first = srt.split("\n")[1].split(" --> ")[0]
    yfirst = ysrt.split("\n")[1].split(" --> ")[0]
    assert first < "00:00:03" <= yfirst, "the YouTube subtitles start after the 3-second title card"
    assert "Here is your phone" in srt and (out / "how-the-example-works.vtt").read_text().startswith("WEBVTT")
    assert (out / "how-the-example-works-chapters.txt").read_text().startswith("0:00 A message, from your phone to the server")
    assert client.get(f"/api/projects/{pid}/outputs.zip").headers["content-type"] == "application/zip"


def test_a_powerpoint_with_sound_a_slide_per_step(client):
    """The deck job: recorded at its own size, a slide per step plus a title
    slide, the voice in each clip, playing by itself and moving on; the video
    files made before are kept."""
    import zipfile
    from app import config
    pid = client.post("/api/projects", json={"title": "How the example works", "brief": "demo"}).json()["id"]
    client.post(f"/api/projects/{pid}/script/example")
    out = config.PROJECTS / pid / "outputs"
    out.mkdir(exist_ok=True)
    (out / "how-the-example-works.mp4").write_bytes(b"made earlier")
    client.patch(f"/api/projects/{pid}", json={"settings": {"deck": {
        "sound": True, "advance": True, "resolution": "720p", "captions": "text", "per": "step"}}})
    r = wait(client, client.post(f"/api/projects/{pid}/jobs", json={"kind": "deck"}).json()["id"])
    assert r["state"] == "done", (r["error"], r["log"][-20:])
    assert (out / "how-the-example-works.mp4").read_bytes() == b"made earlier", "the video stays"
    steps = json.loads((config.PROJECTS / pid / "steps.json").read_text())
    n = sum(len(s["steps"]) for s in steps)
    z = zipfile.ZipFile(out / "how-the-example-works.pptx")
    slides = [x for x in z.namelist() if x.startswith("ppt/slides/slide") and x.endswith(".xml")]
    assert len(slides) == n + 1 == r["result"]["slides"]
    assert not [x for x in z.namelist() if x.endswith("/")], "no folder entries"
    import re
    for name in slides:
        x = z.read(name).decode()
        ids = re.findall(r'<p:cNvPr id="(\d+)" name="([^"]*)"', x)
        assert len({i for i, _ in ids}) == len(ids), f"{name}: two shapes share an id"
        targets = set(re.findall(r'spTgt spid="(\d+)"', x))
        assert [nm for i, nm in ids if i in targets] == ([] if name.endswith("slide1.xml") else [next(nm for _, nm in ids if nm.endswith("animation"))]), \
            f"{name}: the timing plays the animation, not the picture under it"
    two = z.read("ppt/slides/slide2.xml").decode()
    assert "<p:timing>" in two and "mute=" not in two and "advTm=" in two and 'evt="onBegin"' in two
    assert "Here is your phone" in two
    notes = [x for x in z.namelist() if x.startswith("ppt/notesSlides/") and x.endswith(".xml")]
    assert any("Here is your phone" in z.read(x).decode() for x in notes)
    clip = config.PROJECTS / pid / "out" / "deck" / "001.mp4"
    c = probe(clip)
    assert (c["video"]["width"], c["video"]["height"]) == (1280, 720) and "audio" in c


def test_one_continuous_video(client):
    """per = whole: the explainer on one slide after the title, as long as the
    scenes together, with the scenes' sentences in the notes."""
    import zipfile
    from app import config
    pid = client.post("/api/projects", json={"title": "How the example works", "brief": "demo"}).json()["id"]
    client.post(f"/api/projects/{pid}/script/example")
    client.patch(f"/api/projects/{pid}", json={"settings": {"deck": {"per": "whole", "resolution": "720p", "start": "click"}}})
    r = wait(client, client.post(f"/api/projects/{pid}/jobs", json={"kind": "deck"}).json()["id"])
    assert r["state"] == "done", (r["error"], r["log"][-20:])
    z = zipfile.ZipFile(config.PROJECTS / pid / "outputs" / "how-the-example-works.pptx")
    assert sorted(x for x in z.namelist() if x.startswith("ppt/slides/slide") and x.endswith(".xml")) == \
        ["ppt/slides/slide1.xml", "ppt/slides/slide2.xml"]
    two = z.read("ppt/slides/slide2.xml").decode()
    assert 'name="Explainer animation"' in two and "clickEffect" in two and 'mute="1"' in two
    plans = json.loads((config.PROJECTS / pid / "out" / "plans.json").read_text())
    whole = config.PROJECTS / pid / "out" / "deck" / "whole.mp4"
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(whole)],
                               capture_output=True, text=True, check=True).stdout)
    assert abs(dur - sum(float(p["total"]) for p in plans)) < 1.0
    notes = "".join(z.read(x).decode() for x in z.namelist() if x.startswith("ppt/notesSlides/") and x.endswith(".xml"))
    assert "Here is your phone" in notes and "Two copies" in notes
