"""Strategy catalogue for the Linck UI: what each strategy is, plus how
to render each of its config keys as a labelled, explained form field.

Everything is data. To describe a new strategy, add an entry to
STRATEGIES; to explain a config key (in every strategy that has it), add
it to FIELDS. Anything not listed still works — the label is derived
from the key and the field type from its default value — so a strategy
registered tomorrow shows up correctly before anyone writes metadata.
"""

from __future__ import annotations

import re
from typing import Any

from ..strategies.registry import list_strategies

# label, style (Intraday/Positional), risk, typical hold
STRATEGIES: dict[str, dict[str, str]] = {
    "strangle_monthly_v2": {"label": "Monthly strangle", "mode": "positional", "risk": "Undefined unless hedging is on", "hold": "Days to weeks, cycles on checkpoints"},
    "intraday_dtt_simple": {"label": "DTT straddle", "mode": "intraday", "risk": "Undefined, per-leg stop", "hold": "Same day"},
    "intraday_dtt_adjusted": {"label": "DTT adjusted", "mode": "intraday", "risk": "Undefined, break-even stop", "hold": "Same day"},
    "intraday_dtt_advanced": {"label": "DTT advanced", "mode": "intraday", "risk": "Undefined, break-even stop", "hold": "Same day"},
    "calendar_btst": {"label": "Calendar BTST", "mode": "positional", "risk": "Calendar spread", "hold": "Overnight"},
    "pivot_supertrend": {"label": "Pivot SuperTrend", "mode": "intraday", "risk": "Directional", "hold": "Same day"},
    "pivot_supertrend_options": {"label": "Pivot SuperTrend options", "mode": "intraday", "risk": "Undefined, single short leg", "hold": "Same day"},
    "pivot_supertrend_options_inverse": {"label": "SuperTrend option buyer", "mode": "intraday", "risk": "Defined, premium paid", "hold": "A few candles"},
    "trend_credit_spread": {"label": "Trend credit spread", "mode": "positional", "risk": "Defined, spread width", "hold": "Up to the weekly expiry"},
    "weekly_ema_st_spread": {"label": "Weekly EMA spread", "mode": "positional", "risk": "Defined, hedged short", "hold": "Several days"},
}

# key -> label, help, unit (prefix "₹" or suffix text), optional choices
FIELDS: dict[str, dict[str, Any]] = {
    "lots_per_trade": {"label": "Lots", "help": "Lots per leg on each entry", "suffix": "lots"},
    "entry_time": {"label": "Entry time", "help": "IST, when the strategy may enter"},
    "exit_time": {"label": "Exit time", "help": "IST, next-day unwind time"},
    "force_exit_time": {"label": "Square-off time", "help": "IST, every leg exits by this time"},
    "expiry_day_force_exit_time": {"label": "Expiry-day exit", "help": "IST, backstop exit on expiry day"},
    "market_open_time": {"label": "Market open", "help": "IST, first candle the strategy reads"},
    "entry_signal_cutoff_time": {"label": "Last entry signal", "help": "IST, no new entries after this"},
    "eod_check_time": {"label": "Daily check time", "help": "IST, the fixed daily lagging-side check"},
    "checkpoint_exit_not_before": {"label": "Checkpoint not before", "help": "IST, a checkpoint hit waits until this time"},
    "instrument": {"label": "Index", "help": "Underlying index"},
    "options_underlying": {"label": "Index", "help": "Underlying index for the options"},
    "symbol": {"label": "Signal symbol", "help": "Index the signal is read from"},
    "instrument_tokens": {"label": "Signal token", "help": "Kite token that drives this deployment's clock"},
    "expiry_selector": {"label": "Expiry", "help": "Which contract to trade", "choices": ["THIS_WEEK", "NEXT_WEEK", "THIS_MONTH", "NEXT_MONTH"]},
    "pivot_type": {"label": "Pivot type", "help": "How pivot levels are computed", "choices": ["classic", "fibonacci", "camarilla", "woodie"]},
    "atr_smoothing": {"label": "ATR smoothing", "help": "SuperTrend's ATR average", "choices": ["wilder", "sma", "ema"]},
    "convergence_mode": {"label": "Convergence handling", "help": "What happens when the strangle converges to a straddle", "choices": ["fixed_stop", "trailing_stop", "active_management"]},
    "max_adjustments": {"label": "Max adjustments", "help": "Per cycle; 0 disables adjustments"},
    "max_trades_per_day": {"label": "Max trades a day", "help": "Entries allowed per session"},
    "combined_premium_profit_pct": {"label": "Profit target", "help": "Combined premium decay that books profit, as a fraction (0.10 = 10%)"},
    "per_leg_stop_loss_pct": {"label": "Per-leg stop", "help": "A leg up this much exits both, as a fraction (0.40 = 40%)"},
    "checkpoint_profit_pct_of_capital": {"label": "Checkpoint profit", "help": "Cycle profit that triggers a checkpoint, as a fraction of capital"},
    "strike_selection_capital_pct": {"label": "Strike premium target", "help": "Premium per leg as a fraction of capital"},
    "long_days_target_percentage": {"label": "Long-dated premium target", "help": "Used instead when entering more than 29 days out"},
    "strike_liquidity_tolerance_pct": {"label": "Liquidity tolerance", "help": "How far from the target premium a more liquid strike may be, as a fraction"},
    "adjustment_trigger_ratio": {"label": "Adjustment trigger", "help": "Weaker side ÷ stronger side that triggers an adjustment"},
    "adjustment_band_min": {"label": "Adjustment band low", "help": "Lower edge of the replacement premium band"},
    "adjustment_band_max": {"label": "Adjustment band high", "help": "Upper edge of the replacement premium band"},
    "adjustment_strike_window": {"label": "Strike window", "help": "Strikes searched either side for a replacement leg"},
    "enable_hedging": {"label": "Protective hedges", "help": "Buy wings against the short legs"},
    "enter_immediately_on_deploy": {"label": "Enter on deploy", "help": "First entry ignores entry time"},
    "catch_up_late_entry": {"label": "Catch up late entry", "help": "Enter after entry time if started late"},
    "switch_to_next_week_on_expiry": {"label": "Roll on expiry day", "help": "Use next week's contract on expiry day"},
    "force_close_at_contract_expiry": {"label": "Close at expiry", "help": "Backstop exit when the contract expires"},
    "profit_target_pct": {"label": "Profit target", "help": "As a fraction of the credit received"},
    "stop_loss_multiple": {"label": "Stop loss", "help": "Multiple of the credit received", "suffix": "×"},
    "target_sell_premium": {"label": "Target premium", "help": "Premium sought for the short leg", "prefix": "₹"},
    "hedge_width_points": {"label": "Hedge width", "help": "Hedge distance from the short strike", "suffix": "pts"},
    "capital": {"label": "Strategy capital", "help": "Capital the strategy sizes from", "prefix": "₹"},
    "capital_per_lot": {"label": "Capital per lot", "help": "Capital set aside per lot", "prefix": "₹"},
}

_ACRONYMS = {"pct": "%", "eod": "EOD", "atm": "ATM", "atr": "ATR", "ema": "EMA", "st": "ST", "btst": "BTST", "dtt": "DTT"}


def humanize(key: str) -> str:
    words = [_ACRONYMS.get(w, w) for w in key.split("_")]
    s = " ".join(words)
    return s[:1].upper() + s[1:]


def field_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str) and re.fullmatch(r"\d{1,2}:\d{2}", value):
        return "time"
    if isinstance(value, (list, dict)):
        return "json"
    return "text"


def describe_fields(default_config: dict) -> list[dict[str, Any]]:
    out = []
    for key, default in default_config.items():
        meta = FIELDS.get(key, {})
        ftype = "choice" if meta.get("choices") else field_type(default)
        out.append({
            "key": key, "label": meta.get("label") or humanize(key), "help": meta.get("help", ""),
            "type": ftype, "default": default, "choices": meta.get("choices"),
            "prefix": meta.get("prefix", ""), "suffix": meta.get("suffix", ""),
        })
    return out


def _index_of(cfg: dict) -> str:
    v = cfg.get("instrument") or cfg.get("options_underlying") or cfg.get("symbol") or "NIFTY"
    return str(v).replace(" 50", "").upper()


def catalog() -> list[dict[str, Any]]:
    out = []
    for s in list_strategies():
        meta = STRATEGIES.get(s["name"], {})
        cfg = s["default_config"] or {}
        mode = meta.get("mode") or ("intraday" if s["name"].startswith("intraday") else "positional")
        out.append({
            "name": s["name"], "label": meta.get("label") or humanize(s["name"]),
            "description": s["description"], "mode": mode, "risk": meta.get("risk", ""),
            "hold": meta.get("hold", "Same day" if mode == "intraday" else ""),
            "index": _index_of(cfg), "default_config": cfg, "fields": describe_fields(cfg),
        })
    return out
