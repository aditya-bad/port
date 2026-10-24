# 09 · What's next, what's open, what's unverified

## Not deployed

Everything since the owner's last deploy is only on the branch
`claude/nifty-shop-port-setup-xkdq22`. The owner deploys when they choose
(`./redeploy.sh`). Latest migrations that will run on deploy include 0034–0037
(strategy_issues + cash constraint dropped, day-check dismissals, alert
dismissals, shadow_checks + freeze_limits). After deploying, the owner may
run `custom_scripts/shadow_backfill.py --dry-run`.

## Offered, waiting for the owner

1. **P0**: latest-value delivery in today's app. The broadcaster keeps one slot
   per instrument per subscriber with one batch in flight and routes ticks by
   instrument, instead of a 1,000-batch queue per subscriber. Test: after a
   stall the next tick processed is the newest; fills and events still in
   order. Small change in `app/broadcaster.py` / `app/deployments/runner.py`.
2. **Journal, batch numbers, snapshots in today's runner**, so repair and the
   day check replay exactly what a strategy saw.
3. **Benchmark NATS JetStream publish/ack and a gRPC stream hop** so those
   rows become measured (needs `nats-server` and gRPC libs to be downloadable).

## Parked by the owner (don't start without asking)

What-if replay (needs the full option chain stored); account-level loss
limit; backtester / parameter sweep; Telegram alerts; tax export; Greeks
dashboard (wanted "in future scope"); capital allocation view; event
calendar; record-quality grade on Compare; "what each rule earns" ledger;
change-impact report before redeploys; day check explaining its own
differences; Geek views for inverse SuperTrend, calendar BTST, trend credit
spread, weekly EMA spread; Android parity; the strangle's `active_management`
counter reset and post-convergence EOD question.

## Known problems not fixed

- **Thin BSE monthly strangles** (BANKEX/SENSEX): legs that never trade keep
  the stale-price guard closed, so the strangle can't take profit, adjust or
  stop, and its paper prices aren't tradeable. Options offered: leave it;
  price an untraded leg from the quote mid; stop running strangles on those
  monthlies. Owner closed them by hand on 21 Oct and said repair should improve.
- Repair: a leg that traded earlier in the day but not during a stall keeps
  its last price (could be stale for thin legs).
- Repair and the day check, where real data can still part from live (the
  logic itself matches, tested): Kite keeps no order-book history, so a
  replayed quote has a one-tick spread and every strike passes the spread half
  of the strangle's liquidity bar (open interest comes from the 1-minute
  candles, as live); a minute's first tick uses each contract's own 1-minute
  open; strikes listed after the day are in today's contract list. Contracts
  that expired since the day are refused, but only when the app stored one
  of that expiry (any deployment holding one is enough).
- Until the fill-time fix is deployed, pivot_supertrend_options(_inverse),
  trend_credit_spread and weekly_ema_st_spread stamp fills at their signal
  candle's start (5 min early; 1 h for the weekly spread), and the day check
  flags their correct days as mismatches. Days flagged that way can be ignored.
- Equity snapshots and marks recorded during a stall keep stale prices; repair
  doesn't rewrite them.
- Compare groups use full capital for the whole history even before a member
  started (return % slightly low early).
- Linck's account equity curve counts closed trades only (classic's included
  open positions).

## Unverified facts (check before relying on them)

- NSE `qtyfreeze.csv` format and URL (NSE unreachable from the sandbox); NSE
  now sets index freeze limits by circular. SENSEX/BANKEX limits must go in
  `shadow.freeze_qty` (Zerodha's Nov 2025 bulletin: 1,000 / 900).
- Kite basket-margin response read as `final.total`.
- Order-rate limits assumed 10/s and 200/min; legs assumed 0.4 s apart.
- Real feed update rates per instrument and the 09:15 burst (10× assumed).
- NATS and gRPC hop costs; gVisor round-trip cost.
- The SENSEX spot token 265 in `tokens.json` came from general knowledge.
- Broker SDK lists change; re-check each broker's docs.
- The Android app has never been compiled.

## Flaky tests in this sandbox

- With two browser shards the gate fails at random under load; use
  `E2E_SHARDS=1` (same tests).
- The Geek SuperTrend order-book check and a Compare link test have flaked and
  passed on re-run.
- A repair apply/undo test once recorded one extra Activity event; never
  reproduced; it now prints the events it saw.
- Postgres in the sandbox sometimes stops: `service postgresql start`.
