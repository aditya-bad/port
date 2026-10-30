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

A contract Kite has no trade in all day (a far strike on BSE's thin
SENSEX/BANKEX monthlies) is priced by model instead: Black-76 off its
index's own candles, at the implied volatility its last known price
gives (the deployment's own fill, or its last trade in the sessions
before). The plan says which legs were priced that way. Anything else
with no trade yet today (a thin future) is at its last trade in Kite's
history, as Kite's own ltp() would answer.

Nothing here reaches the future: Kite's history is cut at the replay's
own "now", and today's candles are built from the 1-minute candles up to
the current tick (the forming candle carries only the ticks played so
far), so a strategy that seeds itself from history sees what it would
have seen live.
"""

from __future__ import annotations

import bisect
import dataclasses
import sys
import threading
import time as _time
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN, SESSION_CLOSE = time(9, 15), time(15, 30)
KITE_NEVER_TRADED = datetime(1970, 1, 1, 5, 30)   # Kite's trade time for a contract that never traded (epoch, in IST)

UP_FIRST = ("open", "up", "down", "close")
DOWN_FIRST = ("open", "down", "up", "close")
ORDERS = {"up_first": UP_FIRST, "down_first": DOWN_FIRST}
STEP_SECONDS = (0, 20, 40, 59)   # where in the minute each of the four ticks lands
MOMENTS = 20   # a minute's range checked at 0, 5%, ... 100% (ReplayKite.follow_app)

# Spot keys Kite answers ltp()/quote() for, as the resolver asks them.
INDEX_KEYS = {
    "NSE:NIFTY 50": 256265, "NSE:NIFTY BANK": 260105, "NSE:NIFTY FIN SERVICE": 257801,
    "NSE:NIFTY MIDCAP SELECT": 288009, "BSE:SENSEX": 265, "BSE:BANKEX": 274441,
    "NSE:INDIA VIX": 264969,
}
INDEX_LABELS = {tok: key.split(":", 1)[1] for key, tok in INDEX_KEYS.items()}
VIX_TOKEN = 264969
# The index an option's price is modelled on when it never traded.
INDEX_OF = {"NIFTY": 256265, "BANKNIFTY": 260105, "FINNIFTY": 257801, "MIDCPNIFTY": 288009,
            "SENSEX": 265, "BANKEX": 274441}
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

    def __init__(self, day: date, source: Callable, stored: Optional[dict] = None,
                 lookup: Optional[Callable] = None, ltp: Optional[Callable] = None,
                 history: Optional[Callable] = None, quote: Optional[Callable] = None):
        self.day = day
        self.source = source
        self.stored = stored or {}
        self.lookup = lookup                 # tradingsymbol -> its row in Kite's master (or None)
        self.ltp = ltp                       # "EXCH:SYMBOL" -> Kite's last price now (or None)
        self.history = history               # (token, from, to, interval) -> Kite candles, for daily lookbacks
        self.quote = quote                   # ["EXCH:SYMBOL", ...] -> Kite's quote for each (one request for many)
        self.quoted: dict[int, tuple] = {}   # token -> (last_price, last_trade_time) from that quote
        self._key_of: dict[int, str] = {}    # token -> "EXCH:SYMBOL"
        self.never_traded: set[int] = set()  # tokens Kite's quote shows no trade at all for
        self.say: Optional[Callable] = None  # progress lines for a long fetch
        self._levels: dict = {}
        self.ltp_used: dict[int, str] = {}   # token -> the key ltp() was asked for
        self.anchors: dict[int, list] = {}   # token -> [(price, at, what)]: known prices before the day
        self.models: dict[int, dict] = {}    # token -> how a never-traded contract was priced
        self.tried: dict[int, str] = {}      # token -> what was asked of Kite, when it had nothing
        self.before: dict[int, tuple] = {}   # token -> (price, at): its last trade before the day, when that's all there is
        self._prior: dict[int, Optional[tuple]] = {}
        self._row: dict[int, Optional[dict]] = {}
        self.history_errors: dict[int, str] = {}   # token -> what Kite said when asked for the days before
        self.rows: dict[int, dict[datetime, dict]] = {}
        self.keys: dict[int, list[datetime]] = {}
        self.origin: dict[int, str] = {}
        self.errors: dict[int, str] = {}
        self.inverse: dict[int, bool] = {}
        self.symbols: dict[int, str] = {}
        # how far a candle's "up"/"down" tick goes from its open/close
        # toward its high/low: 1 is all the way; less pulls every extreme
        # in (engine.replay's check of a trade only the edge makes)
        self.edge = 1.0
        self._lock = threading.RLock()

    def note(self, token: int, symbol: Optional[str]) -> None:
        if symbol and token not in self.symbols:
            self.symbols[token] = symbol
            self.inverse[token] = moves_against(symbol)

    def anchor(self, token: int, price: float, at: datetime, what: str) -> None:
        """A price the contract is known to have had (a fill), for the model."""
        if price and price > 0:
            self.anchors.setdefault(int(token), []).append((float(price), naive_ist(at), what))

    def _fetch(self, token: int, start: datetime, end: datetime) -> list:
        try:
            return self.source(token, start, end) or []
        except Exception as e:   # expired contract, no session, rate limit after retries
            self.errors.setdefault(token, f"{type(e).__name__}: {e}")
            return []

    def _master(self, token: int) -> Optional[dict]:
        sym = self.symbols.get(token)
        if not (sym and self.lookup):
            return None
        try:
            return self.lookup(sym)
        except Exception as e:
            self.errors.setdefault(token, f"{type(e).__name__}: {e}")
            return None

    def prefetch(self, pairs) -> None:
        """Kite's quote for every (token, "EXCH:SYMBOL") not loaded yet, in
        one request per 400: a strike ladder costs one request, not one
        history lookup per strike."""
        if not self.quote:
            return
        todo = {}
        for tok, key in pairs:
            tok = int(tok)
            if ":" in str(key) and tok not in INDEX_LABELS:
                self._key_of.setdefault(tok, str(key))
                if tok not in self.rows and tok not in self.quoted:
                    todo[str(key)] = tok
        keys = list(todo)
        for i in range(0, len(keys), 400):
            chunk = keys[i:i + 400]
            try:
                got = self.quote(chunk) or {}
            except Exception as e:
                for k in chunk:
                    self.history_errors.setdefault(todo[k], f"{type(e).__name__}: {e}")
                continue
            for k in chunk:
                q = got.get(k) or {}
                ltt = q.get("last_trade_time")
                # Kite gives an empty (1970) trade time for a contract that has never traded
                ltt = naive_ist(ltt) if isinstance(ltt, datetime) and ltt.year >= 2000 else None
                self.quoted[todo[k]] = (float(q.get("last_price") or 0), ltt)

    def _quoted_before_day(self, token: int) -> Optional[tuple[float, datetime]]:
        """(price, at) when Kite's quote says its last trade was before the
        day: then that's the price it showed all day, and there's nothing in
        the day's history to fetch."""
        if not self.quote or token in INDEX_LABELS or self.symbols.get(token) in INDEX_LABELS.values():
            return None
        if token not in self.quoted:
            key = self._key_of.get(token)
            if not key:
                row = self._master(token)
                key = f"{row.get('exchange')}:{row['tradingsymbol']}" if row and row.get("exchange") else None
            if not key:
                return None
            self.prefetch([(token, key)])
        px, ltt = self.quoted.get(token, (0.0, None))
        open_ = datetime.combine(self.day, SESSION_OPEN)
        if px <= 0 or (ltt is not None and ltt >= open_):
            return None   # no price, or it traded on/after the day: its history has the day
        if ltt is None:
            self.never_traded.add(token)
        # never traded: Kite's ticks carry its empty (1970) trade time, which
        # the strangle's stale-price guard reads as "not traded this session"
        # and holds on; a time at the open here would let the replay trade
        # where live held all day
        return px, (ltt or KITE_NEVER_TRADED)

    def load(self, token: int) -> dict[datetime, dict]:
        token = int(token)
        with self._lock:
            if token in self.rows:
                return self.rows[token]
            q = self._quoted_before_day(token)
            if q is not None:
                self.before[token] = q
                self.rows[token], self.keys[token], self.origin[token] = {}, [], "quote"
                return self.rows[token]
            open_, close = datetime.combine(self.day, SESSION_OPEN), datetime.combine(self.day, SESSION_CLOSE)
            got = self._fetch(token, open_, close)
            origin = "kite" if got else "none"
            row = None
            if not got:
                # the token the app holds may not be Kite's: ask by the
                # contract's name, on its own exchange (BFO for SENSEX/BANKEX)
                row = self._master(token)
                alt = int(row["instrument_token"]) if row else None
                if alt and alt != token:
                    got = self._fetch(alt, open_, close)
                    origin = "kite" if got else "none"
                tried = f"token {token}" + (f" and {row.get('exchange', '')}:{row['tradingsymbol']} (token {alt})"
                                            if alt and alt != token else "")
                self.tried[token] = tried + (" — not in Kite's instrument list" if self.lookup and self.symbols.get(token) and row is None else "")
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
            if not rows:
                modelled = self._model(token, row)
                if modelled:
                    rows, origin = modelled, "model"
                elif origin == "none" and self.symbols.get(token) not in INDEX_LABELS.values():
                    # nothing to model it on: it stands at its last trade,
                    # as Kite's own ltp() would answer for it
                    self._row[token] = row
                    prior = self._kite_last(token, row)
                    if prior:
                        self.before[token], origin = (prior[0], prior[1]), "stale"
            self._row[token] = row
            self.rows[token] = rows
            self.keys[token] = sorted(rows)
            self.origin[token] = origin
            return rows

    def last_trade_before_day(self, token: int) -> Optional[tuple[float, datetime]]:
        """(price, at) of its last trade before the day, from Kite's history
        — what Kite's ltp() shows for a contract with no trade yet today."""
        token = int(token)
        with self._lock:
            if token not in self.before:
                self.load(token)
                row = self._row.get(token) or self._master(token)
                got = self._kite_last(token, row)
                self.before[token] = (got[0], got[1]) if got else None
            return self.before[token]

    def _kite_last(self, token: int, row: Optional[dict]) -> Optional[tuple]:
        """Its last trade before the day from Kite's history; failing that
        (nothing in 60 days: BSE's thin futures), Kite's ltp() now — still
        the price it showed that day, as long as it hasn't traded since,
        which Kite's history is asked first."""
        got = self._last_trade(token, row)
        if got or not self.ltp or self.rows.get(token):
            return got   # (one that trades later in the day: today's ltp() is that trade, not the price before it)
        tok = int(row["instrument_token"]) if row else token
        sym = (row or {}).get("tradingsymbol") or self.symbols.get(token)
        ex = (row or {}).get("exchange")
        if not (sym and ex):
            return None
        after = self.day + timedelta(days=1)
        today = datetime.now(IST).date()
        if after > today:
            since = []
        elif self.history:
            since = self._history_quiet(tok, after, today, "day")
        else:
            since = self._fetch_quiet(tok, datetime.combine(after, SESSION_OPEN), datetime.now(IST).replace(tzinfo=None))
        if since:
            return None   # it traded after the day: today's ltp isn't that day's
        try:
            px = self.ltp(f"{ex}:{sym}")
        except Exception as e:
            self.history_errors.setdefault(token, f"{type(e).__name__}: {e}")
            return None
        if not px or px <= 0:
            return None
        self.ltp_used[token] = f"{ex}:{sym}"
        return float(px), datetime.combine(self.day, SESSION_OPEN), "Kite's ltp() (no trade in 60 days)"

    def _model(self, token: int, row: Optional[dict]) -> Optional[dict]:
        """A never-traded option's day, minute by minute, from its index's
        candles: Black-76 at the volatility its latest known price gives
        (a CE's high at the index's high, a PE's at its low)."""
        from app.candles import parse_contract
        from app.geek.optionmath import fwd_implied_vol, fwd_price, years_to
        c = parse_contract(self.symbols.get(token) or "") or {}
        if row:   # Kite's own word on the contract beats what its name says
            e = row.get("expiry")
            c = {"underlying": row.get("name") or c.get("underlying"),
                 "instrument_type": row.get("instrument_type") or c.get("instrument_type"),
                 "strike": row.get("strike") or c.get("strike"),
                 "expiry": (e.date() if isinstance(e, datetime) else e if isinstance(e, date)
                            else date.fromisoformat(str(e)[:10])) if e else c.get("expiry")}
        kind = c.get("instrument_type")
        if kind not in ("CE", "PE") or not c.get("strike") or not c.get("expiry"):
            return None
        idx = INDEX_OF.get(c["underlying"])
        if idx is None:
            return None
        expiry = c["expiry"]
        if expiry < self.day:
            return None
        index = self.load(idx)
        if not index:
            return None
        anchors = list(self.anchors.get(token, []))
        prior = self._last_trade(token, row)
        if prior:
            anchors.append(prior)
        for premium, at, what in sorted(anchors, key=lambda a: a[1], reverse=True):
            level = self._level(idx, at)
            if level is None or level <= 0:
                continue
            k = float(c["strike"])
            vol = fwd_implied_vol(kind, premium, level, k, years_to(expiry, at.replace(tzinfo=IST)))
            if vol is None or not 0.01 <= vol <= 5:
                continue
            out = {}
            for m, ic in index.items():
                t = years_to(expiry, (m + timedelta(seconds=30)).replace(tzinfo=IST))
                px = {f: _tick(fwd_price(kind, ic[f], k, t, vol)) for f in ("open", "high", "low", "close")}
                hi, lo = (px["high"], px["low"]) if kind == "CE" else (px["low"], px["high"])
                out[m] = {"open": px["open"], "high": max(hi, lo), "low": min(hi, lo), "close": px["close"],
                          "volume": 0, "oi": 0}
            self.models[token] = {"index": INDEX_LABELS.get(idx, str(idx)), "iv": round(vol, 4),
                                  "anchor": what, "anchor_price": premium, "anchor_at": at, "level": level}
            return out
        return None

    def _last_trade(self, token: int, row: Optional[dict]) -> Optional[tuple]:
        """Its last 1-minute candle in the week before the day."""
        if token not in self._prior:
            self._prior[token] = self._ask_last_trade(token, row)
        return self._prior[token]

    def _ask_last_trade(self, token: int, row: Optional[dict]) -> Optional[tuple]:
        tok = int(row["instrument_token"]) if row else token
        end = datetime.combine(self.day, time(0)) - timedelta(seconds=1)
        start = datetime.combine(self.day - timedelta(days=7), SESSION_OPEN)
        got = [c for c in self._fetch_quiet(tok, start, end) if minute_of(c["date"]).date() < self.day and c["close"]]
        if got:
            last = max(got, key=lambda c: naive_ist(c["date"]))
            at = minute_of(last["date"]) + timedelta(seconds=59)
            return float(last["close"]), at, f"its last trade, {at:%d %b %H:%M}"
        if not self.history:
            return None
        # thinner than that: Kite's daily candles for 60 days, one light request
        days = [c for c in self._history_quiet(tok, self.day - timedelta(days=60), self.day - timedelta(days=1), "day")
                if naive_ist(c["date"]).date() < self.day and c["close"]]
        if not days:
            return None
        last = max(days, key=lambda c: naive_ist(c["date"]))
        at = datetime.combine(naive_ist(last["date"]).date(), time(15, 29, 59))
        return float(last["close"]), at, f"its last trade, on {at:%d %b} (its close)"

    def _history_quiet(self, token: int, start, end, interval: str) -> list:
        try:
            return self.history(token, start, end, interval) or []
        except Exception as e:
            self.history_errors.setdefault(int(token), f"{type(e).__name__}: {e}")
            return []

    def _fetch_quiet(self, token: int, start: datetime, end: datetime) -> list:
        """History before the day: a failure isn't the day's, but is kept for the notes."""
        try:
            return self.source(token, start, end) or []
        except Exception as e:
            self.history_errors.setdefault(int(token), f"{type(e).__name__}: {e}")
            return []

    def _level(self, idx: int, at: datetime) -> Optional[float]:
        """The index at `at`: from the day's candles, else Kite's."""
        if at.date() == self.day:
            rows = self.load(idx)
            m = minute_of(at)
            c = rows.get(m)
            if c is not None:
                return c["close"]
            prev = self.last_before(idx, m)
            return prev[1]["close"] if prev else (rows[self.keys[idx][0]]["open"] if rows else None)
        key = (idx, at.replace(second=0, microsecond=0))
        if key not in self._levels:
            got = self._fetch_quiet(idx, at - timedelta(minutes=30), at)
            got = [c for c in got if naive_ist(c["date"]) <= at]
            self._levels[key] = float(max(got, key=lambda c: naive_ist(c["date"]))["close"]) if got else None
        return self._levels[key]

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
            f = field_for(phase, self.inverse.get(token, False))
            if self.edge < 1 and phase in ("up", "down"):
                body = max(c["open"], c["close"]) if f == "high" else min(c["open"], c["close"])
                return body + self.edge * (c[f] - body), now
            return c[f], now
        prev = self.last_before(token, minute)
        if prev is None:   # no trade yet today: Kite's last trade before it
            if self.symbols.get(token) in INDEX_LABELS.values():
                return None
            return self.last_trade_before_day(token)
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
        self.moment: Optional[datetime] = None   # an app's own moment inside the minute (engine.replay)

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
        return self.moment or self.minute + timedelta(seconds=STEP_SECONDS[self.step])

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
        # Expiries that were listed on the replayed day but may be gone
        # from Kite's list now (underlying -> {date}), from the contracts
        # the app stored; and the ones a strategy asked for that Kite no
        # longer lists (underlying, expiry): the replay can't trade them.
        self.known_expiries: dict[str, set] = {}
        self.gone: set[tuple[str, date]] = set()
        # (token, minute) -> the price the app itself filled that contract
        # at in that minute (a day check): what Kite really quoted then,
        # where the candle only says where the minute went
        self.app_quotes: dict[tuple[int, datetime], float] = {}
        # minute -> the strikes the app picked by premium in it ({symbol,
        # underlying, type, target}); the ones the replay followed it to
        # (keyed (HH:MM, symbol)); and, while that's being checked, a moment
        # of the minute: every price at that fraction of its candle's range
        self.app_picks: dict[datetime, list[dict]] = {}
        self.followed: dict[tuple, dict] = {}
        self.moment: Optional[float] = None
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
        pairs = []
        for k in keys:
            tok = self._token(k)
            if tok is not None:
                self.book.note(tok, self._symbol(tok, str(k)))
                pairs.append((tok, str(k)))
        self.book.prefetch(pairs)
        need = {t for t, _ in pairs if t not in self.book.rows and self.book._quoted_before_day(t) is None}
        if len(need) > 3 and self.book.say:
            self.book.say(f"      fetching {len(need)} contracts' 1-minute history from Kite "
                          f"(~{len(need) * 0.35:.0f}s at Kite's 3 a second; once each)…")
        for k in keys:
            tok = self._token(k)
            if tok is None:
                continue
            self.book.note(tok, self._symbol(tok, str(k)))
            got = self.book.price(tok, self.clock.minute, self.clock.phase, self.clock.now)
            c = self.book.candle(tok, self.clock.minute) if self.moment is not None else None
            if c is not None:
                # the index this far up its range, a CE as far up its own, a PE as far down
                span = (c["high"] - c["low"]) * self.moment
                got = (c["high"] - span if self.book.inverse.get(tok) else c["low"] + span, self.clock.now)
            elif self.moment is None and (tok, self.clock.minute) in self.app_quotes:
                got = (self.app_quotes[(tok, self.clock.minute)], self.clock.now)
            if got is None:
                continue
            out[str(k)] = (tok, got[0], got[1])
        return out

    def ltp(self, keys):
        return {k: {"instrument_token": tok, "last_price": p} for k, (tok, p, _) in self._priced(keys).items()}

    async def follow_app(self, pick_by_premium, resolver, pick, underlying, selector, option_type, target, args, kwargs):
        """The strike the app picked by premium in this minute, in place of
        `pick` (the replay's own), when it was a pick some moment of the
        minute could have made: the app's own target premium, with the
        index and every contract at the same point of its candle's range.
        A near-tie the candle can't settle is the app's call; a strike no
        moment explains is left to show as a difference."""
        picks = [p for p in self.app_picks.get(self.clock.minute, ())
                 if p["type"] == option_type.upper() and p["underlying"] == underlying]
        if not picks or any(p["symbol"] == pick.tradingsymbol for p in picks):
            return pick
        for p in picks:
            for i in range(MOMENTS + 1):
                self.moment = i / MOMENTS
                try:
                    got = await pick_by_premium(resolver, underlying, selector, option_type, p["target"], *args, **kwargs)
                except Exception:
                    got = None
                finally:
                    self.moment = None
                if got is not None and got.tradingsymbol == p["symbol"]:
                    px = (self.ltp([got.key]).get(got.key) or {}).get("last_price", got.last_price)
                    minute = f"{self.clock.minute:%H:%M}"
                    self.followed[(minute, p["symbol"])] = {
                        "minute": minute, "app": p["symbol"], "replay": pick.tradingsymbol,
                        "target": round(p["target"], 2), "replay_target": round(float(target), 2),
                        "moment": round(i / MOMENTS, 2)}
                    return dataclasses.replace(got, last_price=px)
        return pick

    def quote(self, keys):
        out = {}
        m = self.clock.minute
        for k, (tok, p, traded) in self._priced(keys).items():
            tick = 0.05
            # a book only around a price from the day: a contract with no
            # trade yet that day has nothing known to quote it at (its last
            # trade can be days old), so none, as a strike search should see
            today = traded is not None and naive_ist(traded).date() == self.clock.day
            out[k] = {
                "instrument_token": tok, "last_price": p, "last_trade_time": traded,
                "volume": self.book.day_volume(tok, m), "oi": self.book.oi(tok, m),
                "depth": {"buy": [{"price": round(max(0.05, p - tick * (i + 1)), 2), "quantity": 1800, "orders": 6} for i in range(5)],
                          "sell": [{"price": round(p + tick * (i + 1), 2), "quantity": 1800, "orders": 6} for i in range(5)]}
                if today else {"buy": [], "sell": []},
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

    async def resolve_expiry(self, underlying, selector, exchange=None, reference_date=None):
        # "THIS_WEEK" on the replayed day means that day's nearest expiry,
        # even if it has expired since and Kite no longer lists it: resolve
        # against what was listed then, and refuse (rather than quietly
        # trading next week's contracts) when it's gone
        listed = await self.list_expiries(underlying, exchange)
        then = set(listed) | {e for e in kite.known_expiries.get(underlying, ()) if e >= clock.day}
        if not then:
            raise ValueError(f"No option expiries found for {underlying!r}")
        got = self._resolve_from_list(sorted(then), selector, reference_date)
        if got not in listed:
            kite.gone.add((underlying, got))
            raise ValueError(f"{underlying}'s {got:%d %b} contracts have expired since {clock.day:%d %b}: "
                             "Kite no longer lists them or serves their prices")
        return got
    swap(R.OptionsResolver, "resolve_expiry", resolve_expiry)
    pick_by_premium = R.OptionsResolver.get_leg_by_premium

    async def get_leg_by_premium(self, underlying, expiry_selector, option_type, target_price, *args, **kwargs):
        # a near-tie between two strikes the app settled one way: see follow_app
        leg = await pick_by_premium(self, underlying, expiry_selector, option_type, target_price, *args, **kwargs)
        return await kite.follow_app(pick_by_premium, self, leg, underlying, expiry_selector, option_type, target_price,
                                     args, kwargs)
    swap(R.OptionsResolver, "get_leg_by_premium", get_leg_by_premium)
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


def _brief(args, kwargs) -> str:
    parts = [a if isinstance(a, (int, str)) else (f"{a:%d %b %H:%M}" if isinstance(a, datetime) else str(a)) for a in args[:4]]
    parts += [f"{k}={v}" for k, v in kwargs.items()]
    return "(" + ", ".join(str(p) for p in parts)[:120] + ")"


class KiteGate:
    """A real KiteConnect for the repair: its instrument masters fetched
    once each, and every request spaced to Kite's ~3 a second and retried
    on its rate limit."""

    def __init__(self, kite, min_gap: float = 0.35, attempts: int = 5, log: Optional[Callable] = None):
        self.kite = kite
        self.min_gap, self.attempts = min_gap, attempts
        self.log = log            # verbose: one line per request, as it's made
        self.requests = 0
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
            t0 = _time.monotonic()
            self.requests += 1
            try:
                got = fn(*args, **kwargs)
                if self.log:
                    n = len(got) if isinstance(got, (list, dict)) else "-"
                    self.log(f"    kite #{self.requests} {getattr(fn, '__name__', fn)}{_brief(args, kwargs)} → {n} "
                             f"({_time.monotonic() - t0:.1f}s)")
                return got
            except Exception as e:
                if self.log:
                    self.log(f"    kite #{self.requests} {getattr(fn, '__name__', fn)}{_brief(args, kwargs)} → "
                             f"{type(e).__name__}: {e}")
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


def _tick(p: float) -> float:
    return round(max(0.05, round(p / 0.05) * 0.05), 2)


def kite_minute_source(gate: KiteGate) -> Callable:
    """A MinuteBook source over Kite: one 1-minute request per instrument
    for the day, with open interest where Kite has it; asked again without
    it only if that fails (an empty answer is the same either way)."""
    def fetch(token: int, start: datetime, end: datetime) -> list[dict]:
        try:
            return gate.historical_data(token, start, end, "minute", oi=True) or []
        except Exception:
            return gate.historical_data(token, start, end, "minute") or []
    return fetch


def kite_quote(gate: KiteGate) -> Callable:
    """["EXCH:SYMBOL", ...] -> Kite's quote for each, in one request."""
    def quote(keys: list) -> dict:
        return gate.call(gate.kite.quote, list(keys)) or {}
    return quote


def kite_ltp(gate: KiteGate) -> Callable:
    """"EXCH:SYMBOL" -> Kite's last price for it now."""
    def ltp(key: str) -> Optional[float]:
        got = gate.call(gate.kite.ltp, [key]) or {}
        return (got.get(key) or {}).get("last_price")
    return ltp


def kite_lookup(gate: KiteGate) -> Callable:
    """tradingsymbol -> its row in Kite's instrument master, looked up on
    the contract's own exchange (BFO for SENSEX/BANKEX, else NFO)."""
    from app.candles import parse_contract
    from app.options.resolver import options_exchange_for
    by: dict = {}

    def lookup(symbol: str) -> Optional[dict]:
        c = parse_contract(symbol)
        if not c:
            return None
        ex = options_exchange_for(c["underlying"])
        if ex not in by:
            by[ex] = {r["tradingsymbol"]: {**r, "exchange": r.get("exchange") or ex} for r in gate.instruments(ex)}
        return by[ex].get(symbol)
    return lookup
