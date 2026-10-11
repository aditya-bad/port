"""
live_deploy — estimated brokerage/charges, Python side.

Single source of truth is static/js/calculators.js's Calculators.brokerage
(the Tools page's own Brokerage calculator) — this module is a DELIBERATE,
line-for-line port of that function's `options` segment, not an
independent re-derivation, so the two never drift apart on the same
trade. See that file's own BROKERAGE_RATES comment for how these rates
were verified (a live side-by-side check against zerodha.com's own
calculator) and what isn't modeled (STT on an option exercised/expired
ITM, i.e. held to expiry rather than squared off).

Every strategy in this codebase trades options exclusively via
OptionsResolver (strangle_monthly_v2, the intraday_dtt family,
pivot_supertrend_options(_inverse), calendar_btst, trend_credit_spread,
weekly_ema_st_spread — grep app/strategies/*.py for OptionsResolver to
confirm) — there is no equity or futures strategy anywhere in this repo.
So unlike the generic, segment-parameterized JS calculator (built for
someone pricing an arbitrary trade), this module hardcodes the options
formula rather than carrying unused equity_intraday/futures branches
that nothing here would ever call. If a non-options strategy is ever
added, port the other two BROKERAGE_RATES segments from calculators.js
the same way, parameterized by segment, at that point.

Charges are computed per POSITION, not per individual fill: the whole
buy-side value as one order, the whole sell-side value as one order —
same approximation the Brokerage calculator itself uses. Every strategy
in this codebase opens and closes a leg in exactly one fill each (no
same-direction averaging-in, no partial exits — see queries.record_fill's
own ClosingQtyMismatch), so this is exact for real data here, not an
approximation in practice.
"""

# Mirrors calculators.js's BROKERAGE_RATES.options and SEBI_CHARGE_PCT/
# GST_PCT exactly -- verified 2026-10-11 against two live Zerodha
# brokerage-calculator trades (qty 400 and 40000, buy 100/sell 110);
# see that file's own comment for the full verification note. Update
# BOTH files together if a rate ever changes.
_BROKERAGE_FLAT_PER_ORDER = 20.0          # flat Rs 20/executed order, not percentage-based
_STT_PCT = 0.0015                         # 0.15% of premium, sell side only (w.e.f. 1 Apr 2026)
_EXCHANGE_PCT = 0.0003553                 # NSE: Rs 35.53 per lakh of premium turnover
_STAMP_DUTY_PCT = 0.00003                 # 0.003% of premium, buy side only
_SEBI_CHARGE_PCT = 0.000001               # Rs 10 per crore, both sides
_GST_PCT = 0.18                           # on (brokerage + exchange + SEBI) only, never on STT/stamp duty


def estimate_options_charges(buy_price: float, sell_price: float, qty: float) -> float:
    """Total estimated charges (brokerage + STT + exchange transaction
    charges + SEBI charges + GST + stamp duty) for one options position
    squared off via a buy leg at `buy_price` and a sell leg at
    `sell_price`, `qty` lots-worth total (already lots × lot size) —
    direction-agnostic, same as the JS calculator: pass the position's
    own buy-side price as `buy_price` and sell-side price as
    `sell_price` regardless of whether it was opened long or short (see
    callers for the side-aware mapping).

    Rounds only stamp duty to the nearest whole rupee (matching
    Zerodha's own display), exactly like calculators.js's brokerage().
    """
    buy_value = buy_price * qty
    sell_value = sell_price * qty
    turnover = buy_value + sell_value

    brokerage = _BROKERAGE_FLAT_PER_ORDER * 2   # once per executed order -- buy + sell
    stt = _STT_PCT * sell_value
    exchange = _EXCHANGE_PCT * turnover
    sebi = _SEBI_CHARGE_PCT * turnover
    gst = _GST_PCT * (brokerage + exchange + sebi)
    stamp_duty = round(_STAMP_DUTY_PCT * buy_value)

    return brokerage + stt + exchange + sebi + gst + stamp_duty
