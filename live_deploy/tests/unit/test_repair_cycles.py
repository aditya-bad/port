"""Which cycle a repaired day belongs to, and whether it's still the
deployment's current one (app/v2/book.py: cycle_of) — what decides if a
deployment carries the Repaired mark on every page."""

from datetime import date, datetime, time

from app.clock import IST
from app.v2.book import cycle_of

FRI = date(2026, 10, 16)


def at(d, h=10, m=0):
    return datetime.combine(d, time(h, m), IST)


def test_intraday_a_repair_is_current_until_the_next_session_opens():
    assert cycle_of("intraday", [], FRI, at(FRI, 18)) == {"from": "2026-10-16", "to": "2026-10-16", "current": True}
    assert cycle_of("intraday", [], FRI, at(date(2026, 10, 18), 20))["current"], "through the weekend"
    assert cycle_of("intraday", [], FRI, at(date(2026, 10, 19), 9, 14))["current"], "until Monday’s open"
    assert not cycle_of("intraday", [], FRI, at(date(2026, 10, 19), 9, 15))["current"], "Monday’s session is a new cycle"


def ep(start, end):
    return {"start": at(start), "end": at(end, 15) if end else None, "rows": []}


def test_positional_a_repair_is_the_cycle_it_falls_in():
    eps = [ep(date(2026, 10, 1), date(2026, 10, 9)), ep(date(2026, 10, 12), date(2026, 10, 16))]
    c = cycle_of("positional", eps, date(2026, 10, 5), at(date(2026, 10, 20)))
    assert c == {"from": "2026-10-01", "to": "2026-10-09", "current": False}, "an older cycle"
    c = cycle_of("positional", eps, date(2026, 10, 14), at(date(2026, 10, 20)))
    assert c == {"from": "2026-10-12", "to": "2026-10-16", "current": True}, "the latest, closed: until a new entry"


def test_positional_an_open_cycle_has_no_end_yet():
    eps = [ep(date(2026, 10, 12), None)]
    assert cycle_of("positional", eps, date(2026, 10, 14), at(date(2026, 10, 20))) == {"from": "2026-10-12", "to": None, "current": True}


def test_positional_a_repair_on_a_flat_day_is_that_day():
    eps = [ep(date(2026, 10, 1), date(2026, 10, 9)), ep(date(2026, 10, 12), date(2026, 10, 16))]
    assert cycle_of("positional", eps, date(2026, 10, 10), at(date(2026, 10, 20))) == {"from": "2026-10-10", "to": "2026-10-10", "current": False}
    assert cycle_of("positional", eps, date(2026, 10, 19), at(date(2026, 10, 20)))["current"], "nothing after it yet"
