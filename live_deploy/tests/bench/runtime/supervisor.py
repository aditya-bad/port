"""The Linck runtime, in miniature: one supervisor running strategies written
in any language, behind one protocol (docs/linck-runtime.html).

The supervisor never imports strategy code. It starts a process, says
Hello (parameters and saved state), sends numbered batches and reads back
Done (orders, decisions, a hash of the state), and can ask for a Snapshot.
Everything below works the same for the Go and the Python strategy:

  live      run 600 batches; every input batch is written to the journal
            BEFORE it's sent; a snapshot every 100; a toy venue fills each
            order on the next batch; an order service that refuses an
            intent id it has already seen
  walk      a fresh process from the snapshot at 300, fed the journal's
            batches 301..600: the repair / day-check walk
  branch    the same snapshot, fed two readings of an ambiguous stretch
            (the index up first, then down first): where do they part?
  kill -9   the process is killed mid-batch at a moment with orders in
            flight; a new one starts from the last snapshot, catches up on
            the journal, and the order service drops the replayed intents
  bug       a strategy that crashes on the same batch every time: restarted
            once, crashes again at the same place -> quarantined, not looped
  languages the Go and the Python strategy on the same journal

    python3 supervisor.py           (after: cd gostrat && go build -o ../gostrat_bin .)
"""

import json
import math
import os
import random
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LANGS = {"go": [os.path.join(HERE, "gostrat_bin")], "python": [sys.executable, os.path.join(HERE, "strat.py")]}
PARAMS = {"fast": 0.2, "slow": 0.05}
N, SNAP_EVERY, DEP = 600, 100, "dep-7"


class Crashed(Exception):
    def __init__(self, seq, error=None, stack=None):
        super().__init__(error or "the process died")
        self.seq, self.error, self.stack = seq, error, stack


class Proc:
    """One strategy process, in whatever language."""

    n = 0

    def __init__(self, lang, params, state=None, epoch=1):
        Proc.n += 1
        self.path = f"/tmp/linck-rt-{os.getpid()}-{Proc.n}.sock"
        if os.path.exists(self.path):
            os.remove(self.path)
        lst = socket.socket(socket.AF_UNIX)
        lst.bind(self.path)
        lst.listen(1)
        self.p = subprocess.Popen(LANGS[lang] + [self.path], stderr=subprocess.DEVNULL)
        lst.settimeout(10)
        self.s, _ = lst.accept()
        lst.close()
        self.s.settimeout(5)            # the batch budget's hard limit, for the prototype
        self.r, self.w = self.s.makefile("rb"), self.s.makefile("wb")
        self.epoch, self.seq = epoch, 0
        self.send({"type": "hello", "deployment": DEP, "params": params, "state": state})
        assert self.recv()["type"] == "ready"

    def send(self, m):
        try:
            self.w.write(json.dumps(m, separators=(",", ":")).encode() + b"\n")
            self.w.flush()
        except OSError:                      # the process is gone
            raise Crashed(m.get("seq", self.seq))

    def recv(self):
        try:
            line = self.r.readline()
        except (socket.timeout, OSError):
            line = b""
        if not line:
            raise Crashed(self.seq)
        m = json.loads(line)
        if m["type"] == "error":
            raise Crashed(m["seq"], m["error"], m["stack"])
        return m

    def batch(self, b):
        self.seq = b["seq"]
        self.send(b)
        return self.recv()

    def snapshot(self, seq):
        self.send({"type": "snapshot", "seq": seq})
        m = self.recv()
        return m["state"]

    def kill(self):
        self.p.send_signal(signal.SIGKILL)
        self.p.wait()

    def close(self):
        try:
            for f in (self.w, self.r, self.s):   # all three, or the strategy never sees the end
                try:
                    f.close()
                except OSError:
                    pass
        finally:
            self.p.wait(timeout=5)
            if os.path.exists(self.path):
                os.remove(self.path)


def prices(seed=7):
    """The index, minute by minute: a random walk with a few swings."""
    rnd, px, out = random.Random(seed), 25000.0, []
    for i in range(N + 40):
        px += rnd.gauss(0, 6) + 9 * math.sin(i / 23)
        out.append(round(px * 20) / 20)          # on the price step
    return out


class OrderService:
    """Refuses an intent id it has seen: a replayed order finds the first."""

    def __init__(self):
        self.orders, self.dupes = {}, 0

    def place(self, cmd, epoch):
        if cmd["intent_id"] in self.orders:
            self.dupes += 1
            return False
        self.orders[cmd["intent_id"]] = {**cmd, "epoch": epoch}
        return True


def live(lang, params=PARAMS, kill_at=None, verbose=False):
    """The live loop. Returns the journal, every batch's output, the snapshots,
    the order service and what recovery did."""
    px = prices()
    journal, out, snaps, oms = {}, {}, {}, OrderService()
    pending, events = [], []                  # orders sent, filled on the next batch
    proc, epoch, seq, crashes = Proc(lang, params), 1, 1, {}
    t0 = time.perf_counter()
    while seq <= N:
        if seq not in journal:                # a new batch: written to the journal first
            fills = [{"intent_id": c["intent_id"], "side": c["side"], "qty": c["qty"], "px": px[seq]} for c in pending]
            journal[seq] = {"type": "batch", "seq": seq, "now": 1_700_000_000_000 + seq * 60_000,
                            "ticks": [{"id": 1, "px": px[seq]}, {"id": 2, "px": px[seq] * 0.01}], "fills": fills}
            pending = []
        try:
            if kill_at == seq:               # the process dies as this batch goes out (kill -9)
                proc.kill()
                kill_at = None
            done = proc.batch(journal[seq])
        except Crashed as c:
            key = (c.seq, c.error)
            crashes[key] = crashes.get(key, 0) + 1
            if c.error and crashes[key] >= 2:
                events.append(f"quarantined: crashed twice at batch {c.seq} with the same error: {c.error}")
                proc.close()
                return journal, out, snaps, oms, events
            # restart: a new process from the last snapshot, a new epoch, then catch up on the journal
            proc.close() if c.error else None
            base = max((s for s in snaps if s < c.seq), default=0)
            t = time.perf_counter()
            proc, epoch = Proc(lang, params, snaps.get(base), epoch + 1), epoch + 1
            replayed = dropped = 0
            for s in range(base + 1, c.seq):
                d = proc.batch(journal[s])
                assert d["state_hash"] == out[s]["state_hash"], f"replay diverged at {s}"
                for cmd in d["commands"]:
                    dropped += not oms.place(cmd, epoch)
                replayed += 1
            events.append(f"crash at batch {c.seq} ({c.error or 'killed'}): restarted from snapshot {base}, "
                          f"caught up {replayed} batches in {(time.perf_counter() - t) * 1e3:.0f} ms, "
                          f"{dropped} replayed orders refused as already placed")
            continue                           # the batch that crashed is sent again
        out[seq] = done
        for cmd in done["commands"]:
            if oms.place(cmd, epoch):
                pending.append(cmd)
        if seq % SNAP_EVERY == 0:
            snaps[seq] = proc.snapshot(seq)
        seq += 1
    proc.close()
    events.append(f"{N} batches in {(time.perf_counter() - t0) * 1e3:.0f} ms")
    return journal, out, snaps, oms, events


def walk(lang, journal, snaps, start, end, swap=None):
    """A fresh process from the snapshot at `start`, fed the journal from
    there (with some batches swapped for another reading of them)."""
    proc, outs = Proc(lang, PARAMS, snaps[start]), {}
    for s in range(start + 1, end + 1):
        b = (swap or {}).get(s, journal[s])
        outs[s] = proc.batch(b)
    proc.close()
    return outs


def same(a, b):
    return [s for s in a if s in b and (a[s]["commands"], a[s]["decisions"]) != (b[s]["commands"], b[s]["decisions"])]


def main():
    results = {}
    for lang in ("go", "python"):
        print(f"\n── {lang} ─────────────────────────────")
        journal, out, snaps, oms, ev = live(lang)
        results[lang] = out
        orders = [s for s in out if out[s]["commands"]]
        print(f"live: {ev[-1]}; {len(oms.orders)} orders on batches {orders[:6]}…; snapshots at {sorted(snaps)}")

        w = walk(lang, journal, snaps, 300, N)
        diverged = [s for s in w if w[s]["state_hash"] != out[s]["state_hash"] or w[s]["commands"] != out[s]["commands"]]
        print(f"walk: snapshot 300 → batches 301..{N}: {len(w)} batches, "
              f"{'identical to live (orders, decisions, state)' if not diverged else f'DIVERGED at {diverged[:3]}'}")

        # an ambiguous stretch: the same minutes read "down first" instead
        amb = [s for s in range(301, N) if out[s]["commands"]][0]
        swap = {s: {**journal[s], "ticks": [{"id": 1, "px": journal[s]["ticks"][0]["px"] - 18}, journal[s]["ticks"][1]]}
                for s in range(amb - 3, amb + 1)}
        alt = walk(lang, journal, snaps, 300, N, swap)
        d = same(w, alt)
        print(f"branch: batches {amb - 3}..{amb} read the other way: the paths part at batch {d[0] if d else '—'} "
              f"({len(d)} batches decide differently); both run from the same snapshot, no copy of the process")

        kill = [s for s in range(401, 500) if out[s]["commands"]][0] + 3
        _, out2, _, oms2, ev2 = live(lang, kill_at=kill)
        print(f"kill -9 at batch {kill}: {ev2[0]}")
        print(f"   → orders {'identical to the run with no crash' if list(oms2.orders) == list(oms.orders) else 'DIFFERENT'} "
              f"({len(oms2.orders)} placed, {oms2.dupes} duplicates refused)")

        _, _, _, _, ev3 = live(lang, params={**PARAMS, "panic_at": 250})
        print(f"bug at batch 250: {ev3[0]}")
        print(f"   → {ev3[-1]}")

    d = same(results["go"], results["python"])
    print(f"\nGo vs Python on the same journal: {'the same orders and decisions on all ' + str(N) + ' batches' if not d else f'differ at {d[:5]}'}")


if __name__ == "__main__":
    main()
