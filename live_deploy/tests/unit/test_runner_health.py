"""What keeps a running deployment safe from bad data and its own failures
(app/deployments/health.py), and the strategy-side helpers that report
through it (StrategyBase): each piece on its own."""

import asyncio
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.deployments import health
from app.deployments.health import Breaker, BlockedWatch, Issues, TickFilter, describe
from app.deployments.runner import DeploymentRunner
from app.deployments.strategy_base import StrategyBase

T0 = datetime(2026, 10, 14, 10, 0)


def tk(p, at=T0, tok=1):
    return {"instrument_token": tok, "last_price": p, "exchange_timestamp": at}


# ── TickFilter ────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [None, 0, -5.0, float("nan"), float("inf"), "abc", True])
def test_a_price_that_isnt_a_positive_number_is_dropped(bad):
    f = TickFilter()
    assert f.check(tk(bad)) == (None, "bad_price")
    assert f.dropped["bad_price"] == 1


def test_no_time_older_and_repeated_ticks_are_dropped():
    f = TickFilter()
    assert f.check({"instrument_token": 1, "last_price": 100.0}) == (None, "no_time")
    assert f.check(tk(100.0))[1] is None
    assert f.check(tk(100.0)) == (None, "duplicate")
    assert f.check(tk(100.5)) == (tk(100.5), None), "same second, new price: real"
    assert f.check(tk(100.0, T0 - timedelta(seconds=1))) == (None, "out_of_order")


def test_each_instrument_is_checked_on_its_own():
    f = TickFilter()
    f.check(tk(100.0, tok=1))
    assert f.check(tk(25000.0, tok=2))[1] is None, "a different instrument's price is no spike"


def test_a_spike_is_held_until_the_next_tick_confirms_it():
    f = TickFilter()
    f.check(tk(100.0))
    assert f.check(tk(300.0, T0 + timedelta(seconds=1))) == (None, "held")
    t = tk(301.0, T0 + timedelta(seconds=2))
    assert f.check(t) == (t, None), "two in a row agree: the market really moved"
    assert f.last[1][1] == 301.0


def test_a_spike_the_next_tick_doesnt_confirm_is_discarded():
    f = TickFilter()
    f.check(tk(100.0))
    f.check(tk(300.0, T0 + timedelta(seconds=1)))
    t = tk(100.2, T0 + timedelta(seconds=2))
    assert f.check(t) == (t, None)
    assert f.dropped["spike"] == 1


def test_an_overnight_gap_is_allowed_more_room():
    f = TickFilter()
    f.check(tk(100.0))
    nxt = T0 + timedelta(days=1)
    assert f.check(tk(110.0, nxt))[1] is None, "10% overnight: a gap, not a spike"
    assert f.check(tk(140.0, nxt + timedelta(days=1)))[1] == "held", "40% overnight is held"


# ── Breaker ───────────────────────────────────────────────────────────

def test_the_breaker_trips_after_five_failures_and_backs_off():
    b = Breaker()
    for _ in range(4):
        assert b.failed(T0, "boom") is None
        assert b.allow(T0)
    waits = []
    now = T0
    for _ in range(7):
        until = b.failed(now, "boom")
        waits.append(round((until - now).total_seconds() / 60))
        assert not b.allow(now) and b.allow(until)
        now = until
    assert waits == [1, 2, 4, 8, 15, 15, 15]
    assert b.total == 11 and b.last_error == "boom"


def test_a_success_resets_the_breaker_and_says_it_ended_a_run():
    b = Breaker()
    assert b.succeeded() is False
    for _ in range(6):
        b.failed(T0, "x")
    assert b.succeeded() is True
    assert b.fails == 0 and b.skip_until is None and b.allow(T0)


def test_the_same_error_is_logged_at_most_every_ten_minutes():
    b = Breaker()
    assert b.should_log("x", T0)
    assert not b.should_log("x", T0 + timedelta(minutes=9))
    assert b.should_log("y", T0 + timedelta(minutes=9))
    assert b.should_log("x", T0 + timedelta(minutes=10))


# ── BlockedWatch ──────────────────────────────────────────────────────

def test_a_rule_is_raised_after_ten_minutes_blocked_and_clears_when_through():
    w = BlockedWatch()
    assert w.blocked("entry", T0, "Kite down") is None
    assert w.blocked("entry", T0 + timedelta(minutes=9), "still down") is None
    since, why = w.blocked("entry", T0 + timedelta(minutes=10), "still down")
    assert since == T0 and why == "still down", "since the first block, with the latest reason"
    assert w.through("entry") is True
    assert w.through("entry") is False
    assert w.blocked("entry", T0 + timedelta(minutes=30), "again") is None, "a new run starts over"


# ── describe ──────────────────────────────────────────────────────────

def test_describe_names_the_error_and_where_in_the_app_it_was_raised():
    try:
        TickFilter().check(None)
    except AttributeError as e:
        sig, where = describe(e)
    assert sig.startswith("AttributeError: ")
    assert where.startswith("deployments/health.py:") and where.endswith(" in price_of"), "the innermost app frame"
    assert describe(ValueError()) == ("ValueError", "")


# ── Issues ────────────────────────────────────────────────────────────

class FakeQueries:
    def __init__(self):
        self.upserts, self.resolved, self.fail = [], [], False

    async def open_strategy_issue_keys(self, pool, dep):
        return ["old"]

    async def upsert_strategy_issue(self, pool, dep, key, kind, severity, source, title, detail, data, day, expires_at, count=1):
        if self.fail:
            raise ConnectionError("db down")
        self.upserts.append({"key": key, "kind": kind, "count": count, "expires_at": expires_at, "source": source})
        return len([u for u in self.upserts if u["key"] == key]) == 1

    async def resolve_strategy_issue(self, pool, dep, key):
        self.resolved.append(key)
        return True


@pytest.fixture
def fq(monkeypatch):
    from app.db import queries
    q = FakeQueries()
    for name in ("open_strategy_issue_keys", "upsert_strategy_issue", "resolve_strategy_issue"):
        monkeypatch.setattr(queries, name, getattr(q, name))
    return q


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self, tz=None):
        return self.now


def issues(monkeypatch, events=None):
    clock = Clock(datetime(2026, 10, 14, 4, 30, tzinfo=timezone.utc))

    class DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now
    monkeypatch.setattr(health, "datetime", DT)

    async def record(kind, message=None, metadata=None):
        if events is not None:
            events.append((kind, message))
    return Issues(None, "dep", record, "test"), clock


def run(c):
    return asyncio.run(c)


def test_a_report_is_written_at_most_once_a_minute_and_counts_what_it_held_back(fq, monkeypatch):
    events = []
    iss, clock = issues(monkeypatch, events)

    async def go():
        await iss.report("crash", "boom")
        await iss.report("crash", "boom")
        await iss.report("crash", "boom")
        clock.now += timedelta(seconds=61)
        await iss.report("crash", "boom")
    run(go())
    assert [u["count"] for u in fq.upserts] == [1, 3]
    assert events == [("strategy_issue", "boom")], "the event is for a new issue only"
    assert iss.is_open("crash")


def test_a_ttl_becomes_an_expiry_and_the_source_is_kept(fq, monkeypatch):
    iss, clock = issues(monkeypatch)
    run(iss.report("bad_ticks", "x", ttl=timedelta(minutes=30), source="strategy"))
    assert fq.upserts[0]["expires_at"] == clock.now + timedelta(minutes=30)
    assert fq.upserts[0]["source"] == "strategy"


def test_resolve_only_touches_an_open_issue(fq, monkeypatch):
    events = []
    iss, _ = issues(monkeypatch, events)
    run(iss.resolve("never-raised"))
    assert fq.resolved == []
    run(iss.resolve("old"))
    assert fq.resolved == ["old"], "issues already open in the database count (loaded on first use)"
    assert ("strategy_issue_resolved", "Cleared: old") in events


def test_reporting_never_raises_into_trading(fq, monkeypatch):
    fq.fail = True
    iss, _ = issues(monkeypatch)
    run(iss.report("crash", "boom"))      # no exception
    assert not iss.is_open("crash")


def test_a_disabled_issues_writes_nothing(fq, monkeypatch):
    iss, _ = issues(monkeypatch)
    iss.enabled = False
    run(iss.report("crash", "boom"))
    run(iss.resolve("old"))
    assert fq.upserts == [] and fq.resolved == []


# ── state that can't be saved as JSON ─────────────────────────────────

def test_nan_and_infinity_in_state_are_saved_as_null():
    clean = DeploymentRunner._clean_state({"a": float("nan"), "b": [1.0, float("inf")], "c": {"d": -float("inf"), "e": 2}})
    assert clean == {"a": None, "b": [1.0, None], "c": {"d": None, "e": 2}}


# ── StrategyBase: entry retry, lost state, the book ───────────────────

class S(StrategyBase):
    async def on_start(self, runner): ...
    async def on_tick(self, runner, tick): ...
    async def on_stop(self, runner): ...


class R:
    def __init__(self, cfg=None):
        self.config, self.reports, self.resolved = cfg or {}, [], []

    async def report(self, kind, title, detail=None, **kw):
        self.reports.append((kind, kw.get("key"), title, detail))

    async def resolve(self, key):
        self.resolved.append(key)


def test_an_entry_that_fails_is_retried_each_minute_then_skipped():
    s, r = S(), R({"entry_retry_minutes": 10})
    entry = time(9, 20)
    at = datetime.combine(date(2026, 10, 14), entry)

    async def go():
        assert await s.entry_failed(r, at, entry, "Kite down") is False
        assert s.entry_should_wait(at + timedelta(seconds=30))
        assert not s.entry_should_wait(at + timedelta(minutes=1))
        assert await s.entry_failed(r, at + timedelta(minutes=10), entry, "Kite down") is True
        await s.entry_done(r, at)
    run(go())
    assert [k for k, *_ in r.reports] == ["entry_delayed", "entry_skipped"]
    assert {key for _, key, *_ in r.reports} == {"entry:2026-10-14"}, "one issue that changes kind"
    assert "09:30" in r.reports[0][3]
    assert r.resolved == ["entry:2026-10-14"]


def test_unusable_state_is_reported_with_what_was_done_instead():
    r = R()
    run(S().state_unusable(r, "The saved state was unreadable", "Counted from the book."))
    kind, key, title, detail = r.reports[0]
    assert kind == key == "state_lost"
    assert detail == "The saved state was unreadable. Counted from the book."


def test_book_helpers_say_nothing_rather_than_raise():
    class Broken:
        async def positions_opened_on(self, day, side=None):
            raise ConnectionError("db down")

        async def opened_on(self, day):
            raise ConnectionError("db down")
    s = S()
    assert run(s.entries_on(Broken(), date(2026, 10, 14))) == 0
    assert run(s.opened_today(Broken(), date(2026, 10, 14))) is False
    assert run(s.entries_on(object(), date(2026, 10, 14))) == 0


def test_a_decision_reaches_the_runners_watch_with_its_flags():
    seen = []

    class W:
        async def note_decision(self, ts, rule, outcome, summary, fault=False, missed=False):
            seen.append((rule, outcome, fault, missed))

        async def record_decision(self, *a):
            pass
    run(S().decide(W(), T0, "entry", "blocked", "Kite down", fault=True, missed=True))
    assert seen == [("entry", "blocked", True, True)]
