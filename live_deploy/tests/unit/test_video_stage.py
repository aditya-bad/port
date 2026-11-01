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
def pwr():
    with pw.sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def page(pwr):
    s = V.build(sample(), dep=DEP, strategy_label="Monthly strangle", decisions=DECISIONS, now=NOW)
    narr = [[{"dur": 4.0 if (i, j) == (0, 0) else 1.0, "caps": [{"t": 0, "text": st["text"]}], "file": f"{i + 1:02d}-{j + 1:02d}"}
             for j, st in enumerate(sc["steps"])] for i, sc in enumerate(s["scenes"])]
    if True:
        p = pwr
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
            assert r["pi"] <= sc["data"]["pre"], "short of the trade, the positions before it"
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


def _open(p, s):
    narr = [[{"dur": 3.0, "caps": [{"t": 0, "text": st["text"]}], "file": f"{i + 1:02d}-{j + 1:02d}"}
             for j, st in enumerate(sc["steps"])] for i, sc in enumerate(s["scenes"])]
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 720})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script(f"window.__STORY = {json.dumps(s)}; window.__NARR = {json.dumps(narr)};")
    pg.goto(f"{STAGE.as_uri()}?record=1")
    pg.story, pg.errors = s, errors
    return b, pg


@pytest.fixture(scope="module")
def busy(pwr):
    from tests.unit.video_sample import at, busy_sample
    s = V.build(busy_sample(), dep=DEP, strategy_label="Monthly strangle", now=at(2, 15, 25))
    b, pg = _open(pwr, s)
    yield pg
    b.close()


def seek(pg, k, tau):
    pg.evaluate("([s, t]) => window.__seek(s, t)", [k, tau])


def scene(pg, pred):
    return 1 + next(i for i, sc in enumerate(pg.story["scenes"]) if pred(sc))


SHOWN = """() => {
  const S = STORY.series, when = document.querySelector('#layer .pwhen').textContent;
  const pi = S.t.findIndex(t => `${dayOf(t)} ${hm(t)}` === when);
  const box = [...document.querySelectorAll('#layer .pnlchip text')].filter(e => /pnlv/.test(e.getAttribute('class'))).map(e => e.textContent);
  const spark = (document.querySelector('#layer .spark') || { getAttribute: () => '' }).getAttribute('d') || '';
  const bars = [...document.querySelectorAll('#layer .prow')].filter(%s).map(r => ({
    w: +r.querySelector('.pbar').getAttribute('width'), cls: r.querySelector('.pbar').getAttribute('class'),
    pnl: r.querySelector('.pp').textContent }));
  return { pi, box, boxExp: [sinr(S.booked[pi]), sinr(S.pnl[pi] - S.booked[pi]), sinr(S.pnl[pi])], pts: (spark.match(/[ML]/g) || []).length, bars };
}""" % VISIBLE


def test_the_market_plays_from_the_trade_before_across_every_step_that_tells_it(page):
    """The 11:20 checkpoint: the stretch since the 15:13 check is told in
    two steps (the move, then the options and the P&L), and the playhead
    takes both to get there: it starts at the check, is part way at the
    end of the first, and reaches the step before the trade at the second."""
    page.evaluate("window.__ready")
    k = scene(page, lambda sc: sc["title"] == "11:20 · Checkpoint hit")
    d = page.story["scenes"][k - 1]["data"]
    plan = page.evaluate("n => window.__plan(n)", k)
    assert [st["cue"] for st in page.story["scenes"][k - 1]["steps"]][:2] == ["move", "walk"]
    seek(page, k, 0.02)
    start = page.evaluate(SHOWN)["pi"]
    st0, st1 = plan["steps"][0], plan["steps"][1]
    seek(page, k, st0["T0"] + st0["D"] - 0.05)
    mid = page.evaluate(SHOWN)["pi"]
    seek(page, k, st1["T0"] + st1["D"] * 0.3)
    later_ = page.evaluate(SHOWN)["pi"]
    assert start == d["from"] and start < mid < later_ < d["at"], (start, mid, later_, d["from"], d["at"])
    seek(page, k, st1["T0"] + st1["D"] - 0.05)
    assert page.evaluate("() => document.querySelector('#layer .pnlchip text').textContent") == "P&L at 11:20", "there by the end of the second"


def test_the_pnl_line_and_each_positions_bar_move_with_the_market(page):
    page.evaluate("window.__ready")
    k = scene(page, lambda sc: sc["title"] == "15:13 · End-of-day check")
    plan = page.evaluate("n => window.__plan(n)", k)
    seen = []
    for j, f in ((0, 0.3), (0, 0.8), (1, 0.3)):
        st = plan["steps"][j]
        seek(page, k, st["T0"] + st["D"] * f)
        r = page.evaluate(SHOWN)
        assert r["box"] == r["boxExp"], r
        for b in r["bars"]:
            sign = "good" if b["pnl"].startswith("+") else "bad" if b["pnl"].startswith("−") else ""
            assert sign in b["cls"] and (b["w"] > 0) == bool(sign), b
        seen.append(r)
    assert seen[0]["pts"] < seen[1]["pts"] < seen[2]["pts"], "the P&L line draws on"
    assert len({tuple(b["w"] for b in r["bars"]) for r in seen}) == 3, "and the bars grow and shrink"


def test_a_run_plays_each_trade_as_a_beat_with_the_desk_moving(busy):
    busy.evaluate("window.__ready")
    k = scene(busy, lambda sc: sc["kind"] == "run")
    d = busy.story["scenes"][k - 1]["data"]
    plan = busy.evaluate("n => window.__plan(n)", k)
    for j, (g, st) in enumerate(zip(d["segs"], plan["steps"])):
        seek(busy, k, st["T0"] + st["D"] - 0.05)
        r = busy.evaluate(SHOWN)
        assert r["pi"] == g["to"] and r["box"] == r["boxExp"], (j, r)
        reached = [b for b in d["beats"] if b["at"] <= g["to"]]
        counter = busy.evaluate("() => [...document.querySelectorAll('#layer .blog text')].map(e => e.textContent)")
        assert f"{len(reached)} of {len(d['beats'])}" in counter
        newest = busy.evaluate("() => document.querySelector('#layer .bline2.now').textContent")
        assert newest.startswith(busy.evaluate("t => hm(t)", reached[-1]["t"])), newest
        marks = busy.evaluate("() => [...document.querySelectorAll('#layer .beatmk')].filter(e => +e.getAttribute('opacity') > 0).length")
        assert marks == len(reached)
    assert busy.errors == []


def test_every_scene_of_a_busy_cycle_draws_without_errors(busy):
    n = busy.evaluate("window.__ready")
    for k in range(1, n + 1):
        plan = busy.evaluate("n => window.__plan(n)", k)
        for st in plan["steps"]:
            seek(busy, k, st["T0"] + st["D"] - 0.05)
    assert busy.errors == []


def test_more_legs_than_rows_are_shown_by_side(page):
    """Seven legs in a five-row table: a side's legs as one row (how many,
    sold for in all, now, their P&L), not five rows and '+2 more'."""
    page.evaluate("window.__ready")
    rows = page.evaluate("""() => {
      const g = G(document.querySelector('#layer')), t = positions(g, 980, 330, 560, 5);
      const leg = (cls, entry, mark, pnl) => ({ what: `${cls}`, sold: true, entry, mark, pnl, cls });
      t.set([leg('ce', 100, 120, -1500), leg('pe', 90, 60, 2250), leg('pe', 80, 50, 2250), leg('pe', 70, 40, 2250), leg('pe', 60, 30, 2250),
             leg('ce', 110, 100, 750), leg('ce', 50, 40, 750)]);
      const out = [...g.querySelectorAll('.prow')].filter(e => +e.getAttribute('opacity') > 0).map(e => e.textContent);
      g.remove();
      return out;
    }""")
    assert len(rows) == 2
    calls = next(r for r in rows if "3 calls" in r)
    puts = next(r for r in rows if "4 puts" in r)
    assert "Short 3 calls" in calls and "sold ₹260 in all → now ₹260" in calls and "₹0" in calls
    assert "Short 4 puts" in puts and "sold ₹300 in all → now ₹180" in puts and "+₹9,000" in puts


def test_the_journeys_booked_line_fits_on_its_chart(busy):
    """Ten rolls booking ₹33,879 while the total fell: the booked line was
    drawn off the top of a chart scaled to the total alone."""
    busy.evaluate("window.__ready")
    k = scene(busy, lambda sc: sc["kind"] == "journey")
    plan = busy.evaluate("n => window.__plan(n)", k)
    seek(busy, k, plan["total"] - 0.1)
    ys = busy.evaluate("""() => {
      const d = document.querySelector('#layer .ln-booked').getAttribute('d');
      return d.match(/[ML][\\d.]+ ([\\d.-]+)/g).map(x => +x.split(' ')[1]);
    }""")
    assert ys and min(ys) >= 200 - 1 and max(ys) <= 630 + 1, (min(ys), max(ys))


def test_the_walk_ends_on_the_prices_it_says(busy):
    """The 10:40 roll is at a step's end, so the step before it already
    held the new put: the picture stopped on ₹98.86 while the voice said
    the put went 'to ₹176.39'. The walk now ends on the last step that
    still shows the positions the trade changes, the one it describes."""
    busy.evaluate("window.__ready")
    k = scene(busy, lambda sc: sc["kind"] == "trade" and sc["title"] == "10:40 · Roll")
    sc = busy.story["scenes"][k - 1]
    d, S = sc["data"], busy.story["series"]
    plan = busy.evaluate("n => window.__plan(n)", k)
    st1 = plan["steps"][1]
    last = None
    for q in range(1, 40):   # the last moment before the playhead reaches the trade
        seek(busy, k, st1["T0"] + st1["D"] * q / 40)
        if busy.evaluate("() => document.querySelector('#layer .pnlchip text').textContent") != "P&L":
            break
        last = busy.evaluate(SHOWN)
        heads = busy.evaluate("() => [...document.querySelectorAll('#layer .phead')].filter(%s).map(e => e.textContent).sort()" % VISIBLE)
        rows = busy.evaluate("() => [...document.querySelectorAll('#layer .prow')].filter(%s).map(e => e.textContent)" % VISIBLE)
    assert last and last["pi"] == d["pre"]
    shut = next(f["what"] for f in d["fills"] if not f["opening"])
    new = next(f["what"] for f in d["fills"] if f["opening"])
    assert any(shut in r for r in rows) and not any(new in r for r in rows), ("the positions before the roll", rows)
    walk = sc["steps"][1]["text"]
    for h in heads:
        assert f"to {h}" in walk, (h, walk)
