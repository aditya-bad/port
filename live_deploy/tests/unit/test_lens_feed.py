"""LinckLens's own Kite websocket (app/lens_feed.py), on a fake KiteTicker:
it follows the trading feed's session, subscribes the shares held in
quote mode, keeps each price and previous close, and tells browsers only
what changed."""

import asyncio

from app.lens_feed import LensFeed
from tests.unit.test_dispatcher import FakeTicker


class Main:
    api_key, access_token, connected = "key", None, False


def feed():
    FakeTicker.made = []
    scheduled = []

    def schedule(fn, *a):
        scheduled.append(getattr(fn, "__name__", str(fn)))
        fn(*a)
    f = LensFeed(Main(), ticker_cls=FakeTicker, schedule_on_ticker_thread=schedule)
    f.ws._reactor_running = lambda: True        # the trading feed already runs Kite's thread
    f.ws.bind_loop(asyncio.new_event_loop())
    return f, scheduled


def test_it_waits_for_shares_and_a_session_then_connects_on_the_ticker_thread():
    f, scheduled = feed()
    f.main.access_token = "tok"
    f._follow_session()
    assert FakeTicker.made == [], "nothing to follow yet: no connection"
    f.want({256265: "NIFTY 50", 408065: "INFY"})
    assert scheduled == ["_do_reconnect"] and FakeTicker.made[0].access_token == "tok"
    kws = FakeTicker.made[0]
    f.ws._on_connect(kws, None)
    assert sorted(kws.subscribed[0]) == [256265, 408065] and kws.modes[0][0] == "quote"
    f._follow_session()
    assert len(FakeTicker.made) == 1, "same session: left alone"
    f.main.access_token = "tok2"                 # the next day's login
    f._follow_session()
    assert len(FakeTicker.made) == 2 and FakeTicker.made[-1].access_token == "tok2"


def test_holdings_sold_are_dropped_and_new_ones_added():
    f, _ = feed()
    f.main.access_token = "tok"
    f.want({256265: "NIFTY 50", 408065: "INFY", 424961: "ITC"})
    kws = FakeTicker.made[0]
    f.ws._on_connect(kws, None)
    f.take([{"instrument_token": 424961, "last_price": 420.0, "ohlc": {"close": 418.0}}])
    f.want({256265: "NIFTY 50", 408065: "INFY", 2885: "RELIANCE"})
    assert kws.unsubscribed == [[424961]] and kws.subscribed[-1] == [2885]
    assert f.price(424961) is None, "a share no longer held has no price"


def test_ticks_keep_the_price_and_previous_close_and_only_changes_go_out():
    f, _ = feed()
    f.want({408065: "INFY", 256265: "NIFTY 50"})
    f.take([{"instrument_token": 408065, "last_price": 1500.0, "ohlc": {"close": 1480.0}},
            {"instrument_token": 999, "last_price": 1.0}])                   # not followed: ignored
    assert f.price(408065)["ltp"] == 1500.0 and f.price(408065)["close"] == 1480.0
    msg = f.changes()
    assert msg["p"] == {"INFY": [1500.0, 1480.0]}
    assert f.changes() is None, "nothing new"
    f.take([{"instrument_token": 408065, "last_price": 1500.0, "ohlc": {"close": 1480.0}}])
    assert f.changes() is None, "the same price again isn't news"
    f.take([{"instrument_token": 408065, "last_price": 1501.5}])            # an ltp-only tick keeps the close
    assert f.changes()["p"] == {"INFY": [1501.5, 1480.0]}
    assert f.snapshot()["p"] == {"INFY": [1501.5, 1480.0]}


def test_changes_reach_browsers_once_a_second_at_most():
    async def go():
        f = LensFeed(Main(), ticker_cls=FakeTicker, schedule_on_ticker_thread=lambda fn, *a: fn(*a), push_every=0.02, follow_every=10)
        f.start(asyncio.get_running_loop())
        q = await f.out.subscribe()
        f.want({408065: "INFY"})
        await asyncio.sleep(0.01)              # the feed's reader is subscribed
        await f.raw.broadcast([{"instrument_token": 408065, "last_price": 10.0, "ohlc": {"close": 9.0}}])
        await f.raw.broadcast([{"instrument_token": 408065, "last_price": 11.0, "ohlc": {"close": 9.0}}])
        msg = await asyncio.wait_for(q.get(), 1)
        await f.stop()
        return msg
    msg = asyncio.run(go())
    assert msg["p"] == {"INFY": [11.0, 9.0]}, "two ticks, one message, the latest price"
