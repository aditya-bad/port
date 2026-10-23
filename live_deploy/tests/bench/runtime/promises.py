"""Two more promises of the Linck runtime, shown working (docs/linck-how-it-works.html).

supervisor.py shows walk, branch, crash recovery and quarantine. This adds:

  latest    A strategy that takes 40 ms per decision, fed a tick every 5 ms.
            With a queue (what today's app does), it decides on older and
            older prices and the backlog grows. With conflation (one slot
            per instrument, one batch in flight), every decision is on the
            newest price, and the ticks it skipped are counted.
  fence     A process the supervisor gave up on (a "zombie", say after a
            network blip) keeps running and keeps sending orders, while its
            replacement trades from the last snapshot. The order service
            refuses everything from the old epoch. Without the epoch check,
            the zombie, ahead of its replacement and trading on a late feed,
            gets its orders placed, and the replacement's orders with the
            same ids are refused as duplicates: the wrong process trades.

    python3 promises.py           (after: cd gostrat && go build -o ../gostrat_bin .)
"""

import collections
import random
import statistics
import threading
import time

from supervisor import DEP, N, PARAMS, Proc, prices

FEED_S, EVERY_MS, WORK_MS, DRAIN_S = 3.0, 5, 40, 1.0


def latest(lang, mode):
    """Feed a slow strategy faster than it can decide; return what each decision saw."""
    proc = Proc(lang, {**PARAMS, "work_ms": WORK_MS})
    lock, ready = threading.Lock(), threading.Event()
    queue, slot, newest = collections.deque(), {}, [0]
    skipped = [0]

    def feeder():
        rnd, px, n = random.Random(3), 25000.0, 0
        end = time.perf_counter() + FEED_S
        while time.perf_counter() < end:
            px += rnd.gauss(0, 2)
            n += 1
            tick = {"px": round(px * 20) / 20, "n": n, "t": time.perf_counter()}
            with lock:
                newest[0] = n
                if mode == "queue":
                    queue.append(tick)          # every tick, in order, behind the ones before
                else:
                    if 1 in slot:
                        skipped[0] += 1         # overwritten before it was sent: counted, not queued
                    slot[1] = tick
            ready.set()
            time.sleep(EVERY_MS / 1000)

    feed = threading.Thread(target=feeder)
    t0 = time.perf_counter()
    feed.start()
    ages, behind, series, seq = [], [], [], 0
    while time.perf_counter() - t0 < FEED_S + DRAIN_S:
        with lock:
            if mode == "queue":
                tick = queue.popleft() if queue else None
            else:
                tick = slot.pop(1, None)
            ready.clear()
        if tick is None:
            ready.wait(0.05)
            continue
        seq += 1
        proc.batch({"type": "batch", "seq": seq, "now": int(time.time() * 1000),
                    "ticks": [{"id": 1, "px": tick["px"]}], "fills": []})
        done_at = time.perf_counter()
        with lock:
            ages.append((done_at - tick["t"]) * 1000)   # how old the price was when the decision came back
            behind.append(newest[0] - tick["n"])        # how many newer ticks existed by then
            series.append((round(done_at - t0, 3), round(ages[-1], 1)))
    feed.join()
    left = len(queue)
    proc.close()
    return {"decisions": seq, "ticks": newest[0], "age_p50": statistics.median(ages), "age_max": max(ages),
            "behind_max": max(behind), "backlog": left, "skipped": skipped[0], "series": series}


class FencedOrderService:
    """The order service with fencing: an intent from an epoch older than the
    deployment's current one is refused before anything else is looked at."""

    def __init__(self, fence=True):
        self.fence, self.epoch, self.orders = fence, {DEP: 1}, {}
        self.fenced = self.dupes = 0

    def place(self, cmd, epoch):
        if self.fence and epoch < self.epoch[DEP]:
            self.fenced += 1
            return "fenced"
        if cmd["intent_id"] in self.orders:
            self.dupes += 1
            return "duplicate"
        self.orders[cmd["intent_id"]] = {**cmd, "epoch": epoch}
        return "placed"


def fence(lang, fenced=True, lag=3):
    px = prices()

    def batch(seq, lag=0):
        p = px[max(seq - lag, 1)]
        return {"type": "batch", "seq": seq, "now": 1_700_000_000_000 + seq * 60_000,
                "ticks": [{"id": 1, "px": p}, {"id": 2, "px": p * 0.01}], "fills": []}

    oms = FencedOrderService(fenced)
    old = Proc(lang, PARAMS, epoch=1)
    for s in range(1, 301):
        for cmd in old.batch(batch(s))["commands"]:
            oms.place(cmd, 1)
    snap = old.snapshot(300)
    # batch 301: the supervisor stops hearing from the old process, its lease
    # expires, and the deployment moves on with epoch 2 from snapshot 300.
    # The old process isn't dead: it keeps running on a late copy of the feed
    # (prices `lag` minutes old) and keeps sending intents. It's ahead of the
    # new process, which is still starting, so each of its intents arrives first.
    oms.epoch[DEP] = 2
    new = Proc(lang, PARAMS, snap, epoch=2)
    z = {"sent": 0, "fenced": 0, "duplicate": 0, "placed": 0}
    n = {"sent": 0, "fenced": 0, "duplicate": 0, "placed": 0}
    for s in range(301, N + 1):
        for cmd in old.batch(batch(s, lag))["commands"]:
            z["sent"] += 1
            z[oms.place(cmd, 1)] += 1
        for cmd in new.batch(batch(s))["commands"]:
            n["sent"] += 1
            n[oms.place(cmd, 2)] += 1
    old.close()
    new.close()
    return z, n


def main():
    for lang in ("go", "python"):
        print(f"\n── {lang} ─────────────────────────────")
        print(f"latest: a tick every {EVERY_MS} ms for {FEED_S:.0f} s; the strategy takes {WORK_MS} ms to decide")
        for mode in ("queue", "conflate"):
            r = latest(lang, mode)
            print(f"  {mode:8}  {r['decisions']:3} decisions on {r['ticks']} ticks · price age at decision p50 "
                  f"{r['age_p50']:.0f} ms, max {r['age_max']:.0f} ms · up to {r['behind_max']} newer ticks existed · "
                  f"backlog when the feed stopped +{DRAIN_S:.0f}s: {r['backlog']} · skipped (counted): {r['skipped']}")
        for fenced in (True, False):
            z, n = fence(lang, fenced)
            label = "with fencing   " if fenced else "without fencing"
            print(f"fence {label}: zombie sent {z['sent']}, placed {z['placed']} (refused: old epoch {z['fenced']}, "
                  f"already placed {z['duplicate']}) · new process sent {n['sent']}, placed {n['placed']} "
                  f"(refused as already placed {n['duplicate']})")

if __name__ == "__main__":
    main()
