"""Benchmarks behind docs/linck-brokers-languages-load.html (not part of
the gate: the file name doesn't match test_*.py, so it only runs when
named). Each prints one line per case:

    .venv/bin/python -m pytest -q -s -p no:cacheprovider tests/bench/bench_runner.py

- per_tick:   today's runner, a tick that doesn't trade: the whole
              process_tick, and the strategy's own on_tick inside it
- trade_tick: a tick that trades (fake Kite answers at once, so this is
              our own cost: resolving legs, the fill engine, the database)
- kite_calls: how many Kite REST calls each trade makes first (each is
              spaced >= 0.35 s process-wide in production)
- fanout:     today's in-process broadcaster: every runner sees every tick
- zygote:     memory of strategy processes forked from a preloaded parent
"""

import asyncio
import collections
import gc
import math
import os
import random
import statistics
import time
from datetime import timedelta

import pytest

from tests.sim import Sim, ist
from tests.strategies.helpers import cfg

POSITIONAL = {"strangle_monthly_v2", "trend_credit_spread"}


def ms(xs, p):
    return sorted(xs)[max(0, int(len(xs) * p) - 1)] * 1e3


@pytest.mark.parametrize("strategy", ["intraday_dtt_simple", "strangle_monthly_v2", "pivot_supertrend_options"])
def test_per_tick(strategy, pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy),
                               mode="positional" if strategy in POSITIONAL else "intraday", spot=25000)
        d = sim.market.today
        await sim.walk(f"{d} 09:15", f"{d} 10:30", every_min=1, spot_fn=lambda t: 25000 + 60 * math.sin(t.minute / 7))
        orig, inner = sim.strategy.on_tick, []

        async def timed(runner, t):
            a = time.perf_counter()
            r = await orig(runner, t)
            inner.append(time.perf_counter() - a)
            return r
        sim.strategy.on_tick = timed
        t0, outer = ist(f"{d} 10:31", d), []
        for i in range(900):
            a = time.perf_counter()
            await sim.tick(t0 + timedelta(seconds=i), spot=25000 + 8 * math.sin(i / 30))
            outer.append(time.perf_counter() - a)
        print(f"\nper_tick {strategy}: runner p50={ms(outer, .5):.3f}ms p99={ms(outer, .99):.3f}ms "
              f"mean={statistics.mean(outer) * 1e3:.3f}ms | strategy p50={ms(inner, .5):.3f}ms p99={ms(inner, .99):.3f}ms")
    pool_run(go)


@pytest.mark.parametrize("strategy", ["intraday_dtt_simple", "strangle_monthly_v2", "pivot_supertrend_options", "trend_credit_spread"])
def test_trade_tick_and_kite_calls(strategy, pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy),
                               mode="positional" if strategy in POSITIONAL else "intraday", spot=25000)
        calls, k = collections.Counter(), sim.kite
        for name in ("instruments", "ltp", "quote", "historical_data"):
            f = getattr(k, name)

            def wrap(*a, _f=f, _n=name, **kw):
                calls[_n] += 1
                return _f(*a, **kw)
            setattr(k, name, wrap)
        d = sim.market.today
        t, out = ist(f"{d} 09:15", d), []
        while t <= ist(f"{d} 15:20", d):
            before = sum(calls.values())
            n0 = await pool.fetchval("SELECT count(*) FROM position_lots WHERE deployment_id=$1", sim.dep_id)
            a = time.perf_counter()
            await sim.tick(t, spot=25000 + 250 * math.sin((t.hour * 60 + t.minute) / 45))
            dt = time.perf_counter() - a
            n1 = await pool.fetchval("SELECT count(*) FROM position_lots WHERE deployment_id=$1", sim.dep_id)
            if n1 > n0:
                out.append(f"{t:%H:%M} {n1 - n0} fills: {dt * 1e3:.1f} ms, {sum(calls.values()) - before} Kite calls")
            t += timedelta(minutes=1)
        print(f"\ntrade_tick {strategy}: " + "; ".join(out[:4]))
    pool_run(go)


def test_fanout():
    from app.broadcaster import Broadcaster

    async def bench(n_deps, batch, batches):
        b, toks, seen = Broadcaster(max_queue_size=10 ** 6), list(range(1, 3001)), [0]

        async def runner(mine):
            q = await b.subscribe()
            while True:
                ticks = await q.get()
                if ticks is None:
                    return
                for t in ticks:
                    if t["instrument_token"] in mine:
                        t["last_price"] * 1.0001
                seen[0] += 1
        tasks = [asyncio.create_task(runner(set(random.sample(toks, 6)))) for _ in range(n_deps)]
        await asyncio.sleep(0.05)
        payloads = [[{"instrument_token": random.choice(toks), "last_price": 100.0 + i} for i in range(batch)] for _ in range(50)]
        t0 = time.perf_counter()
        for i in range(batches):
            await b.broadcast(payloads[i % 50])
            if i % 20 == 0:
                await asyncio.sleep(0)
        while seen[0] < n_deps * batches:
            await asyncio.sleep(0.001)
        dt = time.perf_counter() - t0
        await b.broadcast(None)
        await asyncio.gather(*tasks)
        print(f"\nfanout {n_deps} deployments: {batches * batch / dt:,.0f} ticks/s through all of them, one core")

    async def main():
        for n in (10, 50, 200):
            await bench(n, 200, 300)
    asyncio.run(main())


def test_zygote():
    import app.strategies.registry as R
    R.list_strategies()
    gc.freeze()
    kids = []
    for _ in range(20):
        pid = os.fork()
        if pid == 0:
            from app.strategies.registry import get_strategy_class
            get_strategy_class("strangle_monthly_v2")()
            _ = [float(i) for i in range(20000)]
            time.sleep(30)
            os._exit(0)
        kids.append(pid)
    time.sleep(3)

    def roll(pid):
        d = {}
        for line in open(f"/proc/{pid}/smaps_rollup"):
            k, *v = line.split()
            d[k.rstrip(":")] = int(v[0]) / 1024 if v and v[0].isdigit() else 0
        return d
    rs = [roll(p) for p in kids]
    avg = lambda k: sum(r[k] for r in rs) / len(rs)  # noqa: E731
    print(f"\nzygote: parent {roll(os.getpid())['Rss']:.1f} MB; per forked strategy: RSS {avg('Rss'):.1f} MB, "
          f"PSS {avg('Pss'):.1f} MB, private {avg('Private_Clean') + avg('Private_Dirty'):.1f} MB")
    for p in kids:
        os.kill(p, 9)
