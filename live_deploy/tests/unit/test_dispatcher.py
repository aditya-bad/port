"""The Kite tick dispatcher, driven by a fake KiteTicker: price/depth
caches, dynamic subscriptions, connect/give-up/recover alerts, and
reconnects marshalled onto the ticker thread."""

import asyncio
from datetime import datetime

import pytest

from app.broadcaster import Broadcaster
from app.dispatcher import LiveDataDispatcher


class FakeTicker:
    made = []

    def __init__(self, api_key, access_token):
        self.api_key, self.access_token = api_key, access_token
        self.subscribed, self.modes, self.unsubscribed = [], [], []
        self.connected = self.closed = False
        FakeTicker.made.append(self)

    def connect(self, threaded=False):
        self.connected = True

    def subscribe(self, tokens):
        self.subscribed.append(list(tokens))

    def set_mode(self, mode, tokens):
        self.modes.append((mode, list(tokens)))

    def unsubscribe(self, tokens):
        self.unsubscribed.append(list(tokens))

    def close(self):
        self.closed = True


def make(tokens=({"instrument_token": 256265, "symbol": "NIFTY 50"},), token=None, issues=None, mode="full", reactor_running=None):
    FakeTicker.made = []
    scheduled = []

    def schedule(fn, *args):
        scheduled.append(fn.__name__ if hasattr(fn, "__name__") else str(fn))
        fn(*args)

    async def on_issue(kind, msg):
        issues.append(kind)

    d = LiveDataDispatcher("key", list(tokens), mode, Broadcaster(), initial_access_token=token,
                           kite_ticker_cls=FakeTicker, schedule_on_ticker_thread=schedule,
                           on_connection_issue=on_issue if issues is not None else None,
                           reactor_running=reactor_running or (lambda: False))
    return d, scheduled


def test_needs_login_until_a_token_arrives():
    d, _ = make()
    d.bind_loop(asyncio.new_event_loop())
    assert d.status["needs_login"] is True and FakeTicker.made == []
    d.reconnect("tok")
    assert d.status["needs_login"] is False and d.access_token == "tok"
    assert FakeTicker.made[0].connected


def test_connect_subscribes_every_token_in_the_configured_mode():
    d, _ = make(token="tok")
    d.bind_loop(asyncio.new_event_loop())
    kws = FakeTicker.made[0]
    d._on_connect(kws, None)
    assert d.connected and kws.subscribed == [[256265]] and kws.modes == [("full", [256265])]


def test_ticks_update_prices_trade_times_and_only_non_empty_depth():
    d, _ = make()
    t = datetime(2026, 10, 14, 10, 0, 5)
    empty = {"buy": [{"price": 0, "quantity": 0}], "sell": []}
    book = {"buy": [{"price": 99.5, "quantity": 75}], "sell": [{"price": 100.5, "quantity": 75}]}
    d._on_ticks(None, [{"instrument_token": 1, "last_price": 100.0, "last_trade_time": t, "depth": empty},
                       {"instrument_token": 2, "last_price": 50.0, "exchange_timestamp": t, "depth": book},
                       {"instrument_token": 3}])
    assert d.last_prices == {1: 100.0, 2: 50.0}
    assert d.last_price_times == {1: t, 2: t}
    assert set(d.last_depths) == {2} and d.last_depths[2][0] == book
    assert d.ticks_received == 3 and d.last_tick_at is not None


def test_ticks_are_broadcast_on_the_loop():
    async def go():
        d, _ = make()
        d.bind_loop(asyncio.get_running_loop())
        q = await d.broadcaster.subscribe()
        ticks = [{"instrument_token": 1, "last_price": 1.0}]
        await asyncio.to_thread(d._on_ticks, None, ticks)    # from the ticker's thread
        assert await asyncio.wait_for(q.get(), 2) == ticks
    asyncio.run(go())


def test_dynamic_tokens_are_reference_counted_and_static_ones_never_released():
    d, _ = make(token="tok")
    d.bind_loop(asyncio.new_event_loop())
    kws = FakeTicker.made[0]
    d._on_connect(kws, None)
    assert d.add_instruments([{"instrument_token": 256265}]) == [], "static: already covered"
    assert d.add_instruments([{"instrument_token": 9, "symbol": "CE"}]) == [9]
    assert d.add_instruments([{"instrument_token": 9, "symbol": "CE"}]) == [], "second claim"
    assert kws.subscribed[-1] == [9]
    assert d.release_instruments([9]) == [], "one claim left"
    assert d.release_instruments([{"instrument_token": 9}]) == [9]
    assert kws.unsubscribed == [[9]] and 9 not in d.instrument_tokens and 9 not in d.token_labels
    assert d.release_instruments([256265, 12345]) == []
    assert 256265 in d.instrument_tokens


def test_tokens_added_while_disconnected_go_out_on_the_next_connect():
    d, _ = make()
    d.bind_loop(asyncio.new_event_loop())
    assert d.add_instruments([{"instrument_token": 9}]) == [9]
    d.reconnect("tok")
    kws = FakeTicker.made[-1]
    d._on_connect(kws, None)
    assert kws.subscribed == [[256265, 9]]


def test_second_reconnect_runs_on_the_ticker_thread_and_closes_the_old_socket():
    d, scheduled = make(token="tok1")
    d.bind_loop(asyncio.new_event_loop())
    first = FakeTicker.made[0]
    d.reconnect("tok2")
    assert scheduled == ["_do_reconnect"], "never connect() from the asyncio thread once the reactor runs"
    assert first.closed and FakeTicker.made[-1].access_token == "tok2"


def test_a_second_connection_in_the_process_connects_on_the_ticker_thread():
    """LinckLens's own feed is a second dispatcher: the trading feed's
    connection already runs Twisted's reactor, so even its FIRST connect
    must go through the reactor thread, not from the asyncio thread."""
    d, scheduled = make(tokens=(), reactor_running=lambda: True)
    d.bind_loop(asyncio.new_event_loop())
    d.reconnect("tok")
    assert scheduled == ["_do_reconnect"] and FakeTicker.made[-1].connected
    d, scheduled = make(tokens=(), token="tok", reactor_running=lambda: True)
    d.bind_loop(asyncio.new_event_loop())
    assert scheduled == ["_do_reconnect"], "a session at start, the same"


def test_give_up_alerts_retries_and_recovery_alerts_once():
    async def go():
        issues = []
        d, _ = make(token="tok", issues=issues)
        d._AUTO_RETRY_AFTER_GIVEUP_DELAY = 0.01
        d._REMINDER_INTERVAL = 0.05
        d.bind_loop(asyncio.get_running_loop())
        kws = FakeTicker.made[0]
        d._on_connect(kws, None)
        await asyncio.sleep(0.01)
        assert issues == [], "a normal first connect is not news"
        await asyncio.to_thread(d._on_noreconnect, kws)
        assert d.status["needs_login"] is True and d.connected is False
        await asyncio.sleep(0.2)
        assert issues[0] == "kite_disconnected" and issues.count("kite_disconnected") >= 2, "reminders repeat"
        assert len(FakeTicker.made) == 2, "retried with the same token"
        await asyncio.to_thread(d._on_connect, FakeTicker.made[-1], None)
        await asyncio.sleep(0.15)
        assert issues[-1] == "kite_reconnected"
        n = len(issues)
        await asyncio.sleep(0.15)
        assert len(issues) == n, "reminders stop once it's back"
    asyncio.run(go())


def test_close_error_reconnect_callbacks_track_state():
    d, _ = make(token="tok")
    d.bind_loop(asyncio.new_event_loop())
    kws = FakeTicker.made[0]
    d._on_connect(kws, None)
    d._on_close(kws, 1006, "gone")
    d._on_error(kws, 1006, "boom")
    d._on_reconnect(kws, 1)
    s = d.status
    assert s["kite_connected"] is False and s["last_error"] == "1006: boom" and s["reconnect_count"] == 1
    d.stop()
    assert kws.closed


@pytest.mark.parametrize("mode", ["ltp", "quote", "full"])
def test_tick_modes(mode):
    d, _ = make(mode=mode)
    assert d.status["tick_mode"] == mode
