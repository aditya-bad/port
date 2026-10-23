"""A strategy in Python, run by the Linck runtime prototype (supervisor.py).

The top half is what the Python SDK would be; the bottom half is the only
part a user writes. Same logic as gostrat/main.go, line for line, so the
two must make the same decisions on the same batches."""

import hashlib
import json
import socket
import sys
import time
import traceback


# ── the SDK ─────────────────────────────────────────────────────────────

class Ctx:
    def __init__(self, seq, now, dep):
        self.seq, self.now, self.dep, self.cmds, self.decisions = seq, now, dep, [], []

    def order(self, side, qty, inst):
        self.cmds.append({"intent_id": f"{self.dep}:{self.seq}:{len(self.cmds)}", "side": side, "qty": qty, "inst": inst})

    def decide(self, rule, outcome, why):
        self.decisions.append(f"{rule}|{outcome}|{why}")


def state_hash(s):
    return hashlib.sha256(json.dumps(s, separators=(",", ":")).encode()).hexdigest()[:16]


def run(strategy):
    sock = socket.socket(socket.AF_UNIX)
    sock.connect(sys.argv[1])
    r, w = sock.makefile("rb"), sock.makefile("wb")

    def send(v):
        w.write(json.dumps(v, separators=(",", ":")).encode() + b"\n")
        w.flush()
    dep, cur = None, 0
    try:
        for line in r:
            m = json.loads(line)
            t = m["type"]
            if t == "hello":
                dep = m["deployment"]
                strategy.init(m.get("params") or {})
                if m.get("state"):
                    strategy.S = dict(m["state"])          # restore: the declared state only
                send({"type": "ready", "sdk": "python", "subscribe": [1]})
            elif t == "batch":
                cur = m["seq"]
                ctx = Ctx(m["seq"], m["now"], dep)
                for f in m.get("fills") or []:
                    strategy.on_fill(ctx, f)
                strategy.on_batch(ctx, m.get("ticks") or [])
                send({"type": "done", "seq": m["seq"], "commands": ctx.cmds, "decisions": ctx.decisions, "state_hash": state_hash(strategy.S)})
            elif t == "snapshot":
                send({"type": "snapshot", "seq": m["seq"], "state": strategy.S})
    except Exception as e:        # a bug in the strategy: say where and why, then exit
        send({"type": "error", "seq": cur, "error": repr(e), "stack": traceback.format_exc()})
        sys.exit(2)


# ── the user's strategy ─────────────────────────────────────────────────

class Strategy:
    def init(self, p):
        self.fast_a, self.slow_a, self.panic_at = p["fast"], p["slow"], int(p.get("panic_at", 0))
        self.work_s = float(p.get("work_ms", 0)) / 1000      # a slow strategy, for the latest-tick test
        self.S = {"Fast": 0.0, "Slow": 0.0, "Pos": 0, "Seen": 0}

    def on_fill(self, ctx, f):
        self.S["Pos"] += f["qty"] if f["side"] == "buy" else -f["qty"]

    def on_batch(self, ctx, ticks):
        S = self.S
        if self.panic_at and ctx.seq == self.panic_at:
            legs = []
            legs[3]                                       # a bug: on this batch, every time
        if self.work_s:
            time.sleep(self.work_s)
        for t in ticks:
            if t["id"] != 1:
                continue
            if S["Seen"] == 0:
                S["Fast"] = S["Slow"] = t["px"]
            S["Seen"] += 1
            S["Fast"] += self.fast_a * (t["px"] - S["Fast"])
            S["Slow"] += self.slow_a * (t["px"] - S["Slow"])
        if S["Seen"] < 20:
            return
        if S["Pos"] == 0 and S["Fast"] > S["Slow"] * 1.0004:
            ctx.order("buy", 75, 1)
            ctx.decide("entry", "acted", "fast %.4f above slow %.4f" % (S["Fast"], S["Slow"]))
        elif S["Pos"] > 0 and S["Fast"] < S["Slow"] * 0.9996:
            ctx.order("sell", S["Pos"], 1)
            ctx.decide("exit", "acted", "fast %.4f below slow %.4f" % (S["Fast"], S["Slow"]))


if __name__ == "__main__":
    run(Strategy())
