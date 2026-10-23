# 11 · History, in order

From `git log` (328 commits, 7 Aug – 23 Oct 2026). For details of the first
117 steps see `live_deploy/README.md` ("What's here (Step N…)" sections).

## August: from backtests to a live paper platform

- **7 Aug** NiftyShop: OHLCV fetch, configurable backtest engine, analytics UI
  with XIRR and sweeps, generic multi-universe pipeline.
- **11 Aug** `tg_int_st_pp`: Nifty 5-min fetch, Pivot + SuperTrend(7,3).
  `live_deploy` begins: one Kite connection dispatcher, persistent resumable
  paper deployments (then Neon Postgres), Kite login UI, strategy registry,
  options resolver, pivot SuperTrend selling options.
- **12–13 Aug** App auth; `intraday_dtt_simple`, inverse SuperTrend,
  `intraday_dtt_adjusted`, `intraday_dtt_advanced`, `strangle_monthly_v2`;
  first real multi-view UI.
- **14–16 Aug** Trade-reason logging, instrument browser, manual Kite login,
  "Bazaar Ledger" UI, ticker bar, deploy form, `calendar_btst`, multi-user
  auth, sessions, redeploy.sh, Flatten All, presets, CSV export, dark mode,
  Portfolio, Compare, Reports, alerts, leaderboard, heatmaps, state
  persistence.
- **17–20 Aug** Real bugs from live use: stale after-hours ticks, container
  timezone (trades 5.5 h late), restart looking like a late start, startup
  race, WebSocket → SSE, PWA install, wrong-exchange SENSEX options,
  SuperTrend drift after WS gaps (self-seeding), one-candle-late option price.
- **20–25 Aug** Equity curves per day, M2M per episode, Compare scorecard and
  leaderboards, redeploy from UI, `weekly_ema_st_spread`, ATM off the future.
- **27–31 Aug** Per-deployment flatten, Kite 429 fixes, move from Neon to a
  local Postgres container.

## September

- Strangle never trading (empty tokens) fixed; one broken deployment can't
  crash startup; Kite 429 storm → one throttle for every REST call; remove
  today's trades script; Kite disconnect alerts; strangle EOD "always replace,
  never grow"; roll strike-cross cap; NEXT_MONTH override; ticks outside
  trading hours rejected; `trend_credit_spread`; native Android app phase 1;
  Remove Trades; log-only liquidity guard.

## 1–11 October

- Missed force-exit emergency backfill (a Kite outage left straddles open);
  disconnect alert every minute; Mark as holiday; Reports yearly/all-time;
  Android parity for three features; checkpoint exit floor; analytics P&L
  mismatches (episodes vs calendar) fixed; Tools page; Zerodha-exact brokerage;
  global "with brokerage" toggle and charges backfill (charges on small trades
  are mostly the flat ₹20/order: real, not a bug).

## 15–17 October

- Strangle: liquidity tie-break and stale-price guard; slippage recorded with
  every fill and the three-step P&L toggle; Cycle Replay; 1-minute leg marks.
- A "Desk" redesign built and **rejected** ("AI slop"); a design brief written
  for Claude Design; the owner designed **Linck** there; v2 API and the Linck
  UI built, then sign-in with Connect Kite, missing classic features, session
  sheet, wizard progress.
- Legs carried past the square-off: three causes fixed plus a safety net and a
  fix script; index candles recorded; "not running" alert; strangle EOD
  roll-out.

## 18–20 October

- Linck is the installed PWA; one-command deploy gate with JS and browser
  tests (coverage 31% → 82%); crash-proof strategies proven by kill tests;
  one 1-minute candle store (options only, then futures); 15:45 backfill with
  progress UI; Geek mode for the strangle, then every deployment, SuperTrend
  and DTT views; logo rounds ending in the terminal-candle mark; parallel
  tests (~9 min → ~3 min); holiday spike, red losses, sign-in holiday, header
  overflow fixes; Compare groups; running cycle shown; Geek cycle page A to Z;
  Account redesigned; emergency stop moved out of the header; Strategy page.

## 21 October

- Every Geek chart hoverable; DTT adjusted/advanced Geek views; trade journal;
  market context on every fill and the Conditions view; strategy health
  alerts; Week in review; India VIX views (nothing stored); IV vs VIX; P&L
  split; VIX spike alert; VIX exposure.
- **Prod incident**: Kite ticks silently stopped at 12:15 IST while "connected";
  fixed with stall detection and self-reconnect. Stall repair CLI built and
  hardened through a long series of fixes against real Kite data (BFO
  contracts with no trades, futures with no trades, request counts, wrong
  date after midnight). Lesson in `10-working-agreement.md`.
- Geek Replay, then a bar-by-bar replay; futures stored and shown; repairs
  shown in the UI.

## 22 October

- Repair in the website in four phases: health monitor and minute alerts; the
  end-of-day check in a worker process; the Repair page (plan, proof, paths,
  draft, push, undo); repair marks everywhere.
- Architecture docs: "Linck at scale"; the platform plan; technical
  architecture v1 → v2 (components, protocols, deployment, languages) →
  diagrams with moving journeys → ticks on conflated gRPC streams, Aeron
  dropped.
- Replay rebuilt as a payoff replay, then on one screen; every strategy made
  fault-tolerant and self-reporting (fault matrix 108 cases); Repaired badge by
  cycle.

## 23 October

- Repair page without the minute monitor; alerts synced across devices and an
  Alerts panel; cycle P&L everywhere; shadow mode in four stages; doc on
  brokers, languages and load (with benchmarks); doc and prototype for the
  any-language runtime; this handoff folder.
