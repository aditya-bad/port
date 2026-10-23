"""Shadow mode's checks (app/shadow.py): what a LIMIT order for a paper
fill would have met at Kite."""

import pytest

from app import shadow
from app.shadow import assess, check_band, check_freeze, check_lot, follow, limit_for, parse_freeze_csv, walk


def book(bids=(), asks=()):
    return {"buy": [{"price": p, "quantity": q, "orders": 1} for p, q in bids],
            "sell": [{"price": p, "quantity": q, "orders": 1} for p, q in asks]}


# ── the limit ───────────────────────────────────────────────────────────

def test_the_limit_is_the_price_plus_the_buffer_on_the_price_step():
    assert limit_for("buy", 100.0, 0.05, 0.5) == 100.5
    assert limit_for("sell", 100.0, 0.05, 0.5) == 99.5
    # never past the buffer: a buy rounds down, a sell up
    assert limit_for("buy", 33.35, 0.05, 0.5) == 33.5      # 33.51675 -> 33.50
    assert limit_for("sell", 33.35, 0.05, 0.5) == 33.2     # 33.18325 -> 33.20


def test_a_cheap_option_still_gets_a_valid_limit():
    assert limit_for("buy", 0.05, 0.05, 0.5) == 0.05
    assert limit_for("sell", 0.05, 0.05, 0.5) == 0.05, "never below one price step"


# ── each rule ───────────────────────────────────────────────────────────

def test_lot_size():
    assert check_lot(150, 75)["status"] == "ok"
    r = check_lot(100, 75)
    assert r["status"] == "reject" and "whole lots" in r["detail"]
    assert check_lot(75, None)["status"] == "unknown"


def test_freeze_limit_splits_an_order_into_several():
    c, n = check_freeze(1800, 1800)
    assert (c["status"], n) == ("ok", 1)
    c, n = check_freeze(4000, 1800, "NSE file of 23 Oct")
    assert (c["status"], n) == ("warn", 3)
    assert "3 orders" in c["detail"] and "NSE file of 23 Oct" in c["detail"]
    c, n = check_freeze(4000, None)
    assert (c["status"], n) == ("unknown", None)


def test_price_band_caps_the_limit_and_refuses_a_price_outside_it():
    c, lim = check_band("buy", 100.0, 100.5, 50.0, 100.2)
    assert c["status"] == "warn" and lim == 100.2
    c, lim = check_band("sell", 51.0, 50.7, 50.8, 150.0)
    assert c["status"] == "warn" and lim == 50.8
    c, lim = check_band("buy", 100.0, 100.5, 50.0, 150.0)
    assert c["status"] == "ok" and lim == 100.5
    assert check_band("buy", 160.0, 160.8, 50.0, 150.0)[0]["status"] == "reject"
    assert check_band("buy", 100.0, 100.5, None, None)[0]["status"] == "unknown"


def test_walking_the_book_stops_at_the_limit():
    b = book(bids=[(99.9, 50), (99.6, 100), (99.0, 500)], asks=[(100.1, 40), (100.4, 60), (101.0, 500)])
    # a sell of 150 with limit 99.5: 50 @ 99.9 + 100 @ 99.6
    got, avg, best = walk(b, "sell", 150, 99.5)
    assert got == 150 and avg == pytest.approx((50 * 99.9 + 100 * 99.6) / 150) and best == 99.9
    # a buy of 200 with limit 100.5: only 100 at or under it
    got, avg, best = walk(b, "buy", 200, 100.5)
    assert got == 100 and avg == pytest.approx((40 * 100.1 + 60 * 100.4) / 100)
    assert walk(book(), "buy", 10, 100) == (None, None, None)


# ── the verdict ─────────────────────────────────────────────────────────

FULL = dict(lot=75, tick=0.05, freeze=1800, band=(50.0, 150.0))


def test_fills_at_about_the_paper_price():
    r = assess("sell", 75, 100.0, depth=book(bids=[(100.0, 500)]), **FULL)
    assert r["verdict"] == "fill" and r["shortfall"] == 0 and r["orders"] == 1
    assert {c["rule"]: c["status"] for c in r["checks"]} == {"lot": "ok", "freeze": "ok", "band": "ok", "book": "ok"}


def test_fills_worse_than_the_paper_price():
    r = assess("sell", 150, 100.0, depth=book(bids=[(99.8, 75), (99.6, 500)]), **FULL)
    assert r["verdict"] == "worse"
    assert r["shortfall"] == pytest.approx(75 * 0.2 + 75 * 0.4)   # ₹45 worse than the paper fill
    assert r["est_price"] == pytest.approx(99.7)


def test_a_thin_book_fills_part_and_the_rest_rests():
    r = assess("buy", 300, 100.0, depth=book(asks=[(100.2, 75), (103.0, 900)]), **FULL)
    assert r["verdict"] == "partial" and r["filled_qty"] == 75 and r["limit_price"] == 100.5


def test_nothing_at_the_limit():
    r = assess("buy", 75, 100.0, depth=book(asks=[(102.0, 900)]), **FULL)
    assert r["verdict"] == "no_fill"
    assert "best ₹102.00" in next(c for c in r["checks"] if c["rule"] == "book")["detail"]


def test_a_rule_kite_refuses_wins_over_the_book():
    r = assess("sell", 100, 100.0, depth=book(bids=[(100.0, 500)]), **FULL)
    assert r["verdict"] == "reject"


def test_without_a_book_it_says_so():
    r = assess("sell", 75, 100.0, depth=None, **FULL)
    assert r["verdict"] == "unknown"
    assert assess("sell", 75, 100.0, depth=book(), **FULL)["verdict"] == "unknown"


def test_over_the_freeze_limit_still_fills_as_several_orders():
    r = assess("sell", 3600, 100.0, depth=book(bids=[(100.0, 5000)]), **FULL)
    assert r["verdict"] == "fill" and r["orders"] == 2


# ── the 60 s after ──────────────────────────────────────────────────────

def test_the_rest_fills_when_the_price_comes_to_the_limit():
    r = assess("buy", 150, 100.0, depth=book(asks=[(100.2, 75), (103.0, 900)]), **FULL)
    f = follow(r, "buy", 150, 100.0, [(1, 101.0), (12, 100.4), (20, 99.0)])
    assert f["verdict"] == "worse" and f["filled_qty"] == 150
    assert f["est_price"] == pytest.approx((75 * 100.2 + 75 * 100.5) / 150)
    assert f["followup"] == {"reached": True, "seconds": 12, "best": 99.0}
    assert f["checks"][-1]["rule"] == "follow" and f["checks"][-1]["status"] == "ok"


def test_a_limit_the_price_never_reaches_wouldnt_fill():
    r = assess("sell", 75, 100.0, depth=book(bids=[(98.0, 500)]), **FULL)
    f = follow(r, "sell", 75, 100.0, [(5, 98.5), (30, 99.2)])
    assert f["verdict"] == "no_fill"
    assert f["followup"] == {"reached": False, "seconds": 60, "best": 99.2}
    assert "re-price" in f["checks"][-1]["detail"]


def test_no_prices_after_the_fill():
    r = assess("sell", 75, 100.0, depth=book(bids=[(98.0, 500)]), **FULL)
    f = follow(r, "sell", 75, 100.0, [])
    assert f["verdict"] == "no_fill" and f["followup"]["reached"] is None
    assert f["checks"][-1]["status"] == "unknown"


def test_a_filled_order_isnt_followed():
    r = assess("sell", 75, 100.0, depth=book(bids=[(100.0, 500)]), **FULL)
    assert follow(r, "sell", 75, 100.0, [(1, 1.0)]) is r


# ── the NSE file ────────────────────────────────────────────────────────

def test_the_nse_freeze_file_is_read_by_its_headers():
    text = "﻿SR.NO., SYMBOL ,VOL_FRZ_QTY\n1,NIFTY,1800\n2, BANKNIFTY ,\"1,050\"\n3,BAD,x\n4,RELIANCE,25000\n"
    assert parse_freeze_csv(text) == {"NIFTY": 1800, "BANKNIFTY": 1050, "RELIANCE": 25000}


def test_a_file_without_the_columns_is_refused():
    with pytest.raises(ValueError):
        parse_freeze_csv("<html>blocked</html>")


def test_config_overrides_the_file():
    f = shadow.FreezeLimits(None, {"nifty": 3510})
    from datetime import date
    f.rows = {"NIFTY": (1800, "nse", date(2026, 10, 23)), "BANKNIFTY": (1050, "nse", date(2026, 10, 23))}
    assert f.get("NIFTY") == (3510, "config")
    assert f.get("banknifty") == (1050, "NSE file of 23 Oct")
    assert f.get("SENSEX") == (None, None)


def test_the_underlying_from_a_symbol():
    assert shadow.underlying_of("NIFTY26OCT25800CE") == "NIFTY"
    assert shadow.underlying_of("BANKNIFTY26OCT55700PE") == "BANKNIFTY"
    assert shadow.underlying_of("SENSEX2610282000CE") == "SENSEX"


# ── ready for live? ─────────────────────────────────────────────────────

from datetime import date, datetime, timedelta, timezone


def chk(lot, day, verdict="fill", shortfall=0.0, paper=100.0, qty=75, orders=1):
    at = datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=5)
    return {"lot_id": lot, "executed_at": at.isoformat(), "verdict": verdict, "shortfall": shortfall,
            "paper_price": paper, "filled_qty": qty if verdict in ("fill", "worse", "partial") else None,
            "qty": qty, "orders": orders, "symbol": f"S{lot}"}


D0 = date(2026, 9, 1)


def days(n):
    return [D0 + timedelta(days=i) for i in range(n)]


def test_an_intraday_record_of_20_clean_sessions_is_ready():
    checks = [chk(f"{i}a", d) for i, d in enumerate(days(20))] + [chk(f"{i}b", d, "worse", 7.5) for i, d in enumerate(days(20))]
    c = shadow.card("intraday", checks, [], {}, 0)
    assert c["ready"] and c["state"] == "ready" and c["sessions"] == 20
    assert c["accepted_pct"] == 100 and c["filled_pct"] == 100
    assert c["shortfall_pct"] == pytest.approx(0.05)    # ₹7.5 on ₹7,500, half the fills
    assert [r["ok"] for r in c["rules"]] == [True, True, True, True]


def test_not_yet_says_which_rule_and_by_how_much():
    checks = [chk(f"{i}", d) for i, d in enumerate(days(12))]
    checks.append(chk("r", days(12)[-1], "reject"))
    checks.append(chk("w", days(12)[-1], "worse", 150.0))    # 2% on one fill
    c = shadow.card("intraday", checks, [], {}, 2)
    assert not c["ready"] and c["state"] == "not_yet"
    r = {x["key"]: x for x in c["rules"]}
    assert r["record"]["value"] == "12 of 20" and not r["record"]["ok"]
    assert r["rejects"]["value"] == "1 refused" and not r["rejects"]["ok"]
    assert r["price"]["ok"], "0.15% on average is inside 1%"
    assert r["repairs"]["value"] == "2 open" and not r["repairs"]["ok"]
    assert c["worst"]["shortfall"] == 150.0 and c["worst"]["symbol"] == "Sw"


def test_a_reject_older_than_the_last_ten_sessions_no_longer_counts():
    ds = days(25)
    checks = [chk(f"{i}", d) for i, d in enumerate(ds)] + [chk("old", ds[5], "reject")]
    assert shadow.card("intraday", checks, [], {}, 0)["ready"]
    checks.append(chk("new", ds[20], "reject"))
    assert not shadow.card("intraday", checks, [], {}, 0)["ready"]


def test_unknown_checks_dont_count_towards_the_record():
    checks = [chk(f"{i}", d, "unknown") for i, d in enumerate(days(25))]
    c = shadow.card("intraday", checks, [], {}, 0)
    assert c["sessions"] == 0 and c["accepted_pct"] is None and not c["ready"]
    assert {x["key"]: x["value"] for x in c["rules"]}["price"] == "no fills yet"
    assert shadow.card("intraday", [], [], {}, 0)["state"] == "none"


def test_positional_counts_full_cycles_and_the_pnl_live_couldnt_have_made():
    ep = lambda ps, pnl, closed=True: {"positions": ps, "closed": closed, "pnl": pnl}   # noqa: E731
    lots = {"p1": ["a", "b"], "p2": ["c", "d"], "p3": ["e", "f"], "p4": ["g", "h"], "p5": ["i"]}
    d = days(9)
    checks = [chk("a", d[0]), chk("b", d[1]),                       # cycle 1: complete
              chk("c", d[2], "reject"), chk("d", d[3]),             # cycle 2: Kite refuses its entry
              chk("e", d[4]), chk("f", d[5], "no_fill"),            # cycle 3: its exit wouldn't fill
              chk("g", d[6]), chk("h", d[7], "unknown"),            # cycle 4: one fill unchecked
              chk("i", d[8])]                                       # cycle 5: still open
    eps = [ep(["p1"], [1000, 900, 850]), ep(["p2"], [5000, 4800, 4700]), ep(["p3"], [-700, -800, -850]),
           ep(["p4"], [200, 150, 100]), ep(["p5"], [0, 0, 0], closed=False)]
    c = shadow.card("positional", checks, eps, lots, 0)
    assert c["cycles"] == 3, "1, 2 and 3 are fully checked; 4 isn't; 5 is open"
    assert {x["key"]: x for x in c["rules"]}["record"]["value"] == "3 of 3"
    assert c["refused"] == {"n": 1, "pnl": 5000, "pnl_net": 4800, "pnl_net_slip": 4700}
    assert c["unfilled"] == {"n": 1, "pnl": -700, "pnl_net": -800, "pnl_net_slip": -850}
    assert c["total"] == {"pnl": 5500, "pnl_net": 5050, "pnl_net_slip": 4800}
    assert not c["ready"], "a refusal in the last ten sessions"


def test_freeze_splits_are_counted():
    c = shadow.card("intraday", [chk("a", D0, orders=3), chk("b", D0)], [], {}, 0)
    assert c["splits"] == 1
