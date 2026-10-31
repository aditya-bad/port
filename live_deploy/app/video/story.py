"""
A replay told as a video: scenes, and in each the steps a narrator says
while the picture moves (static/linck/video/ draws them).

Pure: the replay (app/geek/replay.replay's answer: frames, legs, events
with each trade's trigger and the values it recorded), the checks that
didn't trade (strategy_decisions), the deployment (its name, capital,
config) in; the story out:

  {title, name, episode, index, live, as_of, start, end,
   series: {t, idx, vix, pnl, booked, ce, pe},     at most MAX_POINTS steps
   legs:   [{what, kind, strike, sold, qty, entry, exit, a, b}],
   scenes: [{kind, title, data, steps: [{text, say, span, cue}]}]}

`text` is the caption, `say` what the voice reads (app/video/speech.py),
`span` the seconds of animation drawn for it (the stage stretches it to
the voice), `cue` what the stage animates while it's said.

The scenes, in order: what's in the video (open), how the strategy
plays (rules), the market over the window (market), each trade with its
reason and numbers (trade), the checks that held it back (checks), the
P&L on the way (journey), where the money came from (split), where a
running cycle stands now (now), and the end (wrap).

Every number is from the replay or the strategy's own record; a rule's
reason is what it recorded when it acted. Where a value wasn't recorded
the sentence that needs it is left out, not guessed. The strangle's rules
are explained in full; the other strategies' trades by their recorded
rule and numbers (app/geek/explain.why).
"""

from __future__ import annotations

import bisect
import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from ..geek.explain import SIDE, rs, why as rule_why
from .speech import spoken

IST = ZoneInfo("Asia/Kolkata")
MAX_POINTS = 480          # steps drawn on a chart
MAX_TRADES = 12           # trades given a scene of their own
MAX_CHECKS = 5            # checks that held it back, said
INDEX_SAID = {"NIFTY 50": "Nifty", "NIFTY BANK": "Bank Nifty", "NIFTY FIN SERVICE": "Fin Nifty",
              "NIFTY MIDCAP SELECT": "Midcap Nifty", "SENSEX": "Sensex", "BANKEX": "Bankex"}
WEEKDAY = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

BALANCE = {"continuous_50pct_trigger", "eod_gap_check"}
TARGET = {"checkpoint_target"}
STOPS = {"convergence_stop", "trailing_stop", "fixed_stop"}

# what each strategy does, in plain words (the strangle has its own scene)
ABOUT = {
    "intraday_dtt_simple": ["It sells a call and a put at the same strike, right where the market is: a short straddle, on this week's expiry.",
                            "It gets out of both if either leg rises too far, when the pair has lost enough value to take a profit, or at its exit time."],
    "intraday_dtt_adjusted": ["It sells a call and a put at the market's strike, a short straddle, and manages it through the day.",
                              "When one side halves against the other, it adds a leg to that side. It takes profit on the whole premium, and its last line of defence is a stop at the index's break-even price."],
    "intraday_dtt_advanced": ["It sells a straddle at the market's strike and adjusts it through the day, like the DTT adjusted.",
                              "Once it has as many legs on a side as it allows, a new trigger closes the cheapest leg and opens a fresh one instead, as many times as the day needs."],
    "calendar_btst": ["Near the close, it sells a straddle on this week's expiry and buys the same strike on next week's.",
                      "It unwinds both in the first few minutes of the next morning."],
    "pivot_supertrend": ["It trades the index on the day's pivot levels and the SuperTrend indicator.",
                         "It enters when the price breaks a pivot level with the trend, and gets out when the SuperTrend flips."],
    "pivot_supertrend_options": ["It reads a pivot and SuperTrend signal, and sells an option on it: a put when the signal points up, a call when it points down.",
                                 "It buys the option back when the SuperTrend flips, or at its exit time."],
    "pivot_supertrend_options_inverse": ["It buys options when the SuperTrend flips: a put when it turns down, a call when it turns up.",
                                         "It holds for a set number of candles, then sells."],
    "trend_credit_spread": ["When the SuperTrend turns, it sells a credit spread with the trend: a put spread in an uptrend, a call spread in a downtrend.",
                            "It closes at its profit target, its stop, the next opposite flip, or on expiry day. The most it can lose is the spread's width."],
    "weekly_ema_st_spread": ["On hourly candles, it waits for the 20 period EMA and the SuperTrend to agree on a trend.",
                             "Then it sells a weekly credit spread with a hedge, and exits on a SuperTrend flip, or takes profit early near expiry."],
}


class NoStory(Exception):
    """Nothing to tell: the replay has no steps."""


# ── words ────────────────────────────────────────────────────────────

def _t(v) -> datetime:
    return (v if isinstance(v, datetime) else datetime.fromisoformat(v)).astimezone(IST)


def hhmm(v) -> str:
    return _t(v).strftime("%H:%M")


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def pts(v: Optional[float]) -> str:
    return f"{round(abs(float(v))):,}" if v is not None else "?"


def money(v: Optional[float]) -> str:
    """₹121, ₹119.50: an option's price keeps its paise."""
    if v is None:
        return "an unknown price"
    x = float(v)
    return rs(x) if abs(x - round(x)) < 0.005 else f"₹{x:,.2f}"


def signed(v: Optional[float]) -> str:
    return rs(v, True)


def pct(a: Optional[float], b: Optional[float]) -> Optional[int]:
    try:
        return round(float(a) / float(b) * 100) if b else None
    except (TypeError, ValueError):
        return None


def _contract(sym: str) -> tuple[str, Optional[float], Optional[str]]:
    """('24,500 put', 24500, 'PE')."""
    from ..candles import parse_contract
    c = parse_contract(sym or "") or {}
    kind = c.get("instrument_type") or ((sym or "")[-2:] if (sym or "")[-2:] in ("CE", "PE") else None)
    k = c.get("strike")
    if kind in SIDE:
        return (f"{k:,.0f} {SIDE[kind]}" if k else SIDE[kind]), (float(k) if k else None), kind
    return ("future" if (sym or "").endswith("FUT") else sym), None, kind


def _pick(seed: str, options: list[str]) -> str:
    return options[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(options)]


def _words(s: str) -> int:
    return len(s.split())


def step(text: str, cue: str, **extra) -> dict:
    text = " ".join(text.split())
    s = {"text": text, "say": spoken(text), "cue": cue,
         # the animation as drawn: ~60% of the voice, which the stage stretches to fit
         "span": round(max(1.4, min(9.0, _words(text) / 2.6 * 0.6)), 2)}
    s.update(extra)
    return s


class When:
    """'at 11:42' when the day was just said, else 'on Wednesday the
    22nd, at 11:42'; 'today' and 'yesterday' when the video is of a
    cycle still running."""

    def __init__(self, as_of: datetime, live: bool):
        self.as_of, self.live, self.day = as_of.astimezone(IST), live, None

    def day_of(self, t: datetime) -> str:
        d = t.date()
        if self.live and d == self.as_of.date():
            return "today"
        if self.live and d == self.as_of.date() - timedelta(days=1):
            return "yesterday"
        return f"on {WEEKDAY[d.weekday()]} the {ordinal(d.day)}"

    def __call__(self, v, cap: bool = False) -> str:
        t = _t(v)
        if t.date() == self.day:
            s = f"at {t:%H:%M}"
        else:
            self.day = t.date()
            s = f"{self.day_of(t)}, at {t:%H:%M}"
        return s[0].upper() + s[1:] if cap else s

    def plain_day(self, v) -> str:
        t = _t(v)
        s = self.day_of(t)
        return s[3:] if s.startswith("on ") else s


def _clean(summary: str) -> str:
    """A decision's summary, as said: '24500PE' -> 'the 24,500 put'."""
    s = re.sub(r"\b(?:[A-Z]+\d{2}[A-Z0-9]{3})?(\d{4,6})(CE|PE)\b",
               lambda m: f"the {int(m.group(1)):,} {SIDE[m.group(2)]}", summary or "")
    s = s.replace("PE/CE", "put to call").replace("CE/PE", "call to put").replace("≥", "at or above").replace("≤", "at or below")
    s = re.sub(r"\bCE\b", "call", s)
    s = re.sub(r"\bPE\b", "put", s)
    return s.rstrip(". ")


# ── the series ───────────────────────────────────────────────────────

def _series(D: dict) -> tuple[dict, list[int]]:
    frames, legs = D["frames"], D.get("legs") or []
    n = len(frames)
    keep = set(range(0, n, max(1, -(-n // MAX_POINTS))))
    keep |= {e["frame"] for e in D.get("events") or [] if 0 <= e.get("frame", -1) < n} | {n - 1}
    keep = sorted(keep)
    kinds = [l.get("kind") for l in legs]
    out = {k: [] for k in ("t", "idx", "vix", "pnl", "booked", "ce", "pe")}
    for i in keep:
        f = frames[i]
        out["t"].append(f["t"])
        for k in ("idx", "vix", "pnl", "booked"):
            out[k].append(None if f.get(k) is None else round(float(f[k]), 2))
        side = {"CE": 0.0, "PE": 0.0}
        seen = {"CE": False, "PE": False}
        for h in f.get("legs") or []:
            kd = kinds[h[0]] if h[0] < len(kinds) else None
            if kd in side:
                side[kd] += float(h[1])
                seen[kd] = True
        out["ce"].append(round(side["CE"], 2) if seen["CE"] else None)
        out["pe"].append(round(side["PE"], 2) if seen["PE"] else None)
    return out, keep


def _at(keep: list[int], frame: int) -> int:
    """The series step a replay frame falls on."""
    return max(0, bisect.bisect_right(keep, frame) - 1)


def _step_of(S: dict, t) -> int:
    tt = [_t(x) for x in S["t"]]
    return max(0, bisect.bisect_right(tt, _t(t)) - 1)


# ── the scenes ───────────────────────────────────────────────────────

def build(D: dict, *, dep: dict, strategy_label: str, decisions: Optional[list] = None,
          now: Optional[datetime] = None) -> dict:
    frames = D.get("frames") or []
    if not frames:
        raise NoStory("This replay has no prices to show, so there's nothing to draw.")
    strategy = dep["strategy_name"]
    cfg = dep.get("config") or {}
    capital = float(dep.get("initial_capital") or 0)
    name = dep.get("deployment_name") or "This deployment"
    index = INDEX_SAID.get(D.get("index_name") or "", D.get("index_name") or "the index")
    eps, sel = D.get("episodes") or [], D.get("selected") or [len(D.get("episodes") or []) or 1] * 2
    ep = lambda n: (eps[n - 1]["label"] if 0 < n <= len(eps) else f"Trade {n}")  # noqa: E731
    episode = ep(sel[0]) if sel[0] == sel[1] else f"{ep(sel[0])} to {ep(sel[1])}"
    raw_legs = D.get("legs") or []
    live = any(l.get("t1") is None for l in raw_legs)
    as_of = _t(D.get("end") or frames[-1]["t"]) if not live else (now or _t(D.get("end") or frames[-1]["t"])).astimezone(IST)
    when = When(as_of, live)
    S, keep = _series(D)
    events = sorted(D.get("events") or [], key=lambda e: e["t"])
    f0, fz = frames[0], frames[-1]
    total, booked = float(fz.get("pnl") or 0), float(fz.get("booked") or 0)
    is_strangle = strategy == "strangle_monthly_v2"
    seed = f"{dep.get('id')}|{D.get('start')}"

    legs = []
    for l in raw_legs:
        what, k, kind = _contract(l["symbol"])
        legs.append({"what": what, "symbol": l["symbol"], "kind": kind, "strike": l.get("strike") or k,
                     "sold": l.get("side") == "short", "qty": l.get("qty"), "entry": l.get("entry"), "exit": l.get("exit"),
                     "a": _step_of(S, l["t0"]) if l.get("t0") else 0,
                     "b": _step_of(S, l["t1"]) if l.get("t1") else None,
                     "t0": l.get("t0"), "t1": l.get("t1")})

    def entry_of(sym: str, t: str) -> Optional[dict]:
        """The leg a closing fill closed: that symbol, opened before t."""
        c = [l for l in legs if l["symbol"] == sym and l["t0"] and l["t0"] <= t and (l["t1"] is None or l["t1"] >= t)]
        return c[-1] if c else None

    first_entry = next((e for e in events if (e.get("values") or {}).get("target_premium") is not None), None)
    scenes: list[dict] = []

    # 1. what this video is
    opening = _pick(seed, ["Let's walk through", "Here's", "Okay, let's look at"])
    s1 = f"{opening} {episode} of {name}, the {strategy_label} on {index}"
    if live:
        s1 += f". It started {when.plain_day(D['start'])} at {hhmm(D['start'])}, and it's still running, so this is where it stands at {hhmm(as_of)} {when.plain_day(as_of)}."
    else:
        s1 += f". It ran from {when.plain_day(D['start'])} at {hhmm(D['start'])} to {when.plain_day(D['end'])} at {hhmm(D['end'])}."
    open_now = total - booked
    s2 = f"The short version: it's {'up' if total >= 0 else 'down'} {rs(abs(total))}{' so far' if live else ''}."
    if abs(open_now) >= 1:
        s2 += f" {rs(booked)} of that is booked, and {rs(open_now)} is in positions still open."
    elif events:
        s2 += " All of it is booked; nothing is left open."
    n_adj = sum(1 for e in events if e["kind"] in ("adjustment", "roll", "hedge", "added", "leg_closed"))
    s3 = f"{len(events)} trade{'s' if len(events) != 1 else ''}"
    s3 += f", {n_adj} of them adjustments." if n_adj else "."
    s3 += " Let's see what it did, and why."
    scenes.append({"kind": "open", "title": episode, "data": {
        "name": name, "strategy": strategy_label, "episode": episode, "index": index, "live": live,
        "window": (f"{_t(D['start']):%d %b %H:%M} → now, {as_of:%d %b %H:%M}" if live else
                   f"{_t(D['start']):%d %b %H:%M} → {_t(D['end']):%d %b %H:%M}"),
        "total": round(total, 2), "booked": round(booked, 2), "open": round(open_now, 2), "trades": len(events)},
        "steps": [step(s1, "title"), step(s2, "headline"), step(s3, "count")]})

    # 2. how the strategy plays
    scenes.append(_rules(strategy, strategy_label, cfg, capital, index, first_entry))

    # 3. the market
    scenes.append(_market(S, D, legs, index, when, live, is_strangle))

    # 4. each trade (the biggest MAX_TRADES; the rest said in passing)
    big = events
    if len(events) > MAX_TRADES:
        rank = sorted(events, key=lambda e: (e["kind"] not in ("new", "entry", "exit"), -abs(e.get("booked") or 0)))
        big = sorted(rank[:MAX_TRADES], key=lambda e: e["t"])
    prev = None
    for n, e in enumerate(events):
        if e in big:
            nxt = events[n + 1] if n + 1 < len(events) else None
            scenes.append(_trade(e, prev, nxt, S, keep, legs, entry_of, strategy, is_strangle, cfg, capital, index, when, n + 1, len(events)))
        prev = e
    if len(events) > len(big):
        rest = [e for e in events if e not in big]
        scenes.append({"kind": "more", "title": "The smaller trades", "data": {
            "marks": [{"at": _at(keep, e["frame"]), "label": e["label"], "booked": e.get("booked")} for e in rest]},
            "steps": [step(f"There were {len(rest)} smaller trades as well. Together they booked "
                           f"{signed(sum(e.get('booked') or 0 for e in rest))}.", "marks")]})

    # 5. what held it back
    chk = _checks(decisions or [], S, when)
    if chk:
        scenes.append(chk)

    # 6. the P&L on the way
    scenes.append(_journey(S, total, live, when))

    # 7. where the money came from
    sp = fz.get("split")
    if sp and any(abs(x) >= 1 for x in sp):
        scenes.append(_split(sp, f0, fz, index, legs, total))

    # 8. a running cycle: where it stands now
    if live:
        scenes.append(_now(fz, raw_legs, legs, S, cfg, capital, total, index, is_strangle, as_of, D))

    # 9. the end
    end = f"So that's {episode}: {signed(total)}{' so far' if live else ''}."
    end += " It's still running, so make this video again later to see how the rest of it goes." if live else " Thanks for watching."
    scenes.append({"kind": "wrap", "title": "That's it", "data": {"total": round(total, 2), "live": live},
                   "steps": [step(end, "end")]})

    return {"v": 1, "title": f"{name} · {episode}", "name": name, "episode": episode, "strategy": strategy,
            "strategy_label": strategy_label, "index": index, "live": live, "as_of": as_of.isoformat(),
            "start": D.get("start"), "end": D.get("end"), "series": S,
            "legs": [{k: v for k, v in l.items() if k not in ("symbol", "t0", "t1")} for l in legs],
            "scenes": scenes}


def _rules(strategy: str, label: str, cfg: dict, capital: float, index: str, first_entry: Optional[dict]) -> dict:
    steps, cards = [], []
    if strategy == "strangle_monthly_v2":
        v = (first_entry or {}).get("values") or {}
        tp, dte = v.get("target_premium"), v.get("days_to_expiry")
        s = "First, how it plays. It sells a call above the market and a put below it"
        s += f", each worth about {rs(tp)}" if tp else ""
        s += f", on a contract about {round(float(dte))} days out." if dte is not None else "."
        s += f" Options lose value with time, so while {index} stays between the two, both get cheaper, and it keeps the difference."
        steps.append(step(s, "card"))
        cards.append({"h": "Sell both sides", "b": f"a call above, a put below{f', ~{rs(tp)} each' if tp else ''}"})
        ratio = float(cfg.get("adjustment_trigger_ratio", 0.5))
        steps.append(step(f"It keeps the two sides in balance: if one falls below {round(ratio * 100)}% of the other, "
                          f"it rolls the cheaper one closer to the market.", "card"))
        cards.append({"h": "Keep it balanced", "b": f"roll a side under {round(ratio * 100)}% of the other"})
        eod, floor = cfg.get("eod_check_time", "15:13"), float(cfg.get("eod_gap_floor", 0.8))
        steps.append(step(f"Every day at {eod} it checks again, and it won't carry a gap wider than {round(floor * 100)}% overnight.", "card"))
        cards.append({"h": f"Daily check, {eod}", "b": f"no gap past {round(floor * 100)}% overnight"})
        cp = float(cfg.get("checkpoint_profit_pct_of_capital", cfg.get("checkpoint_pct", 0.005)))
        if capital:
            steps.append(step(f"It takes profit in checkpoints: once a cycle has made {rs(cp * capital)}, "
                              f"{cp * 100:g}% of its capital, it closes everything and starts afresh.", "card"))
            cards.append({"h": "Checkpoint", "b": f"{rs(cp * capital)} a cycle, then start again"})
        mode = cfg.get("convergence_mode", "fixed_stop")
        stop_pct = float(cfg.get("convergence_stop_pct", 0.10))
        if mode in ("fixed_stop", "trailing_stop"):
            steps.append(step(f"And if both sides meet at one strike, it puts a "
                              f"{'trailing ' if mode == 'trailing_stop' else ''}stop {stop_pct * 100:g}% above their combined price.", "card"))
            cards.append({"h": "If they meet", "b": f"{'trailing ' if mode == 'trailing_stop' else ''}stop {stop_pct * 100:g}% above"})
    else:
        about = ABOUT.get(strategy) or [f"This is the {label}."]
        steps.append(step("Here's how this strategy plays. " + about[0], "card"))
        cards.append({"h": label, "b": about[0]})
        for a in about[1:]:
            steps.append(step(a, "card"))
            cards.append({"h": "Then", "b": a})
        sets = _settings(strategy, cfg)
        if sets:
            steps.append(step("The settings it runs with: " + "; ".join(f"{l} {v}" for l, v in sets[:3]) + ".", "card"))
            cards.append({"h": "Settings", "b": " · ".join(f"{l} {v}" for l, v in sets[:4])})
    return {"kind": "rules", "title": "How it plays", "data": {"cards": cards}, "steps": steps}


def _settings(strategy: str, cfg: dict) -> list[tuple[str, str]]:
    from ..v2.catalog import FIELDS
    keys = ["entry_time", "force_exit_time", "per_leg_stop_loss_pct", "combined_premium_profit_pct", "max_adjustments",
            "lots_per_trade", "expiry_selector"]
    out = []
    for k in keys:
        if k in cfg and cfg[k] not in (None, ""):
            f, v = FIELDS.get(k) or {}, cfg[k]
            if "fraction" in (f.get("help") or "") and isinstance(v, (int, float)):
                v = f"{float(v) * 100:g}%"
            elif isinstance(v, str) and v.isupper():
                v = v.replace("_", " ").lower()
            out.append(((f.get("label") or k).lower(), str(v)))
    return out


def _market(S: dict, D: dict, legs: list, index: str, when: When, live: bool, strangle: bool) -> dict:
    ix = [(i, v) for i, v in enumerate(S["idx"]) if v is not None]
    steps = []
    data: dict[str, Any] = {}
    if ix:
        a, z = ix[0][1], ix[-1][1]
        hi = max(ix, key=lambda x: x[1])
        lo = min(ix, key=lambda x: x[1])
        data.update({"hi": hi[0], "lo": lo[0]})
        mv = z - a
        s = f"First, the market. {index} started at {pts(a)}, and {'is' if live else 'ended'} at {pts(z)}"
        s += f", {'up' if mv >= 0 else 'down'} {pts(mv)} points." if abs(mv) >= 1 else ", right where it began."
        steps.append(step(s, "draw"))
        if hi[0] not in (0, len(S["idx"]) - 1) or lo[0] not in (0, len(S["idx"]) - 1):
            steps.append(step(f"Its high was {pts(hi[1])}, {when.plain_day(S['t'][hi[0]])} at {hhmm(S['t'][hi[0]])}, "
                              f"and its low {pts(lo[1])}, {when.plain_day(S['t'][lo[0]])} at {hhmm(S['t'][lo[0]])}.", "extremes"))
    opt = [l for l in legs if l["kind"] in ("CE", "PE") and l["strike"]]
    if opt and ix:
        if strangle:
            s = ("These bands are the strikes it had sold: calls above, puts below. "
                 f"As long as {index} stays inside them, the strangle is comfortable.")
        else:
            s = "These lines are the strikes of the options it held, drawn against the price."
        steps.append(step(s, "strikes"))
    vx = [v for v in S["vix"] if v is not None]
    if len(vx) >= 2:
        v0, v1 = vx[0], vx[-1]
        s = f"And India VIX, the market's fear gauge, went from {v0:.1f} to {v1:.1f}"
        sold = any(l["sold"] for l in opt)
        if abs(v1 - v0) < 0.2:
            s += ", about flat."
        elif v1 < v0:
            s += ", so options got cheaper" + (", which helps a seller." if sold else ".")
        else:
            s += ", so options got pricier" + (", which works against a seller." if sold else ".")
        steps.append(step(s, "vix"))
    if not steps:
        steps.append(step("There's no index history for this window, so the market isn't drawn.", "draw"))
    return {"kind": "market", "title": "The market", "data": data, "steps": steps}


def _family(e: dict) -> str:
    trig = e.get("trigger") or ""
    v = e.get("values") or {}
    if trig in BALANCE and v.get("smaller_sum") is not None and v.get("bigger_sum"):
        return "balance"
    if trig in TARGET and v.get("total_cycle_profit") is not None:
        return "target"
    if trig in STOPS and (v.get("stop_level") is not None or v.get("combined_now") is not None):
        return "stop"
    if all(f["opening"] for f in e.get("fills") or []) and not e.get("closed_before"):
        return "entry"
    return "exit" if not any(f["opening"] for f in e.get("fills") or []) else "change"


def _fill_said(f: dict, legs_entry, t: str, index_at: Optional[float]) -> tuple[str, Optional[str]]:
    """('buys back the 24,500 put at ₹61', 'It sold that for ₹119.50 earlier, so that books plus ₹4,388.')"""
    what, k, kind = _contract(f["symbol"])
    verb = ("sells" if f["opening"] else "sells back") if f["action"] == "sell" else ("buys" if f["opening"] else "buys back")
    s = f"{verb} the {what} at {money(f['price'])}"
    if f["opening"] and k and index_at and kind in SIDE:
        d = k - index_at
        if abs(d) >= 1:
            s += f", {pts(d)} points {'above' if d > 0 else 'below'} the market"
    more = None
    if not f["opening"]:
        L = legs_entry(f["symbol"], t)
        if L and L.get("entry") is not None:
            more = f"It {'sold' if L['sold'] else 'bought'} that for {money(L['entry'])} earlier"
            more += f", so that {'books' if (f.get('pnl') or 0) >= 0 else 'costs'} {rs(abs(f['pnl']))}." if f.get("pnl") is not None else "."
    return s, more


def _fills_sentence(fills: list[dict], legs_entry, t: str, index_at, lead: str = "It", then: str = "Then it") -> list[str]:
    """One sentence a fill (with what a closed leg was opened at), up to
    three fills; more than that, one sentence listing them."""
    if not fills:
        return []
    said = [_fill_said(f, legs_entry, t, index_at) for f in fills]
    if len(said) <= 3:
        return [f"{lead if i == 0 else then} {s}." + (f" {more}" if more else "") for i, (s, more) in enumerate(said)]
    body = ", ".join(s for s, _ in said[:-1]) + " and " + said[-1][0]
    return [f"{lead} {body}."]


def _lead(move: str, head: str, why: str) -> str:
    """'At 10:00, Nifty is at 24,900. It entered…', or without a price 'At 10:00, it entered…'."""
    if move:
        return f"{move} {why}"
    return f"{head}, {why[0].lower()}{why[1:]}"


def _trade(e, prev, nxt, S, keep, legs, entry_of, strategy, strangle, cfg, capital, index, when, n, of) -> dict:
    fam = _family(e)
    v = e.get("values") or {}
    at = _at(keep, e["frame"])
    last = len(S["t"]) - 1
    # from the trade before to a little after this one; an entry, on to the next trade
    w0 = _at(keep, prev["frame"]) if prev else 0
    w0 = max(0, min(w0, at - 12))
    after = max(12, (at - w0) // 3)
    if fam == "entry":
        after = max(after, ((_at(keep, nxt["frame"]) if nxt else last) - at) * 3 // 4)
    w1 = min(last, at + after)
    spot = e.get("idx")
    head = when(e["t"], cap=True)
    title = f"{hhmm(e['t'])} · " + {"target": "Checkpoint hit", "stop": "Stop hit"}.get(
        fam, "End-of-day check" if e.get("trigger") == "eod_gap_check" else e["label"])
    data: dict[str, Any] = {"family": fam, "trigger": e.get("trigger"), "at": at, "w": [w0, w1], "label": e["label"], "n": n, "of": of,
                            "fills": [], "booked": e.get("booked"), "pnl": e.get("pnl"), "spot": spot}
    for f in (e.get("closed_before") or {}).get("fills", []) + (e.get("fills") or []):
        what, k, kind = _contract(f["symbol"])
        data["fills"].append({"verb": ("Sell" if f["action"] == "sell" else "Buy") + ("" if f["opening"] else " back"),
                              "what": what, "price": f["price"], "qty": f["qty"], "pnl": f.get("pnl"), "opening": f["opening"],
                              "strike": k, "kind": kind})
    steps = []
    mv = (e.get("since") or {}).get("idx")
    move = ""
    if spot:
        move = f"{head}, {index} is at {pts(spot)}"
        if mv is not None and prev is not None and abs(mv) >= 1:
            move += f", {'up' if mv > 0 else 'down'} {pts(mv)} points since the last trade"
        move += "."
    fills = e.get("fills") or []
    closes = [f for f in fills if not f["opening"]]
    opens = [f for f in fills if f["opening"]]

    if fam == "entry":
        if strangle and v.get("target_premium") is not None:
            why = (f"That's when it opens the cycle. It looks for a call and a put each worth about {rs(v['target_premium'])}"
                   + (f", on the contract {round(float(v['days_to_expiry']))} days from expiry." if v.get("days_to_expiry") is not None else "."))
        else:
            why = rule_why(strategy, e.get("trigger") or "", v)
        steps.append(step(_lead(move, head, why), "spot"))
        steps += [step(s, "legs") for s in _fills_sentence(opens, entry_of, e["t"], spot, then="And it")]
        sold = [f for f in opens if f["action"] == "sell"]
        bought = [f for f in opens if f["action"] == "buy"]
        if sold:
            prem = sum(float(f["price"]) for f in sold)
            got = sum(float(f["price"]) * float(f["qty"]) for f in sold) - sum(float(f["price"]) * float(f["qty"]) for f in bought)
            qty = sold[0]["qty"]
            s = (f"That's {money(prem)} a unit. " if len(sold) > 1 else "")
            s += f"With {qty:,.0f} units{' a side' if len(sold) > 1 else ''}, it collects about {rs(got)} up front, the most it can make."
            ks = sorted(_contract(f["symbol"])[1] or 0 for f in sold)
            if strangle and len(sold) >= 2 and ks[0] and ks[-1]:
                s += f" While {index} stays between {pts(ks[0])} and {pts(ks[-1])}, time is on its side."
            elif len(sold) == 1:
                s += " If the price stays away from that strike, the option loses value every day, and that's the profit."
            data["collected"] = round(got, 2)
            steps.append(step(s, "collect"))
        elif bought:
            paid = sum(float(f["price"]) * float(f["qty"]) for f in bought)
            steps.append(step(f"It pays {rs(paid)} for that. That's the most it can lose; it needs the price to move its way.", "collect"))

    elif fam == "balance":
        sm, bg = SIDE.get(v.get("smaller_side"), "smaller"), SIDE.get(v.get("bigger_side"), "bigger")
        small, bigv = float(v["smaller_sum"]), float(v["bigger_sum"])
        data["gauge"] = {"small": small, "big": bigv, "small_side": sm, "big_side": bg,
                         "line": float((v.get("eod_gap_floor") or cfg.get("eod_gap_floor", 0.8)) if e.get("trigger") == "eod_gap_check"
                                      else v.get("ratio") or cfg.get("adjustment_trigger_ratio", 0.5))}
        if move:
            steps.append(step(move, "move"))
        s = f"Watch the two sides. The {bg} side is now worth {money(bigv)}, and the {sm} side has shrunk to {money(small)}."
        if mv is not None and ((bg == "call" and mv > 0) or (bg == "put" and mv < 0)):
            s += f" The market moved toward the {bg}, so it got pricier, while the {sm}, further away, lost value."
        steps.append(step(s, "sides"))
        p = pct(small, bigv)
        if e.get("trigger") == "eod_gap_check":
            fl = round(data["gauge"]["line"] * 100)
            s = (f"This is the end-of-day check. It won't carry a gap wider than {fl}% overnight, and {money(small)} "
                 f"is only {p}% of {money(bigv)}, so it acts.")
        else:
            r = round(data["gauge"]["line"] * 100)
            s = (f"Here's the rule. If one side falls below {r}% of the other, the strangle is lopsided: the market is leaning on "
                 f"the {bg}, and the {sm} is too far away to earn much. {money(small)} is just {p}% of {money(bigv)}, so the rule fires.")
        steps.append(step(s, "ratio"))
        for s in _fills_sentence(closes, entry_of, e["t"], spot, lead="First it"):
            steps.append(step(s, "close"))
        if opens:
            s = _fills_sentence(opens, entry_of, e["t"], spot, lead="Then it")[0].rstrip(".")
            new_small = small - sum(float(f["price"]) for f in closes) + sum(float(f["price"]) for f in opens)
            s += f", closer in. Now the sides are {money(bigv)} and about {money(new_small)}, back in balance."
            data["gauge"]["after"] = round(new_small, 2)
            steps.append(step(s, "open"))
        if e.get("pnl") is not None:
            steps.append(step(f"After the roll, {'the cycle' if strangle else 'it'} stands at {signed(e['pnl'])}.", "pnl"))

    elif fam == "target":
        made, tgt = float(v["total_cycle_profit"]), v.get("checkpoint_target")
        data["gauge"] = {"made": made, "target": tgt}
        s = f"{head}, the cycle's profit reaches {rs(made)}"
        parts = [f"{rs(v['cycle_realized_pnl'])} booked" if v.get("cycle_realized_pnl") is not None else None,
                 f"{rs(v['unrealized'])} still open" if v.get("unrealized") is not None else None]
        parts = [x for x in parts if x]
        s += (": " + " and ".join(parts) + ".") if parts else "."
        steps.append(step(s, "progress"))
        if tgt is not None:
            pc = f", {float(tgt) / capital * 100:g}% of the {rs(capital)} capital" if capital else ""
            steps.append(step(f"Its checkpoint is {rs(tgt)}{pc}. Once a cycle has made that much, it doesn't wait around: it locks it in.", "target"))
        cb = e.get("closed_before")
        cfills = cb["fills"] if cb else closes
        for s in _fills_sentence(cfills, entry_of, e["t"], spot, lead="So it"):
            steps.append(step(s, "close"))
        bk = (cb or {}).get("booked") if cb else e.get("booked")
        if bk is not None:
            steps.append(step(f"Closing those legs books {signed(bk)}.", "booked"))
        if cb and opens:
            s = "And it starts the next cycle straight away. " + " ".join(_fills_sentence(opens, entry_of, e["t"], spot, lead="It", then="And it"))
            steps.append(step(s, "open"))

    elif fam == "stop":
        data["gauge"] = {"base": v.get("convergence_premium"), "now": v.get("combined_now"), "stop": v.get("stop_level")}
        if e.get("trigger") == "convergence_stop":
            s = (f"{head}, its call and put are at the same strike. That's a straddle, and a straddle has no cushion: any move hurts it.")
            if v.get("convergence_premium") is not None:
                s += f" The two were worth {money(v['convergence_premium'])} together when that happened."
        else:
            s = f"{head}, it's holding a straddle, and its {'trailing ' if e.get('trigger') == 'trailing_stop' else ''}stop is in play."
        steps.append(step(s, "meet"))
        if v.get("stop_level") is not None and v.get("combined_now") is not None:
            steps.append(step(f"The stop is at {money(v['stop_level'])}. The two legs together have climbed to "
                              f"{money(v['combined_now'])}, past it, so it closes.", "stop"))
        for s in _fills_sentence(closes, entry_of, e["t"], spot, lead="It"):
            steps.append(step(s, "close"))
        if e.get("booked") is not None:
            steps.append(step(f"That {'books' if e['booked'] >= 0 else 'costs'} {rs(abs(e['booked']))}.", "booked"))

    else:   # an exit, or a change worded by its rule
        why = rule_why(strategy, e.get("trigger") or "", v)
        steps.append(step(_lead(move, head, why), "move"))
        vals = [(k.replace("_", " "), x) for k, x in v.items() if isinstance(x, (int, float)) and not isinstance(x, bool)][:4]
        if vals:
            data["values"] = [{"k": k, "v": x} for k, x in vals]
        cb = e.get("closed_before")
        for s in _fills_sentence((cb["fills"] if cb else []) + fills, entry_of, e["t"], spot, lead="It"):
            steps.append(step(s, "close" if closes else "legs"))
        bk = e.get("booked")
        if bk is not None and not any("books" in st["text"] or "costs" in st["text"] for st in steps):
            steps.append(step(f"That {'books' if bk >= 0 else 'costs'} {rs(abs(bk))}.", "booked"))
        if e.get("pnl") is not None:
            steps.append(step(f"After it, {'the cycle' if strangle else 'it'} stands at {signed(e['pnl'])}.", "pnl"))
    if e.get("repaired"):
        steps.append(step("This trade was written in afterwards by a repair, from the market's own prices, because the app wasn't trading at that moment.", "note"))
    return {"kind": "trade", "title": title, "data": data, "steps": steps}


def _checks(decisions: list, S: dict, when: When) -> Optional[dict]:
    said, out = set(), []
    for d in sorted(decisions, key=lambda x: (x["outcome"] != "blocked", _t(x["t"]))):
        key = (d["rule"], d["summary"])
        if key in said or len(out) >= MAX_CHECKS:
            continue
        said.add(key)
        out.append(d)
    if not out:
        return None
    out.sort(key=lambda d: _t(d["t"]))
    when.day = None     # a new scene says its day again
    steps = [step("Not every check ends in a trade. Here's what it looked at, and held back on.", "intro")]
    marks = []
    for d in out:
        verb = "it held off" if d["outcome"] == "blocked" else "it checked and did nothing"
        steps.append(step(f"{when(d['t'], cap=True)}, {verb}: {_clean(d['summary'])}.", "mark"))
        marks.append({"at": _step_of(S, d["t"]), "blocked": d["outcome"] == "blocked", "text": _clean(d["summary"])[:90]})
    return {"kind": "checks", "title": "What it held back on", "data": {"marks": marks}, "steps": steps}


def _journey(S: dict, total: float, live: bool, when: When) -> dict:
    p = [(i, v) for i, v in enumerate(S["pnl"]) if v is not None]
    steps = [step("Now the money. This is the P&L the whole way through: what's booked, plus what the open positions were worth.", "draw")]
    data: dict[str, Any] = {}
    if p:
        hi = max(p, key=lambda x: x[1])
        lo = min(p, key=lambda x: x[1])
        data.update({"hi": hi[0], "lo": lo[0]})
        s = f"At its best it was up {rs(hi[1])}, {when.plain_day(S['t'][hi[0]])} at {hhmm(S['t'][hi[0]])}." if hi[1] > 0 else "It never got above zero."
        s += (f" At its lowest it was {signed(lo[1])}, {when.plain_day(S['t'][lo[0]])} at {hhmm(S['t'][lo[0]])}." if lo[1] < 0
              else " And it never went below zero.")
        steps.append(step(s, "extremes"))
        gap = hi[1] - total
        s = f"It {'stands' if live else 'ends'} at {signed(total)}{' for now' if live else ''}."
        if hi[1] > 0 and gap > max(500.0, abs(hi[1]) * 0.15):
            s += f" That's {rs(gap)} below its best point: open positions swing with the market until they're closed."
        steps.append(step(s, "end"))
    return {"kind": "journey", "title": "The P&L on the way", "data": data, "steps": steps}


def _split(sp: list, f0: dict, fz: dict, index: str, legs: list, total: float) -> dict:
    tdecay, mkt, vol = sp
    seller = any(l["sold"] for l in legs)
    steps = [step("So where did the money come from? Three places.", "intro")]
    if seller:
        steps.append(step(f"Time passing. Options lose a little value every day, and a seller keeps that. Here that's {signed(tdecay)}.", "time"))
    else:
        steps.append(step(f"Time passing. A bought option loses a little value every day; here that's {signed(tdecay)}.", "time"))
    mv = (fz.get("idx") or 0) - (f0.get("idx") or 0) if f0.get("idx") and fz.get("idx") else None
    s = f"{index}'s move" + (f": {pts(mv)} points {'up' if mv >= 0 else 'down'}" if mv is not None else "")
    s += f". That {'added' if mkt >= 0 else 'cost'} {rs(abs(mkt))}."
    steps.append(step(s, "market"))
    s = "And volatility"
    if f0.get("vix") and fz.get("vix"):
        s += f": VIX went from {float(f0['vix']):.1f} to {float(fz['vix']):.1f}"
    s += f". That's {signed(vol)}, along with anything the other two don't explain."
    steps.append(step(s, "vol"))
    steps.append(step(f"Together, {signed(tdecay + mkt + vol)}. The split is an estimate, from an options model.", "sum"))
    return {"kind": "split", "title": "Where the money came from",
            "data": {"time": round(tdecay, 2), "market": round(mkt, 2), "vol": round(vol, 2), "total": round(total, 2)}, "steps": steps}


def _now(fz: dict, raw_legs: list, legs: list, S: dict, cfg: dict, capital: float, total: float, index: str,
         strangle: bool, as_of: datetime, D: dict) -> dict:
    held = []
    for h in fz.get("legs") or []:
        j, mark = h[0], h[1]
        if j < len(legs):
            held.append({**legs[j], "mark": mark, "stale": h[3]})
    data: dict[str, Any] = {"held": [{"what": h["what"], "mark": h["mark"], "entry": h["entry"], "sold": h["sold"]} for h in held]}
    steps = []
    if held:
        parts = [f"the {h['what']} is at {money(h['mark'])}, against {money(h['entry'])} when it {'sold' if h['sold'] else 'bought'} it"
                 for h in held[:4]]
        said = "; ".join(parts)
        steps.append(step(f"So where does it stand right now, at {as_of:%H:%M}? {said[0].upper()}{said[1:]}.", "legs"))
    else:
        steps.append(step(f"Right now, at {as_of:%H:%M}, it holds nothing open.", "legs"))
    spot = fz.get("idx")
    if strangle and held:
        ce = sum(h["mark"] for h in held if h["kind"] == "CE")
        pe = sum(h["mark"] for h in held if h["kind"] == "PE")
        if ce > 0 and pe > 0:
            small, big = (("put", pe), ("call", ce)) if pe < ce else (("call", ce), ("put", pe))
            p = pct(small[1], big[1])
            r = round(float(cfg.get("adjustment_trigger_ratio", 0.5)) * 100)
            data["gauge"] = {"small": small[1], "big": big[1], "small_side": small[0], "big_side": big[0], "line": r / 100}
            s = f"The {small[0]} side is at {p}% of the {big[0]} side. The roll comes below {r}%"
            if p < r:
                s += ", so it's already past the line: a roll should come any moment, unless something is holding it back."
            elif p - r >= 15:
                s += ", so there's room for now."
            else:
                s += f", so it's getting close: if {index} keeps moving toward the {big[0]}, a roll may come soon."
            steps.append(step(s, "ratio"))
        cp = float(cfg.get("checkpoint_profit_pct_of_capital", cfg.get("checkpoint_pct", 0.005))) * capital
        cyc = total if (D.get("selected") or [0, 0])[0] == (D.get("selected") or [0, 0])[1] else None
        if cp and cyc is not None:
            data["target"] = {"made": round(cyc, 2), "target": round(cp, 2)}
            if cyc >= 0:
                steps.append(step(f"The cycle has made {rs(cyc)} of its {rs(cp)} checkpoint, that's {pct(cyc, cp)}%. "
                                  f"Another {rs(max(cp - cyc, 0))} and it books everything and starts over.", "target"))
            else:
                steps.append(step(f"The cycle is at {signed(cyc)} right now, {rs(cp - cyc)} away from its {rs(cp)} checkpoint.", "target"))
        ks = sorted((h["strike"], h["kind"]) for h in held if h["strike"])
        calls = [k for k, kd in ks if kd == "CE"]
        puts = [k for k, kd in ks if kd == "PE"]
        if spot and calls and puts:
            steps.append(step(f"{index} is at {pts(spot)}: {pts(min(calls) - spot)} points "
                              f"{'below' if min(calls) >= spot else 'above'} the call at {pts(min(calls))}, and {pts(spot - max(puts))} points "
                              f"{'above' if spot >= max(puts) else 'below'} the put at {pts(max(puts))}.", "band"))
    elif spot:
        steps.append(step(f"{index} is at {pts(spot)}, and it stands at {signed(total)}.", "band"))
    return {"kind": "now", "title": "Where it stands now", "data": data, "steps": steps}
