# 03 · Market facts and the strategies

Each strategy file's module docstring (in `app/strategies/`) is the full spec,
and many are long. This file is the summary with the things that bit us.
Read the docstring before changing a strategy.

## Market facts the code depends on

- **Instruments**: index options on NIFTY, BANKNIFTY (NSE, segment `NFO`) and
  SENSEX, BANKEX (BSE, segment `BFO`). No equity or futures strategy exists.
- **Session**: 09:15–15:30 IST. NSE sends indicative pre-open ticks
  09:00–09:15, so every candle signal is gated by `market_open_time` (09:15)
  using the **candle's own bucket time**, not the wall clock. The runner ignores
  ticks outside 09:15–15:40 and ticks older than 5 minutes.
- **Closing auction**: constituents enter a call auction at 15:15, which is why
  the strangle's EOD check runs at **15:13**.
- **ATM** is taken off the **future**, not spot (options price off the
  forward). Futures for a leg: same expiry if listed, else that month's
  monthly, else none (e.g. a next-December option).
- **Expiries**: never assume a weekday; always check the resolved expiry date.
  The weekly expiry day has changed before.
- **Lot sizes, strike steps, tick size**: from the live instrument master,
  never hard-coded. Tick size 0.05.
- **Freeze limits** (largest quantity per order): NSE daily file
  (`qtyfreeze.csv`) with config override `shadow.freeze_qty`; NSE now sets
  index limits by circular. SENSEX/BANKEX aren't in NSE's file (Zerodha's Nov
  2025 bulletin: SENSEX 1,000, BANKEX 900; set them in config).
- **Kite limits**: about 3 REST requests a second for history (the app spaces
  every call ≥0.35 s), 3 WebSockets × 3,000 instruments per key, orders about
  10/s and 200/min (assumed in shadow mode). Full tick mode carries 5-level
  depth and the day's OHLC and previous close.
- **Thin BSE monthlies**: far strikes on SENSEX/BANKEX monthlies may never
  trade. Kite history has nothing for them; a paper fill there is at a price
  nobody traded at. The strangle's stale-price guard then never lifts, so
  those strangles can't take profit, adjust or stop. This is known and
  unresolved (see `09-open-items.md`).
- **India VIX**: Nifty-based 30-day IV, annualised; daily expected move ≈
  VIX/√365 %. Only a stand-in for BankNifty/Sensex/Bankex.
- **Charges**: Zerodha flat ₹20 per executed order, STT 0.15% of premium on
  the option sell side (from 1 Apr 2026), NSE exchange charge 0.03553%, SEBI,
  GST 18%, stamp duty rounded to the rupee. On small trades the flat ₹40 per
  round trip is most of the cost; that is real, not a bug.

## The strategies

| Strategy | Mode | One line |
|---|---|---|
| `intraday_dtt_simple` | intraday | 10:00 ATM short straddle; per-leg stop 40%; combined target 10% decay; force exit 15:00 |
| `intraday_dtt_adjusted` | intraday | Same entry; adds legs to the decaying side when the smaller side ≤ 50% of the bigger (lifetime cap 2); unwinds the cheapest leg on reversal; break-even band on spot |
| `intraday_dtt_advanced` | intraday | Subclass of adjusted: rolls (close cheapest, sell new) instead of stopping at the cap; `max_adjustments` is a concurrent cap here; configurable break-even multiplier |
| `strangle_monthly_v2` | positional | Monthly checkpoint-cycling short strangle, the most complex; detailed below |
| `calendar_btst` | overnight | 15:20 sell this-week ATM straddle, buy next-week ATM straddle, same strike; exit 09:20 next day |
| `pivot_supertrend_options` | intraday | 5-min close beyond R1–R3 (ST green) sells ATM PE; below S1–S3 (ST red) sells ATM CE; exit on SuperTrend(7,3) flip or 15:00; max trades a day |
| `pivot_supertrend_options_inverse` | intraday | Buys ATM option on a SuperTrend flip, holds N candles |
| `trend_credit_spread` | positional | Weekly defined-risk credit spread on a SuperTrend flip, once per flip, strikes by steps from ATM |
| `weekly_ema_st_spread` | positional | EMA(20) + SuperTrend on 1-hour NIFTY candles (09:15-anchored), hedged weekly credit spread, exit on ST flip only; several constants marked PROVISIONAL |

`pivot_supertrend.py` is the shared signal engine (pivots, SuperTrendState,
CandleAggregator, seeding from Kite); it is no longer deployable on its own.

Shared rules across the family: never skip a trading day because of expiry
(`switch_to_next_week_on_expiry` picks the contract instead); "late start"
catch-up entry is configurable; exits are never blocked by cash; once-a-day
entries retry every minute for 30 min if Kite fails, reporting "entry late"
then "entry skipped".

### strangle_monthly_v2 (the one the owner cares most about)

Defaults in brackets. Section numbers match the docstring.

1. **Contract**: day 1–10 of the month → this month's expiry, else next
   month's; also next month if this month's expiry is ≤14 days away. Locked as
   `contract_expiry` for the whole position. **Expiry backstop**: close
   everything on expiry day.
2. **Entry** at `entry_time` [10:00].
3. **Sizing**: per-leg target premium = capital × `strike_selection_capital_pct`
   [3%] ÷ lot size ÷ 2; [5%] (`long_days_target_percentage`) when expiry is
   >29 days away and in a different month, for liquidity. Quantity =
   `lots_per_trade` × lot size, fixed (never compounds). **Liquidity
   tie-break**: among strikes within ±`strike_liquidity_tolerance_pct` [5%] of
   the target, the highest-OI liquid one wins (21200 over 21150).
4. **Checkpoint**: cycle P&L (booked + open) ≥ `checkpoint_profit_pct_of_capital`
   [0.5% of capital] → flatten and immediately re-enter a fresh cycle.
   `monthly_target_pct` [2%] is informational only. Optional
   `checkpoint_exit_not_before` time floor.
5. **Continuous adjustment**: smaller side ≤ 50% of the bigger → close the
   cheapest leg on the **smaller (decaying, winning)** side and sell a
   replacement so that side's total = midpoint of [80%, 95%] (87.5%) of the
   bigger side. Always replaces, never grows the leg count. The losing side is
   never touched.
6. **EOD check at 15:13**: smaller < 80% of bigger → same replace, aiming at
   the midpoint of [80%, 95%]. **EOD roll-out** (`eod_rollout_instead_of_straddle`,
   default on): if that roll would reach the other side's strike (making a
   straddle right before the overnight gap), roll the **tested** side out
   instead (a small debit). Recorded as its own event type "EOD roll-out".
7. **Strike-cross cap**: a roll never crosses the other side; it snaps to the
   other side's strike, which is **convergence** (now a straddle, sticky for
   the cycle). Modes: `fixed_stop` [default, 10% above combined premium at
   convergence; rolls stop], `trailing_stop`, `active_management` (borrows the
   DTT-adjusted methods). Known gap: under `active_management` the counter is
   usually already spent, and whether EOD rules should still apply after
   convergence is undecided.
8. **Priority each tick**: expiry backstop → checkpoint → convergence stop →
   EOD check → continuous adjustment.
9. **Stale-price guard**: no decision while any open leg hasn't traded since
   today's 09:15 (`dispatcher.last_price_times`); each trigger is re-checked on
   a live quote (bid/ask mid) before acting, 15 s cooldown if refuted. Added
   after yesterday's CE price triggered false rolls at 09:15.
10. A roll now finds its replacement before closing the old leg; if it can't,
    the old leg stays and an issue is reported.
11. Optional hedging (`enable_hedging`, off): buy protective longs, hedge
    first when rolling.

Worked example the owner asked about (keep as a reference): CE1 and PE1 sold
at 100. At 15:00 CE1 75 / PE1 150 → the 50% rule closes CE1 and sells CE2 at
about 131 (87.5% of 150). At 15:13 CE2 155 / PE1 145 → 93.5%, no EOD action.
At CE2 155 / PE1 120 (77.4%) → EOD closes PE1 and sells a PE at about 135.6,
unless that hits CE2's strike, in which case the roll-out rule rolls CE2 out to
about 137 instead. "Tested" is decided by which way the market moved, not by
which leg is newer.
