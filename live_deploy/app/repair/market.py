"""
live_deploy — the market a repair replays: 1-minute candles played back
as ticks, and Kite, the dispatcher and the clock as a strategy sees them
at each of those ticks.

ONE MINUTE = FOUR TICKS. A 1-minute candle only says where the price
opened, closed and how far it went each way, not in which order. Each
minute is played as open -> one extreme -> the other -> close, and every
instrument is priced at the same moment of its own candle:

  phase    the index    a CE / future    a PE (and India VIX)
  open     open         open             open
  up       high         high             low
  down     low          low              high
  close    close        close            close

so a CE is at its high and a PE at its low when the index is at its high,
and the other way round (they move opposite ways). Whether the index went
up first or down first ("up_first" / "down_first") is the one thing a
candle can't tell; the engine plays both where it matters and asks.

A contract with no candle in a minute (no trade) keeps its last close,
with that older trade time, exactly as a live price would sit still.

Nothing here reaches the future: Kite's history is cut at the replay's
own "now", and today's candles are built from the 1-minute candles up to
the current tick (the forming candle carries only the ticks played so
far), so a strategy that seeds itself from history sees what it would
have seen live.
"""

from __future__ import annotations

import bisect
import sys
import threading
import time as _time
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN, SESSION_CLOSE = time(9, 15), time(15, 30)

UP_FIRST = ("open", "up", "down", "close")
DOWN_FIRST = ("open", "down", "up", "close")
ORDERS = {"up_first": UP_FIRST, "down_first": DOWN_FIRST}
STEP_SECONDS = (0, 20, 40, 59)   # where in the minute each of the four ticks lands

# Spot keys Kite answers ltp()/quote() for, as the resolver asks them.
INDEX_KEYS = {
    "NSE:NIFTY 50": 256265, "NSE:NIFTY BANK": 260105, "NSE:NIFTY FIN SERVICE": 257801,
    "NSE:NIFTY MIDCAP SELECT": 288009, "BSE:SENSEX": 265, "BSE:BANKEX": 274441,
    "NSE:INDIA VIX": 264969,
}
INDEX_LABELS = {tok: key.split(":", 1)[1] for key, tok in INDEX_KEYS.items()}
VIX_TOKEN = 264969
INTERVAL_MINUTES = {"minute": 1, "3minute": 3, "5minute": 5, "10minute": 10, "15minute": 15,
                    "30minute": 30, "60minute": 60}


def moves_against(symbol: str) -> bool:
    """True for what falls when the index rises: puts, and India VIX."""
    s = (symbol or "").upper()
    return s.endswith("PE") or s == "INDIA VIX"


def field_for(phase: str, inverse: bool) -> str:
    if phase in ("open", "close"):
        return phase
    up = phase == "up"
    return ("low" if up else "high") if inverse else ("high" if up else "low")


def natural_order(candle: Optional[dict]) -> str:
    """The usual reading of a candle: a green one dipped first, then ran
    up; a red one ran up first, then fell."""
    if candle is None:
        return "down_first"
    return "down_first" if candle["close"] >= candle["open"] else "up_first"


def naive_ist(dt: datetime) -> datetime:
    return dt.astimezone(IST).replace(tzinfo=None) if dt.tzinfo is not None else dt


def minute_of(dt: datetime) -> datetime:
    return naive_ist(dt).replace(second=0, microsecond=0)


def session_minutes(day: date, start: time, end: time) -> list[datetime]:
    """Every minute from `start` up to (not including) `end`."""
    t, stop = datetime.combine(day, start), datetime.combine(day, end)
    out = []
    while t < stop:
        out.append(t)
        t += timedelta(minutes=1)
    return out


class MinuteBook:
    """The day's 1-minute candles per instrument, fetched once each.

    `source(token, start, end)` returns Kite-shaped candles (a "date" and
    open/high/low/close, optionally volume and oi); `stored` is the app's
    own saved candles (candles_1m) by token, used when Kite has none (an
    expired contract)."""

    def __init__(self, day: date, source: Callable, stored: Optional[dict] = None):
        self.day = day
        self.source = source
        self.stored = stored or {}
        self.rows: dict[int, dict[datetime, dict]] = {}
        self.keys: dict[int, list[datetime]] = {}
        self.origin: dict[int, str] = {}
        self.errors: dict[int, str] = {}
        self.inverse: dict[int, bool] = {}
        self.symbols: dict[int, str] = {}
        self._lock = threading.RLock()

    def note(self, token: int, symbol: Optional[str]) -> None:
        if symbol and token not in self.symbols:
            self.symbols[token] = symbol
            self.inverse[token] = moves_against(symbol)

    def load(self, token: int) -> dict[datetime, dict]:
        token = int(token)
        with self._lock:
            if token in self.rows:
                return self.rows[token]
            got, origin = [], "none"
            try:
                got = self.source(token, datetime.combine(self.day, SESSION_OPEN),
                                  datetime.combine(self.day, SESSION_CLOSE)) or []
                origin = "kite" if got else "none"
            except Exception as e:   # expired contract, no session, rate limit after retries
                self.errors[token] = f"{type(e).__name__}: {e}"
            if not got and self.stored.get(token):
                got, origin = self.stored[token], "stored"
            rows = {}
            for c in got:
                m = minute_of(c["date"])
                if m.date() != self.day:
                    continue
                rows[m] = {"open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]),
                           "close": float(c["close"]), "volume": int(c.get("volume") or 0),
                           "oi": int(c.get("oi") or 0)}
            self.rows[token] = rows
            self.keys[token] = sorted(rows)
            self.origin[token] = origin
            return rows

    def candle(self, token: int, minute: datetime) -> Optional[dict]:
        return self.load(token).get(minute)

    def last_before(self, token: int, minute: datetime) -> Optional[tuple[datetime, dict]]:
        """The latest candle strictly before `minute`."""
        self.load(token)
        keys = self.keys[int(token)]
        i = bisect.bisect_left(keys, minute)
        return (keys[i - 1], self.rows[int(token)][keys[i - 1]]) if i else None

    def at(self, token: int, minute: datetime, phase: str) -> Optional[float]:
        """The price at one phase of `minute`, carrying the last close over
        a minute with no trade."""
        got = self.price(token, minute, phase, minute)
        return got[0] if got else None

    def price(self, token: int, minute: datetime, phase: str, now: datetime) -> Optional[tuple[float, datetime]]:
        """(price, last traded at) at this tick, or None before its first trade."""
        token = int(token)
        c = self.candle(token, minute)
        if c is not None:
            return c[field_for(phase, self.inverse.get(token, False))], now
        prev = self.last_before(token, minute)
        if prev is None:
            return None
        return prev[1]["close"], prev[0] + timedelta(seconds=59)

    def partial(self, token: int, minute: datetime, phases: tuple) -> Optional[dict]:
        """`minute`'s candle as far as `phases` have been played."""
        c = self.candle(token, minute)
        if c is None:
            return None
        inv = self.inverse.get(int(token), False)
        vals = [c[field_for(p, inv)] for p in phases]
        return {"open": c["open"], "high": max(vals), "low": min(vals), "close": vals[-1],
                "volume": c["volume"], "oi": c["oi"]}

    def day_volume(self, token: int, minute: datetime) -> int:
        rows = self.load(token)
        return sum(c["volume"] for m, c in rows.items() if m <= minute)

    def oi(self, token: int, minute: datetime) -> int:
        c = self.candle(token, minute) or (self.last_before(token, minute) or (None, {}))[1]
        return int(c.get("oi") or 0)


class ReplayClock:
    """Where the replay is: which minute, which order it's played in, and
    which of its four ticks."""

    def __init__(self, day: date):
        self.day = day
        self.minute = datetime.combine(day, SESSION_OPEN)
        self.order = DOWN_FIRST
        self.step = 0

    def set(self, minute: datetime, order: tuple = None, step: int = 0) -> None:
        self.minute = minute
        if order is not None:
            self.order = order
        self.step = step

    @property
    def phase(self) -> str:
        return self.order[self.step]

    @property
    def played(self) -> tuple:
        return self.order[: self.step + 1]

    @property
    def now(self) -> datetime:
        """Naive IST, as Kite stamps ticks."""
        return self.minute + timedelta(seconds=STEP_SECONDS[self.step])

    def now_ist(self) -> datetime:
        return self.now.replace(tzinfo=IST)


class ReplayKite:
    """What the app asks Kite's REST API, answered at the replay's own
    moment: instruments from the real master (today's contracts), prices
    from the 1-minute candles, history cut at "now". There is no order
    book in history: quote() shows a one-tick spread around the price,
    so liquidity checks pass on open interest alone (noted in the plan)."""

    def __init__(self, real, book: MinuteBook, clock: ReplayClock):
        self.real = real
        self.book = book
        self.clock = clock
        self._masters: dict[str, list] = {}
        self._by_key: dict[str, dict] = {}
        self._by_token: dict[int, dict] = {}
        self._lock = threading.RLock()

    # instrument master
    def instruments(self, exchange=None):
        with self._lock:
            if exchange not in self._masters:
                rows = list(self.real.instruments(exchange) if exchange else self.real.instruments())
                self._masters[exchange] = rows
                for r in rows:
                    self._by_key[f"{r.get('exchange', exchange)}:{r['tradingsymbol']}"] = r
                    self._by_token[int(r["instrument_token"])] = r
            return list(self._masters[exchange])

    def _token(self, key) -> Optional[int]:
        key = str(key)
        if key.isdigit():
            return int(key)
        if key in INDEX_KEYS:
            return INDEX_KEYS[key]
        if key not in self._by_key and ":" in key:
            try:
                self.instruments(key.split(":", 1)[0])
            except Exception:
                return None
        row = self._by_key.get(key)
        return int(row["instrument_token"]) if row else None

    def _symbol(self, token: int, key: str) -> str:
        if token in INDEX_LABELS:
            return INDEX_LABELS[token]
        row = self._by_token.get(token)
        return row["tradingsymbol"] if row else key.split(":", 1)[-1]

    def _priced(self, keys):
        keys = [keys] if isinstance(keys, (str, int)) else list(keys)
        out = {}
        for k in keys:
            tok = self._token(k)
            if tok is None:
                continue
            self.book.note(tok, self._symbol(tok, str(k)))
            got = self.book.price(tok, self.clock.minute, self.clock.phase, self.clock.now)
            if got is None:
                continue
            out[str(k)] = (tok, got[0], got[1])
        return out

    def ltp(self, keys):
        return {k: {"instrument_token": tok, "last_price": p} for k, (tok, p, _) in self._priced(keys).items()}

    def quote(self, keys):
        out = {}
        m = self.clock.minute
        for k, (tok, p, traded) in self._priced(keys).items():
            tick = 0.05
            out[k] = {
                "instrument_token": tok, "last_price": p, "last_trade_time": traded,
                "volume": self.book.day_volume(tok, m), "oi": self.book.oi(tok, m),
                "depth": {"buy": [{"price": round(max(0.05, p - tick * (i + 1)), 2), "quantity": 1800, "orders": 6} for i in range(5)],
                          "sell": [{"price": round(p + tick * (i + 1), 2), "quantity": 1800, "orders": 6} for i in range(5)]},
            }
        return out

    # history
    def historical_data(self, instrument_token, from_date, to_date, interval, continuous=False, oi=False):
        tok = int(instrument_token)
        now = self.clock.now
        frm, to = naive_ist(from_date), min(naive_ist(to_date), now)
        midnight = datetime.combine(self.clock.day, time(0))
        out = []
        if frm < midnight:
            kw = {}
            if continuous:
                kw["continuous"] = continuous
            if oi:
                kw["oi"] = oi
            prev = self.real.historical_data(tok, frm, min(to, midnight - timedelta(seconds=1)), interval, **kw) or []
            out += [c for c in prev if naive_ist(c["date"]) < midnight]
        if to >= datetime.combine(self.clock.day, SESSION_OPEN):
            out += self._today(tok, max(frm, midnight), to, interval)
        return out

    def _today(self, tok: int, frm: datetime, to: datetime, interval: str) -> list[dict]:
        """Today's candles at `interval` from the 1-minute ones played so
        far, the last one still forming."""
        rows = self.book.load(tok)
        mins = [m for m in self.book.keys[tok] if m < self.clock.minute]
        candles = [(m, rows[m]) for m in mins]
        cur = self.book.partial(tok, self.clock.minute, self.clock.played)
        if cur is not None:
            candles.append((self.clock.minute, cur))
        open_ = datetime.combine(self.clock.day, SESSION_OPEN)
        step = INTERVAL_MINUTES.get(interval)
        buckets: dict[datetime, dict] = {}
        for m, c in candles:
            key = datetime.combine(m.date(), time(0)) if step is None else \
                open_ + timedelta(minutes=((m - open_).seconds // 60) // step * step)
            b = buckets.get(key)
            if b is None:
                buckets[key] = {"date": key, "open": c["open"], "high": c["high"], "low": c["low"],
                                "close": c["close"], "volume": c["volume"], "oi": c["oi"]}
            else:
                b["high"], b["low"] = max(b["high"], c["high"]), min(b["low"], c["low"])
                b["close"], b["volume"], b["oi"] = c["close"], b["volume"] + c["volume"], c["oi"]
        lo = datetime.combine(frm.date(), time(0)) if step is None else frm.replace(second=0, microsecond=0)
        return [{**b, "date": b["date"].replace(tzinfo=IST)} for k, b in sorted(buckets.items()) if lo <= k <= to]


class ReplayDispatcher:
    """The dispatcher's price cache as a strategy reads it: what's
    subscribed (the deployment's index, VIX, its legs, and any contract a
    strategy subscribes to), each priced at the current tick. Any other
    price a strategy asks for goes through ReplayKite."""

    def __init__(self, book: MinuteBook, clock: ReplayClock):
        self.book = book
        self.clock = clock
        self.last_prices: dict[int, float] = {}
        self.last_price_times: dict[int, datetime] = {}
        self.last_depths: dict = {}
        self.token_labels: dict[int, str] = dict(INDEX_LABELS)
        self.subscribed: set[int] = set()   # the replay adds the deployment's own index, VIX and its legs
        for tok, label in INDEX_LABELS.items():
            book.note(tok, label)
        self.access_token, self.api_key, self.connected = "replay", "replay", True
        self.status = {"needs_login": False}

    def add_instruments(self, items) -> None:
        for it in items:
            tok = int(it["instrument_token"])
            self.book.note(tok, it.get("symbol") or it.get("tradingsymbol"))
            self.subscribed.add(tok)
            self._publish(tok)

    def release_instruments(self, tokens) -> None:
        pass   # keep pricing them: a leg may still be open (the live dispatcher does too)

    def publish(self) -> None:
        for tok in list(self.subscribed):
            self._publish(tok)

    def _publish(self, tok: int) -> None:
        got = self.book.price(tok, self.clock.minute, self.clock.phase, self.clock.now)
        if got is not None:
            self.last_prices[tok], self.last_price_times[tok] = got

    def save(self) -> dict:
        return {"p": dict(self.last_prices), "t": dict(self.last_price_times), "s": set(self.subscribed)}

    def load(self, s: dict) -> None:
        self.last_prices, self.last_price_times, self.subscribed = dict(s["p"]), dict(s["t"]), set(s["s"])


@contextmanager
def replaying(kite: ReplayKite, clock: ReplayClock):
    """Point every place the app reaches Kite, the clock and expiry
    rotation at the replay, and put them all back afterwards."""
    import app.clock as app_clock
    import app.options.resolver as R
    from app import vix
    saved = []

    def swap(obj, name, value):
        saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    for name, mod in list(sys.modules.items()):
        if (name == "app" or name.startswith("app.")) and getattr(mod, "get_kite_connect", None) is not None:
            swap(mod, "get_kite_connect", lambda d: kite)
    swap(R, "_KITE_CALL_MIN_INTERVAL", 0.0)
    orig = R.OptionsResolver._resolve_from_list
    swap(R.OptionsResolver, "_resolve_from_list",
         lambda self, expiries, selector, reference_date=None: orig(self, expiries, selector, reference_date or clock.day))
    swap(app_clock, "now_ist", clock.now_ist)
    caches = dict(R._INSTRUMENT_CACHE)
    R._INSTRUMENT_CACHE.clear()
    vix_token = vix.state.get("token")
    vix.state["token"] = VIX_TOKEN
    try:
        yield
    finally:
        for obj, name, value in reversed(saved):
            setattr(obj, name, value)
        R._INSTRUMENT_CACHE.clear()
        R._INSTRUMENT_CACHE.update(caches)
        vix.state["token"] = vix_token


class KiteGate:
    """A real KiteConnect for the repair: its instrument masters fetched
    once each, and every request spaced to Kite's ~3 a second and retried
    on its rate limit."""

    def __init__(self, kite, min_gap: float = 0.35, attempts: int = 5):
        self.kite = kite
        self.min_gap, self.attempts = min_gap, attempts
        self._next = 0.0
        self._lock = threading.Lock()
        self._masters: dict = {}

    def call(self, fn, *args, **kwargs):
        last = None
        for i in range(self.attempts):
            with self._lock:
                wait = self._next - _time.monotonic()
                if wait > 0:
                    _time.sleep(wait)
                self._next = _time.monotonic() + self.min_gap
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last = e
                if "too many" in str(e).lower() or getattr(e, "code", None) == 429:
                    _time.sleep(0.6 * 2 ** i)
                    continue
                raise
        raise last

    def instruments(self, exchange=None):
        if exchange not in self._masters:
            self._masters[exchange] = self.call(self.kite.instruments, exchange) if exchange else self.call(self.kite.instruments)
        return self._masters[exchange]

    def historical_data(self, *args, **kwargs):
        return self.call(self.kite.historical_data, *args, **kwargs)


def kite_minute_source(gate: KiteGate) -> Callable:
    """A MinuteBook source over Kite: one 1-minute request per instrument
    for the day, with open interest where Kite has it."""
    def fetch(token: int, start: datetime, end: datetime) -> list[dict]:
        try:
            return gate.historical_data(token, start, end, "minute", oi=True)
        except Exception:
            return gate.historical_data(token, start, end, "minute")
    return fetch
