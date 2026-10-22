"""A silent tick stall (prod, 21 Oct 2026: last tick 12:15 IST, the
websocket still "connected" until the close, nothing traded or updated):
in market hours, once ticks have flowed today and then stopped, the
watchdog reconnects with the same session, pushes once, and /health says
so — never on a holiday, before the open, after the close, or while
logged out."""

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.deployments.manager import DeploymentManager

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 21, 15, 0, tzinfo=IST)   # a Wednesday, in the session


class Disp:
    def __init__(self, last, connected=True, needs_login=False):
        self.last_tick_at, self.connected, self.access_token = last, connected, "tok"
        self._needs_login, self.reconnects = needs_login, []

    @property
    def status(self):
        return {"needs_login": self._needs_login}

    def reconnect(self, token):
        self.reconnects.append(token)


def manager(d):
    m = object.__new__(DeploymentManager)
    m.dispatcher, m.runners, m._stall_alerted, m._stall_retry_at = d, {}, False, None
    m.pushes = []

    async def push(title, body):
        m.pushes.append((title, body))
    m._push = push
    return m


def test_what_counts_as_a_stall():
    stalled = Disp(datetime(2026, 10, 21, 12, 15, 49, tzinfo=IST).astimezone(timezone.utc))
    assert round(manager(stalled).tick_stall(NOW)) == 2 * 3600 + 44 * 60 + 11, "the prod case: 2h44m"
    assert manager(Disp(NOW - timedelta(seconds=30))).tick_stall(NOW) is None, "ticks 30s ago: fine"
    assert manager(Disp(NOW - timedelta(seconds=95))).tick_stall(NOW) is not None, "95s: a stall"
    y = datetime(2026, 10, 20, 15, 29, tzinfo=IST)
    assert manager(Disp(y)).tick_stall(NOW) is None, "nothing yet today (a holiday): not a stall"
    assert manager(Disp(NOW.replace(hour=9, minute=10))).tick_stall(NOW) is None, "only pre-open ticks today"
    assert manager(Disp(NOW - timedelta(hours=1))).tick_stall(NOW.replace(hour=15, minute=45)) is None, "after the close"
    assert manager(Disp(NOW - timedelta(hours=1))).tick_stall(NOW + timedelta(days=3)) is None, "a Saturday"
    assert manager(Disp(NOW - timedelta(hours=1), connected=False)).tick_stall(NOW) is None, "disconnected: a different alert"
    assert manager(Disp(NOW - timedelta(hours=1), needs_login=True)).tick_stall(NOW) is None
    assert manager(Disp(None)).tick_stall(NOW) is None


def test_the_watchdog_reconnects_and_leaves_the_alerts_to_the_health_monitor():
    """Reconnecting is the watchdog's; the alert (every minute until prices
    return, then "Fixed") is app/ops_health.py's kite:stall issue."""
    d = Disp(NOW - timedelta(minutes=5))
    m = manager(d)
    asyncio.run(m.check_tick_stall(NOW))
    assert d.reconnects == ["tok"], "reconnected with the same session"
    asyncio.run(m.check_tick_stall(NOW + timedelta(seconds=30)))
    assert d.reconnects == ["tok"], "not again within 3 minutes"
    m._stall_retry_at -= 200
    asyncio.run(m.check_tick_stall(NOW + timedelta(minutes=4)))
    assert d.reconnects == ["tok", "tok"], "retried"
    d.last_tick_at = NOW + timedelta(minutes=5)
    asyncio.run(m.check_tick_stall(NOW + timedelta(minutes=5, seconds=10)))
    assert m._stall_alerted is False and m.pushes == []
