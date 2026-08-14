"""
live_deploy — shared trade-reason metadata builder.

Every strategy's `runner.buy(...)`/`runner.sell(...)` call site is
expected to pass a `metadata` dict with the same five-field shape
(`trigger`, `action`, `trigger_values`, `target_basis`, `resulting_state`)
strangle_monthly_v2's own `_trade_meta`/Section 12 established first —
see that module for the reference implementation this was extracted
from. The POINT of the schema is that a trade log line is independently
checkable on its own: `trigger` names the exact rule that fired,
`trigger_values` carries the actual numbers that made it true (not just
that it fired), `target_basis` records what a strike/premium selection
was aiming for vs. what it actually got (only where a selection genuinely
happened), and `resulting_state` is a compact snapshot of the book
immediately after this fill.

WHY THIS LIVES IN ITS OWN MODULE rather than being copy-pasted per
strategy (the fate `resolve_atm_straddle_legs` avoided by living in
intraday_dtt_simple.py): unlike that helper, this dict-shape builder
isn't STRATEGY logic that happens to be shared by two files that trade
the same instrument — it's a pure formatting/shape concern used by every
strategy in this package, single-position and multi-leg alike. No
existing strategy file "owns" it any more than another does, so it gets
its own small module instead of being hung off whichever file was
written first.

WHAT THIS DELIBERATELY DOES NOT DO: decide what `resulting_state` looks
like. That's genuinely different per strategy (single position vs.
variable-length multi-leg dict), so each strategy builds its own
resulting_state dict/snapshot and just passes it in here — this module
only assembles the common envelope around it, exactly once.
"""

from typing import Optional


def build_trade_meta(
    trigger: str,
    action: str,
    trigger_values: Optional[dict] = None,
    resulting_state: Optional[dict] = None,
    target_basis: Optional[dict] = None,
    **extra,
) -> dict:
    """
    Build a fill's `metadata` dict in the standard five-field shape.

    `trigger` — the specific rule that caused this trade (not a generic
        category like "adjustment" or "exit_signal" — the strategy's own
        real trigger name, e.g. "pivot_break_long", "leg_spike_stop").
    `action` — open/close plus enough to identify which position (a
        plain string is fine for a single-position strategy; multi-leg
        strategies typically pass something like "sell_open:CE").
    `trigger_values` — the actual numbers that made the condition true
        at this moment, not just that it fired. Defaults to `{}` (never
        omitted entirely — the UI's _tradeMetaHtml only skips rendering
        an EMPTY dict, so an intentionally-empty one still renders as
        "nothing recorded" rather than looking like a missing field).
    `resulting_state` — a compact snapshot of the position immediately
        after this fill. Same default-to-`{}` reasoning as above.
    `target_basis` — OMITTED entirely (not even as `{}`) unless the
        caller passes one, since it doesn't apply to every strategy (see
        pivot_supertrend, which trades the underlying directly) and
        forcing an empty dict onto every call site would misrepresent
        "this trigger never selects a strike/premium" as "a selection
        happened but nothing about it was recorded".
    `**extra` — any additional strategy-specific keys (e.g. "leg",
        "strike", "exchange", "leg_role", "entry_spot",
        "entry_candle_date") merged in verbatim on top. These are often
        RESUME-CRITICAL — read back by a strategy's own `on_start()` to
        reconstruct in-memory state after a restart — so callers must
        keep passing whatever keys their own resume logic depends on,
        never renaming or dropping one during this retrofit.
    """
    meta = {
        "trigger": trigger,
        "action": action,
        "trigger_values": trigger_values or {},
        "resulting_state": resulting_state or {},
    }
    if target_basis is not None:
        meta["target_basis"] = target_basis
    meta.update(extra)
    return meta
