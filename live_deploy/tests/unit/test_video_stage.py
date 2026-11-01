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
    assert "24,500 put" in shown and "closed ₹61" in shown and "booked on this trade +₹4,388" in shown and "50% line" in shown
    assert page.locator("#cap").text_content().startswith("After the roll")


def test_each_step_lasts_as_long_as_its_voice(page):
    page.evaluate("window.__ready")
    plan = page.evaluate("n => window.__plan(n)", 1)
    first = plan["steps"][0]
    assert first["D"] >= 4.0 + 0.6 - 1e-6 and first["dur"] == 4.0
    assert plan["steps"][1]["T0"] == pytest.approx(first["D"])


def test_a_trade_is_shown_at_its_moment_while_it_is_explained(page):
    """The chart stops at the trade and stays there while the rest is
    said: the P&L box shows the P&L at that moment (the number said), not
    one from hours later (a video once said 'booked ₹750' over a chart
    already at the next morning, its box showing −₹1k)."""
    page.evaluate("window.__ready")
    for k, sc in enumerate(page.story["scenes"], 1):
        if sc["kind"] != "trade":
            continue
        d = sc["data"]
        hm = d["t"][11:16]
        plan = page.evaluate("n => window.__plan(n)", k)
        for st in plan["steps"][1:]:
            page.evaluate("([s, t]) => window.__seek(s, t)", [k, st["T0"] + st["D"] - 0.05])
            box = page.locator("#layer .pnlchip").text_content()
            assert f"P&L at {hm}" in box, (sc["title"], box)
            assert f"₹{round(abs(d['pnl'])):,}" in box, (sc["title"], box)
        if d["booked"] is not None:
            assert f"booked on this trade +₹{round(d['booked']):,}" in page.locator("#layer .pnlchip").text_content(), sc["title"]


VISIBLE = "e => { for (let n = e; n && n.getAttribute; n = n.parentNode) { const o = n.getAttribute('opacity'); if (o != null && parseFloat(o) <= 0) return false; } return true; }"

# what the stage shows during a trade's walk, and what the series says at
# the step its clock reads: worked out in the page, with the page's own formats
AT_STEP = """() => {
  const vis = %s;
  const S = STORY.series, L = STORY.legs, q = s => [...document.querySelectorAll(s)];
  const when = document.querySelector('#layer .pwhen').textContent;
  const pi = S.t.findIndex(t => `${dayOf(t)} ${hm(t)}` === when);
  const box = q('#layer .pnlchip text').filter(e => /pnlv/.test(e.getAttribute('class'))).map(e => e.textContent);
  const held = S.held[pi] || [];
  return {
    pi, when,
    ix: document.querySelector('#layer .ixv').textContent, ixExp: `${STORY.index} ${fmt(S.idx[pi])}`,
    heads: q('#layer .phead').filter(vis).map(e => e.textContent).sort(),
    headsExp: held.filter(([j]) => isOpt(L[j])).map(([, m]) => px(m)).sort(),
    box, boxExp: [sinr(S.booked[pi]), sinr(S.pnl[pi] - S.booked[pi]), sinr(S.pnl[pi])],
    rows: q('#layer .prow').filter(vis).map(e => e.textContent),
    rowsExp: held.map(([j, m]) => L[j].what),
  };
}""" % VISIBLE


def test_on_the_walk_the_index_the_options_and_the_pnl_are_one_moment(page):
    """The index moving while the option prices stood still, then the
    prices moving once the move was said: now the index, each option's own
    price, the positions and the P&L (booked, open, total) are all read
    from the step the playhead is on, all the way to the trade."""
    page.evaluate("window.__ready")
    walked = 0
    for k, sc in enumerate(page.story["scenes"], 1):
        if sc["kind"] != "trade" or sc["data"]["at"] - sc["data"]["w"][0] < 4:
            continue
        plan = page.evaluate("n => window.__plan(n)", k)
        st0 = plan["steps"][0]
        seen = []
        for f in (0.15, 0.45, 0.75):
            page.evaluate("([s, t]) => window.__seek(s, t)", [k, st0["T0"] + st0["D"] * f])
            r = page.evaluate(AT_STEP)
            assert r["pi"] >= 0, r["when"]
            assert r["ix"] == r["ixExp"], (sc["title"], r)
            assert r["heads"] == r["headsExp"], (sc["title"], r)
            assert r["box"] == r["boxExp"], (sc["title"], r)
            assert len(r["rows"]) == len(r["rowsExp"]) and all(w in row for w, row in zip(r["rowsExp"], r["rows"])), (sc["title"], r)
            seen.append(r)
        assert len({r["pi"] for r in seen}) == 3, "the playhead moves"
        assert len({r["ix"] for r in seen}) > 1, "and the index with it"
        if all(r["heads"] for r in seen):   # options held on the way (not before an entry)
            assert len({tuple(r["heads"]) for r in seen}) > 1, "and the options with it"
            walked += 1
    assert walked >= 2


def test_at_the_trade_a_close_is_booked_and_the_new_leg_comes_in(page):
    """At the roll: the 24,500 put's row turns to closed at ₹61, booked,
    and the box moves what it booked from open to booked; the 24,800 put
    comes in as new; the total doesn't change (a close books, it doesn't earn)."""
    page.evaluate("window.__ready")
    k = 1 + next(i for i, sc in enumerate(page.story["scenes"]) if sc["title"] == "13:40 · Roll")
    d = page.story["scenes"][k - 1]["data"]
    plan = page.evaluate("n => window.__plan(n)", k)
    cues = [st["cue"] for st in page.story["scenes"][k - 1]["steps"]]
    rows = lambda: page.evaluate("() => [...document.querySelectorAll('#layer .prow')].filter(%s).map(e => e.textContent)" % VISIBLE)  # noqa: E731
    box = lambda: page.evaluate("() => [...document.querySelectorAll('#layer .pnlchip text')].filter(e => /pnlv/.test(e.getAttribute('class'))).map(e => e.textContent)")  # noqa: E731
    fmt = lambda v: page.evaluate("v => sinr(v)", v)  # noqa: E731
    before = plan["steps"][cues.index("ratio")]
    page.evaluate("([s, t]) => window.__seek(s, t)", [k, before["T0"] + before["D"] - 0.05])
    assert not any("booked" in r for r in rows()) and not any("new" in r for r in rows())
    assert box() == [fmt(d["booked_before"]), fmt(d["pnl"] - d["booked_before"]), fmt(d["pnl"])]
    page.evaluate("([s, t]) => window.__seek(s, t)", [k, plan["total"] - 0.1])
    after = rows()
    assert any("24,500 put" in r and "closed ₹61" in r and "booked" in r for r in after), after
    assert any("24,800 put" in r and "just now at ₹118" in r and "new" in r for r in after), after
    assert box() == [fmt(d["booked_after"]), fmt(d["pnl"] - d["booked_after"]), fmt(d["pnl"])]


def test_the_market_draws_each_options_price_under_the_index(page):
    page.evaluate("window.__ready")
    k = 1 + next(i for i, sc in enumerate(page.story["scenes"]) if sc["kind"] == "market")
    plan = page.evaluate("n => window.__plan(n)", k)
    page.evaluate("([s, t]) => window.__seek(s, t)", [k, plan["total"] - 0.1])
    opts = [l for l in page.story["legs"] if l["kind"] in ("CE", "PE")]
    paths = page.evaluate("() => [...document.querySelectorAll('#layer .pleg path')].map(e => e.getAttribute('d'))")
    assert len(paths) == len(opts) and all(p and p.count("L") >= 1 for p in paths)


def test_the_pnl_journey_and_the_end_say_what_is_booked_and_what_is_open(page):
    page.evaluate("window.__ready")
    S = page.story["series"]
    fmt = lambda v: page.evaluate("v => sinr(v)", v)  # noqa: E731
    k = 1 + next(i for i, sc in enumerate(page.story["scenes"]) if sc["kind"] == "journey")
    plan = page.evaluate("n => window.__plan(n)", k)
    page.evaluate("([s, t]) => window.__seek(s, t)", [k, plan["total"] - 0.1])
    shown = page.evaluate("() => [...document.querySelectorAll('#layer .readout .bigs')].map(e => e.textContent)")
    assert shown == [fmt(S["booked"][-1]), fmt(S["pnl"][-1] - S["booked"][-1]), fmt(S["pnl"][-1])]
    k = len(page.story["scenes"])
    w = page.story["scenes"][-1]["data"]
    plan = page.evaluate("n => window.__plan(n)", k)
    page.evaluate("([s, t]) => window.__seek(s, t)", [k, plan["total"] - 0.1])
    text = page.locator("#layer").text_content()
    assert f"booked {fmt(w['booked'])}" in text and f"open {fmt(w['open'])}" in text
    for x in w["trades"]:
        if x["booked"] is not None:
            assert f"{x['title']}  {fmt(x['booked'])}" in text or f"{x['title']} {fmt(x['booked'])}" in text, x
