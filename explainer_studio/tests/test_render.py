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
