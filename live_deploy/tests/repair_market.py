"""1-minute candles for repair tests, from the simulated market in
tests/sim.py: the index follows `path(minute) -> (open, high, low, close)`,
and every option's candle is its simulated price at each of those four
index levels (a CE's high at the index high, a PE's high at the index
low), so the candles keep exactly the pairing the repair relies on."""

from __future__ import annotations

from datetime import datetime, timedelta

from tests.sim import INDEX

VIX = 264969


def flat(spot: float, wiggle: float = 4.0):
    """Every minute: open = close = spot, ±wiggle either way."""
    return lambda m: (spot, spot + wiggle, spot - wiggle, spot)


def candles_source(sim, path, vix: float = 14.0, overrides: dict | None = None):
    """A MinuteBook source over `sim`'s market. `overrides`:
    {(token, "HH:MM"): (o, h, l, c)} for a hand-made candle."""
    market = sim.market
    tok_of = {v[0]: k for k, v in INDEX.items()}
    overrides = overrides or {}

    def source(token, start, end):
        out, m = [], start
        saved_now, saved_spot = market.now, dict(market.spot)
        try:
            while m < end:
                key = (int(token), f"{m:%H:%M}")
                if key in overrides:
                    o, h, l, c = overrides[key]
                elif int(token) == VIX:
                    o, h, l, c = vix, vix + 0.1, vix - 0.1, vix
                else:
                    o, h, l, c = path(m)
                    if int(token) not in tok_of:
                        row = market.by_token.get(int(token))
                        thin = market.thin.get(row["tradingsymbol"]) if row is not None else None
                        if row is None or (thin is not None and thin[1] < m):   # no trade since its last one
                            m += timedelta(minutes=1)
                            continue
                        market.now = m
                        name = row["name"]

                        def at(s):
                            market.spot[name] = s
                            return market.price_of(row)
                        po, ph, pl, pc = at(o), at(h), at(l), at(c)
                        if row["instrument_type"] == "PE":
                            ph, pl = pl, ph
                        o, h, l, c = po, max(ph, po, pc), min(pl, po, pc), pc
                out.append({"date": m, "open": o, "high": h, "low": l, "close": c, "volume": 100, "oi": 600000})
                m += timedelta(minutes=1)
        finally:
            market.now, market.spot = saved_now, saved_spot
        return out
    return source


def ist_minute(sim, hhmm: str) -> datetime:
    return datetime.combine(sim.market.today, datetime.strptime(hhmm, "%H:%M").time())
