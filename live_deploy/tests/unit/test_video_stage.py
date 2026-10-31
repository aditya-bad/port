"""The video's stage (static/linck/video/): every scene of a story drawn
in a real browser at the end of each step, with no page errors, and the
numbers on screen the story's own; the clock stretches each step to its
voice. Drawn the way app/video/render.py records it (from disk, the
story handed in)."""

import json

import pytest

from app.video import story as V
from app.video.render import STAGE
from tests.unit.video_sample import DECISIONS, DEP, NOW, sample

pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def page():
    s = V.build(sample(), dep=DEP, strategy_label="Monthly strangle", decisions=DECISIONS, now=NOW)
    narr = [[{"dur": 4.0 if (i, j) == (0, 0) else 1.0, "caps": [{"t": 0, "text": st["text"]}], "file": f"{i + 1:02d}-{j + 1:02d}"}
             for j, st in enumerate(sc["steps"])] for i, sc in enumerate(s["scenes"])]
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 720})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script(f"window.__STORY = {json.dumps(s)}; window.__NARR = {json.dumps(narr)};")
        pg.goto(f"{STAGE.as_uri()}?record=1")
        pg.story, pg.errors = s, errors
        yield pg
        b.close()


def test_every_scene_draws_every_step_without_errors(page):
    n = page.evaluate("window.__ready")
    assert n == len(page.story["scenes"])
    for k in range(1, n + 1):
        plan = page.evaluate("n => window.__plan(n)", k)
        for st in plan["steps"]:
            page.evaluate("([s, t]) => window.__seek(s, t)", [k, st["T0"] + st["D"] - 0.05])
        assert page.locator("#hud .ttl").text_content() == page.story["scenes"][k - 1]["title"]
    assert page.errors == []


def test_the_picture_shows_the_storys_numbers(page):
    page.evaluate("window.__ready")
    plan = page.evaluate("n => window.__plan(n)", 1)
    page.evaluate("([s, t]) => window.__seek(s, t)", [1, plan["total"] - 0.1])
    shown = page.locator("#layer").text_content()
    total = next(sc["data"]["total"] for sc in page.story["scenes"] if sc["kind"] == "open")
    assert f"+₹{round(total):,}" in shown and "booked +₹9,412" in shown       # rounded as the words are
    roll = 1 + next(i for i, sc in enumerate(page.story["scenes"]) if sc["title"] == "13:40 · Roll")
    plan = page.evaluate("n => window.__plan(n)", roll)
    page.evaluate("([s, t]) => window.__seek(s, t)", [roll, plan["total"] - 0.1])
    shown = page.locator("#layer").text_content()
    assert "BUY BACK" in shown and "24,500 put" in shown and "booked +₹4,388" in shown and "50% line" in shown
    assert page.locator("#cap").text_content().startswith("After the roll")


def test_each_step_lasts_as_long_as_its_voice(page):
    page.evaluate("window.__ready")
    plan = page.evaluate("n => window.__plan(n)", 1)
    first = plan["steps"][0]
    assert first["D"] >= 4.0 + 0.6 - 1e-6 and first["dur"] == 4.0
    assert plan["steps"][1]["T0"] == pytest.approx(first["D"])
