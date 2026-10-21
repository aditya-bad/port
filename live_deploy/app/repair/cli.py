"""
live_deploy — the interactive repair CLI (custom_scripts/repair_stall.py
starts it): every deployment the stall caught, what replaying it would
do, the minutes only the trader can decide, and apply / undo, one
deployment at a time.

Screens:
  list     every deployment that wasn't stopped, scanned with the usual
           reading of each candle: what it held, how many trades the
           replay makes, how many minutes are unclear, and the window's
           P&L repaired against as the stall left it.
  review   one deployment: replayed until a minute where the order inside
           the candle changes what the strategy does; both paths are shown
           (candles, trades, P&L) and the trader picks one; on to the next.
  plan     the whole repair: trades, P&L repaired vs as left, booked,
           charges, best and worst moment, the P&L path and Nifty through
           the window, open legs after. Apply (this deployment only),
           change a choice, or go back.

Input and output are injected (inp, out) so the whole flow is testable.
"""

from __future__ import annotations

import builtins
import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable, Optional

from . import engine
from .market import MinuteBook

BARS = "▁▂▃▄▅▆▇█"
ORDER_WORDS = {"up_first": "UP first", "down_first": "DOWN first"}


def went(order: str, index: str = "the index") -> str:
    """'NIFTY 50 went UP first'."""
    return f"{index} went {ORDER_WORDS[order]}"
PHASE_WORDS = {"open": "open", "close": "close", "up": "↑ high", "down": "↓ low"}


# ── Formatting ──────────────────────────────────────────────────────────


def inr(x: Optional[float], sign: bool = True, dp: int = 0) -> str:
    """Indian digit grouping: 1,23,456. None -> '—'."""
    if x is None:
        return "—"
    neg = x < 0
    whole, _, frac = f"{abs(x):.{dp}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    s = whole + (f".{frac}" if frac else "")
    return ("−" if neg else "+" if sign and x > 0 else "") + s


def spark(values: list, width: int = 48) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return ""
    if len(vals) > width:
        step = len(vals) / width
        vals = [vals[int(i * step)] for i in range(width)]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return BARS[3] * len(vals)
    return "".join(BARS[int((v - lo) / (hi - lo) * (len(BARS) - 1))] for v in vals)


def short(symbol: str, n: int = 24) -> str:
    return symbol if len(symbol) <= n else symbol[: n - 1] + "…"


class Paint:
    def __init__(self, on: bool):
        self.on = on

    def _c(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.on else str(s)

    def bold(self, s):
        return self._c("1", s)

    def dim(self, s):
        return self._c("2", s)

    def warn(self, s):
        return self._c("33", s)

    def money(self, x, **kw):
        s = inr(x, **kw)
        if x is None or not self.on:
            return s
        return self._c("32" if x > 0 else "31" if x < 0 else "0", s)


# ── The running app (pause / resume) ────────────────────────────────────


class AppApi:
    """The app's own API, to pause a deployment while it's repaired and
    resume it after (a running deployment doesn't re-read the database)."""

    def __init__(self, base: Optional[str], key: Optional[str], timeout: float = 15):
        self.base = (base or "").rstrip("/")
        self.key = key
        self.timeout = timeout

    def _call(self, method: str, path: str) -> dict:
        req = urllib.request.Request(self.base + path, method=method, headers={"X-API-Key": self.key or ""})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            body = r.read()
        return json.loads(body) if body else {}

    def reachable(self) -> bool:
        if not self.base:
            return False
        try:
            self._call("GET", "/health")
            return True
        except Exception:
            return False

    def last_tick_at(self) -> Optional[str]:
        try:
            found = []

            def walk(v):
                if isinstance(v, dict):
                    for k, x in v.items():
                        if k == "last_tick_at" and x:
                            found.append(x)
                        walk(x)
            walk(self._call("GET", "/health"))
            return found[0] if found else None
        except Exception:
            return None

    def pause(self, dep_id) -> None:
        self._call("POST", f"/deployments/{dep_id}/pause")

    def resume(self, dep_id) -> None:
        self._call("POST", f"/deployments/{dep_id}/resume")


# ── The CLI ─────────────────────────────────────────────────────────────


class RepairCLI:
    def __init__(self, pool, book: MinuteBook, real_kite, window: engine.Window, api: AppApi,
                 inp: Optional[Callable[[str], str]] = None, out: Callable[[str], None] = print,
                 color: bool = False, author: Optional[str] = None):
        self.pool, self.book, self.kite, self.window, self.api = pool, book, real_kite, window, api
        self.inp, self.out, self.p, self.author = inp, out, Paint(color), author
        self.rows: list[dict] = []
        self.scans: dict = {}

    def say(self, s: str = "") -> None:
        self.out(s)

    def ask(self, prompt: str) -> str:
        try:
            return ((self.inp or builtins.input)(prompt) or "").strip()
        except EOFError:
            return "q"

    # ── list ──
    async def run(self) -> None:
        w = self.window.as_dict()
        self.say(self.p.bold(f"STALL REPAIR · {self.window.day:%a %d %b %Y} · replaying {w['from']} → {w['to']} on 1-minute candles"))
        self.say(self.p.dim("Each minute is played as open → one extreme → the other → close; a CE is at its high and a PE at "
                            "its low when the index is at its high. Where the order inside a minute changes what the strategy "
                            "does, you choose."))
        await self.scan()
        while True:
            self.show_list()
            a = self.ask("\nPick a number to review · r rescan · q quit › ").lower()
            if a in ("q", "quit", "exit"):
                return
            if a == "r":
                await self.scan()
                continue
            if a.isdigit() and 1 <= int(a) <= len(self.rows):
                await self.review(self.rows[int(a) - 1])
                await self.scan(only=self.rows[int(a) - 1]["id"])

    async def scan(self, only=None) -> None:
        rows = await engine.candidates(self.pool, self.window)
        if only is None:
            self.say(f"\nScanning {len(rows)} deployment(s)…")
        for i, r in enumerate(rows, 1):
            if only is not None and r["id"] != only:
                r["scan"] = self.scans.get(r["id"])
                continue
            if r["repaired"]:
                r["scan"] = None
            else:
                if only is None:
                    self.say(self.p.dim(f"  [{i}/{len(rows)}] {r['name']}"))
                try:
                    o = await engine.plan(self.pool, r["id"], self.window, self.book, self.kite, {}, auto=True)
                    r["scan"] = {"outcome": o, "summary": engine.summarize(o, r["capital"])}
                except Exception as e:   # one deployment's failure never hides the rest
                    r["scan"] = {"error": f"{type(e).__name__}: {e}"}
            self.scans[r["id"]] = r["scan"]
        self.rows = rows

    def show_list(self) -> None:
        p = self.p
        self.say("\n" + p.bold(f"{'#':>2}  {'Deployment':<28} {'Strategy':<24} {'Mode':<10} {'Status':<7} "
                               f"{'Open':>4} {'Trades':>6} {'Unclear':>7} {'Repaired':>10} {'As left':>10} {'Change':>9}"))
        for i, r in enumerate(self.rows, 1):
            head = f"{i:>2}  {short(r['name'], 28):<28} {short(r['strategy'], 24):<24} {r['mode']:<10} {r['status']:<7} {len(r['legs']):>4}"
            if r["repaired"]:
                at = r["repaired"]["applied_at"].astimezone(engine.IST)
                self.say(head + "  " + p.dim(f"repaired at {at:%H:%M} ({r['repaired']['summary'].get('fills', 0)} trades) — pick to view or undo"))
                continue
            sc = r.get("scan") or {}
            if "error" in sc:
                self.say(head + "  " + p.warn(f"replay failed: {sc['error'][:60]}"))
                continue
            s, o = sc["summary"], sc["outcome"]
            if o.blind:
                self.say(head + "  " + p.warn(f"no prices for {len(o.blind)} leg(s): can't be repaired — pick it to see why"))
                continue
            if not o.fills and not r["legs"]:
                self.say(head + "  " + p.dim("nothing to repair"))
                continue
            self.say(head + f" {len(o.fills):>6} {len(o.auto):>7} {p.money(s['pnl']):>10} {p.money(s['left']):>10} {p.money(s['change']):>9}")
            if r["fills_after_start"]:
                self.say("    " + p.warn(f"⚠ {r['fills_after_start']} fill(s) already recorded after {self.window.as_dict()['from']} — "
                                       "the replay starts from the deployment as it is now"))
        self.say(p.dim("Repaired / As left: P&L over the window with the strategy replayed / with the legs left as they were "
                       "(marked to each minute's close). Unclear: minutes where you'll choose; the scan used the usual reading."))

    # ── review ──
    async def review(self, row: dict) -> None:
        if row["repaired"]:
            await self.show_repaired(row)
            return
        self.header(row)
        choices: dict = {}
        while True:
            self.say(self.p.dim("Replaying…"))
            o = await engine.plan(self.pool, row["id"], self.window, self.book, self.kite, choices)
            if not o.complete:
                pick = self.ask_path(o)
                if pick is None:
                    return
                choices[o.ambiguity.minute] = pick
                continue
            act = self.show_plan(row, o)
            if act == "apply":
                if await self.apply(row, o):
                    return
            elif act == "change":
                changed = self.change(choices)
                if changed is not None:
                    choices = changed
                    continue
            elif act == "back":
                return

    def header(self, row: dict) -> None:
        p = self.p
        self.say("\n" + p.bold(f"━━ {row['name']} · {row['strategy']} · {row['mode']} · capital {inr(row['capital'], sign=False)}"))
        if row["legs"]:
            self.say(f"Open when ticks stopped (before {self.window.as_dict()['from']}):")
            for l in row["legs"]:
                self.say(f"   {'SELL' if l['side'] == 'short' else 'BUY ':<4} {float(l['qty']):>6.0f} {l['symbol']:<26} entry {float(l['avg_entry_price']):>9.2f}")
        else:
            self.say("No open legs when ticks stopped.")
        self.say(f"Booked today before the stall: {p.money(row['booked_before'])}")

    def ask_path(self, o: engine.Outcome) -> Optional[str]:
        a, p = o.ambiguity, self.p
        self.say("\n" + p.warn(f"⚠ {a.minute} — the 1-minute candle can't tell which way {o.index_name} went first, and the strategy "
                               "acts differently each way."))
        self.say(f"   {'Candle ' + a.minute:<28} {'open':>10} {'high':>10} {'low':>10} {'close':>10}")
        for c in a.candles:
            self.say(f"   {short(c['label'], 28):<28} {c['open']:>10.2f} {c['high']:>10.2f} {c['low']:>10.2f} {c['close']:>10.2f}")
        idx = a.candles[0] if a.candles else None
        options = [a.natural, engine.other(a.natural)]
        for n, order in enumerate(options, 1):
            v = a.paths[order]
            how = ""
            if idx:
                first, then = (idx["high"], idx["low"]) if order == "up_first" else (idx["low"], idx["high"])
                how = f" (to {inr(first, sign=False, dp=2)}, then {inr(then, sign=False, dp=2)})"
            usual = p.dim("  · the usual reading of this candle") if order == a.natural else ""
            self.say(f"\n   [{n}] {went(order, o.index_name)}{how}{usual}")
            if not v["fills"]:
                self.say("       no trade this minute")
            for f in v["fills"]:
                self.say("       " + self.fill_line(f))
            self.say(f"       P&L over the window so far: {p.money(v['pnl'])}   open legs after: "
                     + (", ".join(f"{short(l['symbol'], 22)}" for l in v["open"]) or "none"))
        while True:
            c = self.ask("\nChoose 1 or 2 · b back › ").lower()
            if c in ("1", "2"):
                return options[int(c) - 1]
            if c in ("b", "q"):
                return None

    def fill_line(self, f: dict) -> str:
        booked = f" booked {self.p.money(f['realized'])}" if f.get("realized") is not None else ""
        return (f"{f['minute']} {PHASE_WORDS.get(f['phase'], f['phase']):<6} {f['action'].upper():<4} {f['qty']:>5.0f} "
                f"{short(f['symbol'], 24):<24} @ {f['price']:>8.2f}  {f['reason']}{booked}")

    def show_plan(self, row: dict, o: engine.Outcome) -> str:
        p = self.p
        s = engine.summarize(o, row["capital"])
        w = self.window.as_dict()
        self.say("\n" + p.bold(f"━━ Repair plan — {row['name']}"))
        if o.blind:
            self.say(p.warn(f"⚠ CAN'T BE TRUSTED: no prices at all for {', '.join(o.blind)}. The strategy couldn't see "
                            "these legs, so what it did (or didn't do) below means nothing. It can't be applied; the notes "
                            "at the end say why the prices are missing."))
        if o.choices:
            self.say("Paths you chose: " + ", ".join(f"{k} {ORDER_WORDS[v].lower()}" for k, v in sorted(o.choices.items())))
        else:
            self.say("No minute needed a choice.")
        self.say(p.bold(f"\nTrades the strategy would have made ({len(o.fills)}):"))
        if not o.fills:
            self.say("   none — the strategy had nothing to do in the window")
        for f in o.fills:
            self.say("   " + self.fill_line(f))
        self.say(p.bold(f"\nP&L over {w['from']}–{w['to']}"))
        col = lambda label, a, b="": self.say(f"   {label:<28}{a:>14}{b:>24}")  # noqa: E731
        col("", "Repaired", "As left by the stall")
        col("Window P&L", p.money(s["pnl"]), p.money(s["left"]))
        col("Change from repairing", p.money(s["change"]))
        col("Booked in the window", p.money(s["booked"]))
        col("Estimated charges", p.money(-s["charges"] if s["charges"] else 0))
        col("Net of charges", p.money(s["net"]))
        if s["pct"] is not None:
            col("Of capital", inr(s["pct"], dp=2) + "%")
        if s["best"]:
            self.say(f"   {'Best / worst moment':<28}{p.money(s['best']['pnl'])} at {s['best']['t']} / "
                     f"{p.money(s['worst']['pnl'])} at {s['worst']['t']}")
        self.say(f"   {'P&L path (repaired)':<28}{spark([c['pnl'] for c in o.curve])}")
        self.say(f"   {'P&L path (as left)':<28}{spark([c['left'] for c in o.curve])}")
        if s["index"]:
            i = s["index"]
            self.say(f"   {o.index_name:<28}{spark([c['idx'] for c in o.curve])}  {inr(i['first'], sign=False)} → {inr(i['last'], sign=False)}"
                     f" (range {inr(i['low'], sign=False)}–{inr(i['high'], sign=False)})")
        if s["vix"]:
            self.say(f"   {'India VIX':<28}{s['vix']['first']:.2f} → {s['vix']['last']:.2f}")
        if s["spikes"]:
            self.say(p.dim(f"   {s['spikes']} trade(s) at a candle's high or low (a spike counts, as agreed)."))
        last = o.curve[-1]["t"] if o.curve else w["to"]
        for title, legs in ((f"As the stall left them (marked at {last}, P&L since entry)", o.left_positions),
                            (f"Open after the repair (marked at {last}, P&L since entry)", o.end_positions)):
            self.say(p.bold(f"\n{title}:"))
            if not legs:
                self.say("   flat")
            for l in legs:
                mark = f"{l['mark']:.2f}" if l["mark"] is not None else "—"
                self.say(f"   {'SELL' if l['side'] == 'short' else 'BUY ':<4} {l['qty']:>6.0f} {short(l['symbol'], 26):<26} "
                         f"entry {l['entry']:>9.2f}  mark {mark:>9}  {p.money(l['pnl'])}")
        for n in o.notes:
            self.say(p.warn("   note: " + n))
        for n in o.problems:
            self.say(p.warn("   strategy error during the replay: " + n))
        self.say(p.dim("   The order book isn't in history: liquidity checks see a one-tick spread; fills are at candle prices."))
        while True:
            a = self.ask("\na apply to this deployment · c change a choice · b back › ").lower()
            if a in ("a", "apply"):
                if o.blind:
                    self.say(p.warn("   Not applicable: the replay had no prices for " + ", ".join(o.blind) + "."))
                    continue
                return "apply"
            if a == "c":
                if o.choices:
                    return "change"
                self.say("   No choices were made in this plan.")
            if a in ("b", "q", "back"):
                return "back"

    def change(self, choices: dict) -> Optional[dict]:
        keys = sorted(choices)
        for n, k in enumerate(keys, 1):
            self.say(f"   [{n}] {k} — {went(choices[k])}")
        a = self.ask("Which choice to flip (number) · b back › ").lower()
        if not a.isdigit() or not 1 <= int(a) <= len(keys):
            return None
        k = keys[int(a) - 1]
        # later minutes were decided on the old path: decide them again
        kept = {m: v for m, v in choices.items() if m < k}
        kept[k] = engine.other(choices[k])
        dropped = [m for m in keys if m > k]
        if dropped:
            self.say(self.p.dim(f"   Later choices ({', '.join(dropped)}) will be asked again on the new path."))
        return kept

    async def apply(self, row: dict, o: engine.Outcome) -> bool:
        p = self.p
        self.say(f"\nThis applies {len(o.fills)} trade(s) to {p.bold(row['name'])} only"
                 + (", pausing it first and resuming it after." if row["status"] == "active" else "."))
        if self.ask("Apply? (y/N) › ").lower() not in ("y", "yes"):
            self.say("   Not applied.")
            return False
        paused = False
        if row["status"] == "active":
            if self.api.reachable():
                try:
                    self.api.pause(row["id"])
                    paused = True
                except Exception as e:
                    self.say(p.warn(f"   Couldn't pause it through the app ({e}); not applied."))
                    return False
            else:
                self.say(p.warn(f"   The app isn't reachable at {self.api.base or '(no URL)'} to pause the deployment. If the "
                                "app is running, its runner won't see the repair and may undo it."))
                if self.ask("   Is the app stopped? Apply anyway (y/N) › ").lower() not in ("y", "yes"):
                    self.say("   Not applied.")
                    return False
        try:
            res = await engine.apply(self.pool, row["id"], self.window, self.book, self.kite, o, author=self.author)
        except (engine.PlanChanged, engine.AlreadyRepaired) as e:
            self.say(p.warn(f"   Not applied: {e}. Review it again."))
            res = None
        finally:
            if paused:
                try:
                    self.api.resume(row["id"])
                except Exception as e:
                    self.say(p.warn(f"   Applied, but resuming failed ({e}) — resume it from the app."))
        if res is None:
            return False
        s = res["summary"]
        self.say(p.bold(f"   Applied: {s['fills']} trade(s), window P&L {inr(s['pnl'])} (as left {inr(s['left'])}). "
                        "A journal note was added; undo is available from the list."))
        return True

    async def show_repaired(self, row: dict) -> None:
        p, r = self.p, row["repaired"]
        s = r["summary"]
        self.say("\n" + p.bold(f"━━ {row['name']} — repaired at {r['applied_at'].astimezone(engine.IST):%H:%M}"))
        if r["choices"]:
            self.say("Paths chosen: " + ", ".join(f"{k} {went(v)}" for k, v in sorted(r["choices"].items())))
        for f in s.get("fills_detail", []):
            self.say("   " + self.fill_line(f))
        self.say(f"Window P&L {p.money(s.get('pnl'))} (as left {p.money(s.get('left'))}), booked {p.money(s.get('booked'))}")
        a = self.ask("\nu undo this repair · b back › ").lower()
        if a != "u":
            return
        if self.ask("Undo it? Its trades, note and events are removed and the legs restored (y/N) › ").lower() not in ("y", "yes"):
            return
        paused = False
        if row["status"] == "active" and self.api.reachable():
            try:
                self.api.pause(row["id"])
                paused = True
            except Exception as e:
                self.say(p.warn(f"   Couldn't pause it ({e}); not undone."))
                return
        try:
            res = await engine.undo(self.pool, r["id"])
            self.say(p.bold(f"   Undone: {res['lots_removed']} fill(s) removed."))
        except engine.PlanChanged as e:
            self.say(p.warn(f"   Not undone: {e}."))
        finally:
            if paused:
                try:
                    self.api.resume(row["id"])
                except Exception as e:
                    self.say(p.warn(f"   Resuming failed ({e}) — resume it from the app."))


def color_ok(stream) -> bool:
    return hasattr(stream, "isatty") and stream.isatty() and not os.environ.get("NO_COLOR")


def parse_hhmm(s: str):
    return datetime.strptime(s.strip(), "%H:%M").time()
