"""
Explain: a replay told in words (the replay page's Explain button).

Pure: the replay (app/geek/replay.build's answer, with each trade's
trigger and the values it recorded, and the checks that didn't trade from
strategy_decisions) in; a script out: beats, each a frame of the replay
and what to say there. The page plays them with the device's voice and
captions.

Every reason and number here is what the strategy recorded when it acted
(the trigger and its values on the fill, its decision log), worded per
rule. Nothing is inferred: a rule with no wording of its own is said with
its name and the numbers it saw. The strangle's rules are worded in full;
the others' mostly by that fallback.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .common import saw
from ..v2.catalog import humanize

IST = ZoneInfo("Asia/Kolkata")
MAX_DECISIONS = 12        # checks that didn't trade, said at most this many times
SIDE = {"CE": "call", "PE": "put"}


def rs(v: Optional[float], sign: bool = False) -> str:
    """Rupees as said aloud: 'rupees 1,234' reads badly, '₹1,234' is read
    as 'rupees one thousand…' by every voice."""
    if v is None:
        return "an unknown amount"
    n = round(abs(float(v)))
    s = f"{n:,}"
    # Indian grouping
    if n >= 1000:
        head, tail = str(n)[:-3], str(n)[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    if sign:
        return f"{'minus ' if v < 0 else 'plus '}₹{s}" if round(float(v)) else "₹0"
    return f"{'minus ' if v < 0 else ''}₹{s}"


def _num(v: Any, dp: int = 2) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{x:,.{dp}f}".rstrip("0").rstrip(".") if dp else f"{x:,.0f}"


def _pct(a: float, b: float) -> str:
    return f"{a / b * 100:.0f}%" if b else "—"


def _hhmm(t: str) -> str:
    return datetime.fromisoformat(t).astimezone(IST).strftime("%H:%M")


def _day(t: str) -> str:
    d = datetime.fromisoformat(t).astimezone(IST)
    return f"{d.day} {d:%b}"


def _contract(sym: str) -> str:
    """NIFTY26NOV24500PE or a weekly NIFTY2450922400CE -> 'the 24,500 put'."""
    from ..candles import parse_contract
    c = parse_contract(sym or "") or {}
    kind = SIDE.get(c.get("instrument_type") or (sym or "")[-2:])
    if not kind:
        return sym
    return f"the {c['strike']:,.0f} {kind}" if c.get("strike") else f"the {kind}"


def fills_said(fills: list[dict]) -> str:
    """'It bought back the 24,500 put at ₹61 and sold the 24,800 put at ₹118.'"""
    parts = []
    for f in fills:
        what = _contract(f["symbol"])
        if f["action"] == "sell":
            verb = "sold" if f["opening"] else "sold back"
        else:
            verb = "bought" if f["opening"] else "bought back"
        parts.append(f"{verb} {what} at {rs(f['price'])}")
    if not parts:
        return ""
    return "It " + (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]) + "."


# ── what each rule means, from the values it recorded ─────────────────

def _why_strangle(trigger: str, v: dict) -> Optional[str]:
    g = v.get
    if trigger in ("initial_entry", "reentry_after_checkpoint_target") or (trigger == "checkpoint_target" and g("target_premium") is not None):
        bits = []
        if g("target_premium") is not None:
            bits.append(f"it looked for a call and a put each worth about {rs(g('target_premium'))}")
        if g("days_to_expiry") is not None:
            bits.append(f"on the contract {_num(g('days_to_expiry'), 0)} days from expiry")
        start = "It started a new cycle after taking profit" if trigger != "initial_entry" else "It entered"
        return f"{start}: " + ", ".join(bits) + "." if bits else f"{start}."
    if trigger == "continuous_50pct_trigger":
        sm, bg = SIDE.get(g("smaller_side"), g("smaller_side")), SIDE.get(g("bigger_side"), g("bigger_side"))
        if g("smaller_sum") is not None and g("bigger_sum"):
            return (f"The {sm} side had fallen to {rs(g('smaller_sum'))}, {_pct(g('smaller_sum'), g('bigger_sum'))} of the {bg} side's "
                    f"{rs(g('bigger_sum'))}. The rule acts below {_num((g('ratio') or 0.5) * 100, 0)}%, so it rolled the cheaper {sm} "
                    f"closer to the market to bring the two sides back in balance.")
    if trigger == "eod_gap_check":
        sm, bg = SIDE.get(g("smaller_side"), g("smaller_side")), SIDE.get(g("bigger_side"), g("bigger_side"))
        if g("smaller_sum") is not None and g("bigger_sum"):
            return (f"At the end-of-day check the {sm} side was {rs(g('smaller_sum'))}, {_pct(g('smaller_sum'), g('bigger_sum'))} of the "
                    f"{bg} side. It doesn't hold a gap that wide overnight (the floor is {_num((g('eod_gap_floor') or 0.8) * 100, 0)}%), "
                    f"so it replaced the cheaper {sm}.")
    if trigger == "checkpoint_target" and g("total_cycle_profit") is not None:
        parts = [f"{rs(g('cycle_realized_pnl'))} booked" if g("cycle_realized_pnl") is not None else None,
                 f"{rs(g('unrealized'))} still open" if g("unrealized") is not None else None]
        parts = [x for x in parts if x]
        made = f"The cycle had made {rs(g('total_cycle_profit'))}" + (f": {' and '.join(parts)}" if parts else "")
        return (f"{made}. That passed its checkpoint of {rs(g('checkpoint_target'))}, "
                f"so it closed everything and took the profit.")
    if trigger == "convergence_stop" and g("stop_level") is not None:
        return (f"Its legs had met at one strike, a straddle, at {rs(g('convergence_premium'))} combined. They rose to "
                f"{rs(g('combined_now'))}, past the stop at {rs(g('stop_level'))}, so it closed everything.")
    if trigger in ("trailing_stop", "fixed_stop") and (g("stop_level") is not None or g("combined_now") is not None):
        kind = "trailing stop" if trigger == "trailing_stop" else "stop"
        return (f"As a straddle, the combined premium rose to {rs(g('combined_now'))}, past its {kind} at {rs(g('stop_level'))}, "
                f"so it closed everything.")
    if trigger == "contract_expiry_backstop":
        return "Its contract expired that day, and it closes everything rather than hold through expiry."
    if trigger == "eod_rollout":
        return "At the end of the day it rolled out to the next strikes instead of letting the legs meet at one strike."
    return None


WORDERS = {"strangle_monthly_v2": _why_strangle}

# rules most strategies share, said the same way whichever strategy
COMMON = {
    "entry": "It entered", "entry_time_reached": "Its entry time came, so it entered",
    "force_exit": "Its exit time came, so it closed everything", "st_flip": "The SuperTrend flipped against it, so it closed",
    "pivot_break_long": "The price broke above the pivot level, so it entered", "st_flip_entry": "The SuperTrend flipped, so it entered",
    "roll_close": "It rolled", "roll_open": "It rolled", "early_close_capture": "It closed early to keep most of the premium",
    "orphan_leg_cleanup": "It closed a leg left without its pair",
}


def why(strategy: str, trigger: str, values: Optional[dict]) -> str:
    v = values if isinstance(values, dict) else {}
    said = WORDERS.get(strategy, lambda *_: None)(trigger, v)
    if said:
        return said
    nums = saw(v, 4)
    start = COMMON.get(trigger) or f"Its {humanize(trigger).lower()} rule acted"
    return start + (f", seeing {nums}." if nums else ".")


# ── the script ────────────────────────────────────────────────────────

def script(D: dict, *, name: str, strategy: str, strategy_label: str, decisions: Optional[list] = None) -> dict:
    """{beats: [{frame, t, kind, text}], words} for the replay D."""
    frames, events = D.get("frames") or [], D.get("events") or []
    if not frames:
        return {"beats": [], "words": 0}
    idx_name = D.get("index_name") or "the index"
    beats = []

    def beat(frame, kind, text, t=None):
        beats.append({"frame": max(0, min(len(frames) - 1, int(frame))), "t": t or frames[max(0, min(len(frames) - 1, int(frame)))]["t"],
                      "kind": kind, "text": " ".join(text.split())})

    f0, fz = frames[0], frames[-1]
    # what it held before the replay's first moment (not what its first trade opens)
    held0 = [l for l in D.get("legs") or [] if l.get("t0") and l["t0"] < D["start"]]
    intro = f"{name}, running {strategy_label}. This replay runs from {_day(D['start'])} at {_hhmm(D['start'])}"
    intro += f" to {_day(D['end'])} at {_hhmm(D['end'])}." if D.get("end") else "."
    if f0.get("idx"):
        intro += f" {idx_name} was at {_num(f0['idx'], 0)}" + (f", and India VIX at {_num(f0['vix'], 1)}." if f0.get("vix") else ".")
    if held0:
        intro += f" It started holding {len(held0)} leg{'s' if len(held0) != 1 else ''}: {', '.join(_contract(l['symbol']) for l in held0[:4])}."
    beat(0, "intro", intro)

    # the checks that didn't trade, once per rule and wording
    said, nd = set(), 0
    tt = [datetime.fromisoformat(f["t"]) for f in frames]

    def frame_of(t: datetime) -> int:
        k = 0
        for j, x in enumerate(tt):
            if x <= t:
                k = j
        return k
    marks = []
    for d in sorted(decisions or [], key=lambda x: x["t"]):
        key = (d["rule"], d["summary"])
        if key in said or nd >= MAX_DECISIONS:
            continue
        said.add(key)
        nd += 1
        t = d["t"] if isinstance(d["t"], datetime) else datetime.fromisoformat(d["t"])
        verb = "held off" if d["outcome"] == "blocked" else "checked and did nothing"
        when = f"At {t.astimezone(IST):%H:%M}" + (f" on {_day(t.isoformat())}" if _day(t.isoformat()) != _day(D["start"]) else "")
        marks.append((t, "decision", f"{when} it {verb}: {d['summary'].rstrip('.')}.", frame_of(t)))

    for e in events:
        t = datetime.fromisoformat(e["t"])
        text = f"At {_hhmm(e['t'])}" + (f" on {_day(e['t'])}" if _day(e["t"]) != _day(D["start"]) else "") + f", {e['label'].lower()}. "
        if e.get("closed_before"):
            cb = e["closed_before"]
            text += why(strategy, e.get("trigger") or "", e.get("values")) + " "
            text += fills_said(cb["fills"]) + (f" Closing those legs booked {rs(cb['booked'], True)}." if cb.get("booked") is not None else "") + " "
            text += "Then it entered again. " + fills_said(e["fills"])
        else:
            text += why(strategy, e.get("trigger") or "", e.get("values")) + " " + fills_said(e["fills"])
            if e.get("booked") is not None:
                text += f" That booked {rs(e['booked'], True)}."
        if e.get("idx"):
            text += f" {idx_name} was at {_num(e['idx'], 0)}."
        if e.get("pnl") is not None:
            text += f" So far it stands at {rs(e['pnl'], True)}."
        marks.append((t, "trade", text, e["frame"]))

    for t, kind, text, fr in sorted(marks, key=lambda m: (m[0], m[1] != "trade")):
        beat(fr, kind, text, t.isoformat())

    # how it went
    booked, total = fz.get("booked") or 0.0, fz.get("pnl") or 0.0
    out = f"In all, {rs(total, True)}"
    out += f": {rs(booked, True)} booked" + (f" and {rs(total - booked, True)} still open." if abs(total - booked) >= 1 else ".")
    if f0.get("idx") and fz.get("idx"):
        mv = fz["idx"] - f0["idx"]
        out += f" Over the replay {idx_name} moved {'up' if mv >= 0 else 'down'} {_num(abs(mv), 0)} points, to {_num(fz['idx'], 0)}"
        out += f", and VIX went from {_num(f0['vix'], 1)} to {_num(fz['vix'], 1)}." if f0.get("vix") and fz.get("vix") else "."
    trades = [e for e in events if e.get("booked") is not None]
    if trades:
        best = max(trades, key=lambda e: e["booked"])
        worst = min(trades, key=lambda e: e["booked"])
        if best["booked"] > 0:
            out += f" The best single move was the {best['label'].lower()} at {_hhmm(best['t'])}, booking {rs(best['booked'])}."
        if worst["booked"] < 0 and worst is not best:
            out += f" The costliest was the {worst['label'].lower()} at {_hhmm(worst['t'])}, {rs(worst['booked'])}."
    sp = fz.get("split")
    if sp and any(abs(x) >= 1 for x in sp):
        # replay.split_leg: (time decay, the market's move, volatility), an estimate from the volatility at entry
        out += (f" Roughly, {rs(sp[0], True)} of it came from time passing, {rs(sp[1], True)} from {idx_name}'s move, "
                f"and {rs(sp[2], True)} from changes in volatility.")
    beat(len(frames) - 1, "summary", out)
    return {"beats": beats, "words": sum(len(b["text"].split()) for b in beats)}
