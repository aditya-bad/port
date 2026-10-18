"""A simulated market for strategy tests.

FakeKite answers what the real OptionsResolver asks Kite for
(instruments, ltp, quote, historical_data) from a synthetic options
market, so the REAL resolver (strike-by-premium search, liquidity
tie-break, expiry rotation, synthetic futures) runs unchanged. Sim then
drives a strategy through the REAL DeploymentRunner and the real fill
engine on the test database, one tick at a time, at simulated IST times.

    sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", config)
    await sim.tick("10:00")                       # today, spot unchanged
    await sim.tick("11:30", spot=25120)
    sim.open_positions(), sim.fills(), sim.events()
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from app.db import queries
from app.deployments.runner import DeploymentRunner
from app.strategies.registry import get_strategy_class

INDEX = {   # underlying -> (spot token, spot label, exchange, F&O exchange, lot, step, spot)
    "NIFTY": (256265, "NIFTY 50", "NSE", "NFO", 75, 50, 25000.0),
    "BANKNIFTY": (260105, "NIFTY BANK", "NSE", "NFO", 35, 100, 55000.0),
    "SENSEX": (265, "SENSEX", "BSE", "BFO", 20, 100, 82000.0),
    "BANKEX": (274441, "BANKEX", "BSE", "BFO", 30, 100, 63000.0),
}
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _expiries(today: date) -> list[date]:
    """Weekly (Tuesday) expiries for 9 weeks plus the last Tuesday of the
    next three months, so THIS/NEXT_WEEK and THIS/NEXT_MONTH all exist."""
    out = set()
    d = today
    while d.weekday() != 1:
        d += timedelta(days=1)
    for _ in range(9):
        out.add(d)
        d += timedelta(days=7)
    for m in range(4):
        y, mo = today.year + (today.month - 1 + m) // 12, (today.month - 1 + m) % 12 + 1
        last = date(y + (mo // 12), mo % 12 + 1, 1) - timedelta(days=1)
        while last.weekday() != 1:
            last -= timedelta(days=1)
        if last >= today:
            out.add(last)
    return sorted(out)


class Market:
    def __init__(self, today: date, vol: float = 0.16, spots: dict | None = None):
        self.today = today
        self.now = datetime.combine(today, datetime.min.time()).replace(hour=9, minute=15)
        self.vol = vol
        self.spot = {k: v[6] for k, v in INDEX.items()}
        if spots:
            self.spot.update(spots)
        self.rows: dict[str, list[dict]] = {"NFO": [], "BFO": []}
        self.by_token: dict[int, dict] = {}
        self.by_key: dict[str, dict] = {}
        tok = 10_000_000
        for name, (_, _, _, fo, lot, step, s0) in INDEX.items():
            for exp in _expiries(today):
                code = f"{exp:%y}{MONTHS[exp.month - 1]}"
                for i in range(-60, 61):
                    k = round(s0 / step) * step + i * step
                    for typ in ("CE", "PE"):
                        tok += 1
                        self._add(fo, {"instrument_token": tok, "tradingsymbol": f"{name}{code}{exp.day:02d}{int(k)}{typ}",
                                       "name": name, "expiry": exp, "strike": float(k), "instrument_type": typ,
                                       "lot_size": lot, "tick_size": 0.05, "exchange": fo, "segment": f"{fo}-OPT"})
            for exp in sorted({e for e in _expiries(today) if (e + timedelta(days=7)).month != e.month}):
                tok += 1
                self._add(fo, {"instrument_token": tok, "tradingsymbol": f"{name}{exp:%y}{MONTHS[exp.month - 1]}FUT",
                               "name": name, "expiry": exp, "strike": 0.0, "instrument_type": "FUT",
                               "lot_size": lot, "tick_size": 0.05, "exchange": fo, "segment": f"{fo}-FUT"})

    def _add(self, ex, row):
        self.rows[ex].append(row)
        self.by_token[row["instrument_token"]] = row
        self.by_key[f"{ex}:{row['tradingsymbol']}"] = row

    def price_of(self, row: dict) -> float:
        s = self.spot[row["name"]]
        t = max((datetime.combine(row["expiry"], datetime.min.time()).replace(hour=15, minute=30) - self.now).total_seconds(), 1800) / (365 * 86400)
        if row["instrument_type"] == "FUT":
            return round(s * (1 + 0.065 * t), 2)
        k = row["strike"]
        sd = s * self.vol * math.sqrt(t)
        intrinsic = max(0.0, s - k) if row["instrument_type"] == "CE" else max(0.0, k - s)
        tv = 0.4 * sd * math.exp(-abs(k - s) / (1.1 * sd))
        return max(0.05, round((intrinsic + tv) * 20) / 20)

    def spot_price(self, key: str) -> float | None:
        for name, (_, label, ex, *_r) in INDEX.items():
            if key in (f"{ex}:{label}", f"{ex}:{name}"):
                return self.spot[name]
        return None


class FakeKite:
    def __init__(self, market: Market):
        self.m = market
        self.calls: list[str] = []

    def instruments(self, exchange):
        self.calls.append(f"instruments {exchange}")
        return list(self.m.rows.get(exchange, []))

    def _one(self, key):
        row = self.m.by_key.get(key)
        if row is not None:
            return {"instrument_token": row["instrument_token"], "last_price": self.m.price_of(row)}
        sp = self.m.spot_price(key)
        if sp is not None:
            return {"instrument_token": next(v[0] for k, v in INDEX.items() if key.endswith(v[1]) or key.endswith(k)), "last_price": sp}
        raise KeyError(key)

    def ltp(self, keys):
        keys = [keys] if isinstance(keys, str) else list(keys)
        self.calls.append(f"ltp {len(keys)}")
        return {k: self._one(k) for k in keys}

    def quote(self, keys):
        keys = [keys] if isinstance(keys, str) else list(keys)
        out = {}
        for k in keys:
            q = self._one(k)
            p = q["last_price"]
            q.update({"oi": 500000, "volume": 100000,
                      "depth": {"buy": [{"price": round(max(0.05, p - 0.05 * (i + 1)), 2), "quantity": 1500, "orders": 5} for i in range(5)],
                                "sell": [{"price": round(p + 0.05 * (i + 1), 2), "quantity": 1500, "orders": 5} for i in range(5)]}})
            out[k] = q
        return out

    def historical_data(self, token, start, end, interval):
        row = self.m.by_token.get(int(token))
        p = self.m.price_of(row) if row else next((self.m.spot[k] for k, v in INDEX.items() if v[0] == int(token)), 100.0)
        step = {"minute": 1, "5minute": 5, "15minute": 15, "60minute": 60}.get(interval, 1)
        out, t = [], start
        while t < end:
            out.append({"date": t, "open": p, "high": p, "low": p, "close": p, "volume": 0})
            t += timedelta(minutes=step)
        return out


class FakeDispatcher:
    def __init__(self, market: Market):
        self.m = market
        self.last_prices: dict[int, float] = {}
        self.last_price_times: dict[int, datetime] = {}
        self.last_depths: dict = {}
        self.token_labels = {v[0]: v[1] for v in INDEX.values()}
        self.subscribed: set[int] = set(self.token_labels)
        self.access_token, self.api_key, self.connected = "sim-token", "sim-key", True
        self.status = {"needs_login": False}

    def add_instruments(self, items):
        for it in items:
            self.subscribed.add(int(it["instrument_token"]))

    def release_instruments(self, tokens):
        pass   # keep pricing them: a leg may still be open

    def publish(self):
        for tok in list(self.subscribed) + list(self._index_tokens()):
            row = self.m.by_token.get(tok)
            if row is not None:
                self.last_prices[tok] = self.m.price_of(row)
            else:
                name = next((k for k, v in INDEX.items() if v[0] == tok), None)
                if name is None:
                    continue
                self.last_prices[tok] = self.m.spot[name]
            self.last_price_times[tok] = self.m.now

    def _index_tokens(self):
        return [v[0] for v in INDEX.values()]


def install(monkeypatch, market: Market) -> FakeKite:
    """Route every Kite REST call the app makes to a FakeKite."""
    import app.options.resolver as R
    import app.options.client as C
    kite = FakeKite(market)
    monkeypatch.setattr(R, "get_kite_connect", lambda d: kite)
    monkeypatch.setattr(C, "get_kite_connect", lambda d: kite)
    monkeypatch.setattr(R, "_KITE_CALL_MIN_INTERVAL", 0.0)
    # Expiry rotation (THIS_WEEK, ...) is relative to date.today(); pin it
    # to the simulated day so results don't depend on when tests run.
    orig = R.OptionsResolver._resolve_from_list
    monkeypatch.setattr(R.OptionsResolver, "_resolve_from_list",
                        lambda self, expiries, selector, reference_date=None:
                        orig(self, expiries, selector, reference_date or market.today))
    R._INSTRUMENT_CACHE.clear()
    return kite


# The default simulated trading day: a Wednesday, so THIS_WEEK is the
# following Tuesday (6 days out) and premiums are the same on every run.
SIM_DAY = date(2026, 10, 14)


def ist(s: str, today: date | None = None) -> datetime:
    """'10:00' (on `today`, default SIM_DAY) or '2026-10-15 10:00' -> naive
    IST datetime, the form Kite ticks carry."""
    if len(s) <= 5:
        return datetime.combine(today or SIM_DAY, datetime.strptime(s, "%H:%M").time())
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


class Sim:
    def __init__(self):
        self.pool = self.runner = self.strategy = self.market = self.dispatcher = None
        self.underlying = "NIFTY"

    @classmethod
    async def create(cls, pool, monkeypatch, strategy_name: str, config: dict, *, mode: str = "intraday",
                     capital: float = 500000, today: date | None = None, underlying: str = "NIFTY",
                     spot: float | None = None, vol: float = 0.16, name: str | None = None):
        sim = cls()
        sim.pool, sim.underlying = pool, underlying
        sim.market = Market(today or SIM_DAY, vol=vol, spots={underlying: spot} if spot else None)
        sim.kite = install(monkeypatch, sim.market)
        sim.dispatcher = FakeDispatcher(sim.market)
        cfg = {"instrument_tokens": [INDEX[underlying][0]], **config}
        row = await queries.create_deployment(pool, name or f"sim {strategy_name}", strategy_name, mode, capital, cfg)
        sim.dep_id = row["id"]
        await queries.set_status(pool, row["id"], "active")
        row = await queries.get_deployment(pool, row["id"])
        sim.strategy = get_strategy_class(strategy_name)()
        sim.runner = DeploymentRunner(row, pool, None, sim.dispatcher, sim.strategy)
        sim.runner._is_stale_tick = lambda t: False   # simulated times are in the past; the real check has its own tests
        sim.dispatcher.publish()
        await sim.runner._reload_positions()
        await sim.strategy.on_start(sim.runner)
        return sim

    async def tick(self, at: str | datetime, spot: float | None = None):
        when = ist(at, self.market.today) if isinstance(at, str) else at
        self.market.now = when
        if spot is not None:
            self.market.spot[self.underlying] = spot
        self.dispatcher.publish()
        tok = INDEX[self.underlying][0]
        t = {"instrument_token": tok, "last_price": self.market.spot[self.underlying], "exchange_timestamp": when}
        await self.runner._intraday_guard(t)
        await self.strategy.on_tick(self.runner, t)

    async def walk(self, start: str, end: str, every_min: int = 1, spot_fn=None):
        """Tick every `every_min` minutes from start to end (same day)."""
        t, stop = ist(start, self.market.today), ist(end, self.market.today)
        while t <= stop:
            await self.tick(t, spot=spot_fn(t) if spot_fn else None)
            t += timedelta(minutes=every_min)

    async def open_positions(self):
        return [dict(r) for r in await queries.list_open_positions(self.pool, self.dep_id)]

    async def all_positions(self):
        return [dict(r) for r in await self.pool.fetch(
            "SELECT * FROM positions WHERE deployment_id = $1 ORDER BY opened_at, symbol", self.dep_id)]

    async def fills(self):
        return [dict(r) for r in await self.pool.fetch(
            "SELECT * FROM position_lots WHERE deployment_id = $1 ORDER BY executed_at, id", self.dep_id)]

    async def events(self, kind: str | None = None):
        rows = await self.pool.fetch(
            "SELECT event_type, message FROM deployment_events WHERE deployment_id = $1 ORDER BY created_at", self.dep_id)
        return [dict(r) for r in rows if kind is None or r["event_type"] == kind]

    async def restart(self):
        """Simulate an app restart: a fresh strategy instance resumed from the database."""
        row = await queries.get_deployment(self.pool, self.dep_id)
        self.strategy = type(self.strategy)()
        self.runner = DeploymentRunner(row, self.pool, None, self.dispatcher, self.strategy)
        self.runner._is_stale_tick = lambda t: False
        await self.runner._reload_positions()
        await self.strategy.on_start(self.runner)


async def leg_prices(sim, spot: float | None = None) -> dict[str, float]:
    """{symbol: price} of every open leg, at `spot` if given (market unchanged)."""
    legs = await sim.open_positions()
    saved = sim.market.spot[sim.underlying]
    if spot is not None:
        sim.market.spot[sim.underlying] = spot
    try:
        return {p["symbol"]: sim.market.price_of(sim.market.by_token[int(p["instrument_token"])]) for p in legs}
    finally:
        sim.market.spot[sim.underlying] = saved


async def find_spot(sim, pred, step: float, limit: int = 300) -> float:
    """First spot, moving from the current one by `step`, where
    pred({symbol: price}) holds."""
    s = sim.market.spot[sim.underlying]
    for _ in range(limit):
        s += step
        if pred(await leg_prices(sim, s)):
            return s
    raise AssertionError("no spot satisfies the condition")


def side_sum(prices: dict[str, float], side: str) -> float:
    return sum(v for k, v in prices.items() if k.endswith(side))
