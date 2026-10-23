# 04 · Features, how they behave, and why

Grouped by the question they answer. "Why" records the owner's reason or the
incident that caused it.

## Standards that apply everywhere

- **Cycle P&L, never day P&L** (owner's rule, 23 Oct). Intraday: the cycle is
  the day. Positional: entry → adjustments/rolls → exit. If a positional cycle
  exits and a new one enters on the same day, that day shows the new cycle with
  "+ closed today ₹x" under it; from the next day only the new cycle shows.
  Charts and reports book a positional cycle's P&L on the day it closes.
  (Exception kept on purpose: Geek Volatility's "daily P&L vs VIX change".)
- **P&L basis switch** in the header: Gross → After charges → After charges +
  slippage. Shared by Linck and classic. Every figure follows it.
- **Colours**: green/red only for profit/loss; yellow only for "needs you";
  buttons are ink. A loss is never drawn green (several bugs were exactly that,
  e.g. "−357% there" in green, equity area green below zero).
- **Money formatting**: −₹1,574.30; Indian grouping (₹1,23,456, ₹1.2L).
- **Charts**: one y-axis only (stack panels instead); every chart responds to
  hover/tap (enforced by a test).
- **Phone**: no sideways scroll at 320–1920 px in all three themes (tested);
  no overlapping text.
- **Risky actions** say exactly what will happen first. Pause/resume is instant
  with Undo; Close positions lists the orders; Close everything needs typing
  CLOSE ALL; delete needs the name typed.
- **Repaired badge** follows cycles: on deployment-level places only while the
  repaired cycle is current; permanently on that cycle's row in
  reports/analytics/Geek. "Flagged" (day check found a problem, not repaired)
  stays until repaired or dismissed.

## Watching (Desk and alerts)

- **Desk**: "things need you" first (on phones above the P&L); headline cycle
  P&L = sum of lanes; **session lanes** (one P&L line per deployment across
  09:15–15:30 with fill/adjust/error markers; the signature component); open
  positions grouped by deployment; index strip; "If VIX +1" exposure.
- **Holdings table**: index, strike, side, entry, LTP flashing, Live dot, or
  "Not streaming · last known prices" (greyed last recorded prices).
- **Alerts panel** in the header: every alert, action + Dismiss, Dismiss all,
  restore. Dismissals are stored per user on the server, so they sync across
  devices.
- **Health alerts**: ops issues alert every minute until fixed (Kite not
  logged in by 09:05, prices stalled 90 s, deployment not running/silent/
  erroring, legs with no trade today, export failed). Strategy self-reports:
  critical alert every minute, warnings once, notes never. Strategy health
  alerts (drift vs its own history, ≥30 trades): win rate down, bigger losses,
  worst trade, slippage doubled, deepest drawdown, trading less.
- **VIX spike** (≥10% over last close) on the Desk and pushed once a day.
- **Mark as holiday** mutes pushes and market-hours checks until 07:00 next
  day; the sign-in page shows "Market holiday".
- Push notifications via web push (VAPID), titled "Linck".

## Controlling

- Pause / resume / close positions (keeps the deployment running; the
  strategy may re-enter) / stop / delete / remove current trade / edit
  settings (only while paused) / copy as new / restart (for "not running").
- **Emergency stop** (Account → Emergency stop): close everything, shorts
  before longs, then pause all.
- Deploy wizard with presets, numbered steps, created paused or started.

## Understanding one deployment

- Deployment page tabs: **Now** (holdings, strategy engine card, "Against the
  index" futures panel with basis, distances, delta, ±move scenarios, shadow
  panel, repair notes), **Performance**, **Cycles** (every day or cycle, the
  running cycle pinned on top; click opens the session sheet: underlying over
  P&L, markers, legs, orders), **Trades & replay**, **Settings**.
- **Geek mode** (button or `G`, full-screen transition, read-only, follows the
  theme): Overview, Positions, Trades, Performance, Replay (R), Engine (E: each
  rule and how close it is to firing, next move in words), Decisions (D: every
  check, acted or not), Journal, strategy-specific views (strangle: Cycle map,
  Cycles with Spotlight / "is this cycle normal?" / day-by-day, one Cycle page
  A to Z, Adjustments "did rolling pay?", Premium & decay, Entries; SuperTrend:
  Signals, Trade, Exits, Days; DTT: Straddles, Straddle, Exits & timing; DTT
  adjusted/advanced: Sessions, Session, Adjustments), Conditions (K: MAE/MFE,
  results by gap, range, move, strike distance, ATR, candle size…),
  Volatility (V: VIX at entry, ₹ per VIX point, IV vs VIX, P&L split into time
  decay / move / volatility), Live-ready (L, shadow), Settings at work.
  Inverse SuperTrend, calendar BTST, trend credit spread and weekly EMA have
  only the shared views.
- **Replay** (Opstra/Sensibull style): payoff chart (expiry curve + "today"
  line from each leg's IV backed out of its stored price), index and future
  markers, positions table that grows (closed legs greyed with entry/exit
  time, price, P&L, reason), steps 1m–1d limited to what data supports,
  autoplay, all in one screen at 1440×900.
- **Journal**: notes on a day, a deployment, a trade, or attached to nothing;
  tags with their average P&L.

## Comparing

- **Leaderboard**: month/year/all-time boards per metric, ranked table with
  plain-word labels, flags (too new, losing streak, quiet, repaired cycles,
  flagged), Live-ready column.
- **Compare**: up to 5 items head to head; **groups**: combine deployments
  into one portfolio (daily P&L summed, trades pooled, capital summed, the
  group's own drawdown), "By strategy" tiles, rename groups, half vs half of
  the same strategy.
- **Strategy page** (`#/s/:name`): all deployments of a strategy combined,
  untick any; settings in use per deployment.
- **Reports**: day/week/month/year/all-time statements, by strategy and by
  deployment. **Week in review** page plus a Saturday 09:00 push.

## Trust: are the numbers true?

- **Charges** (Zerodha-exact) and **slippage** (order book at the fill).
- **Shadow mode** (`app/shadow.py`): every paper fill checked as the LIMIT
  order live would send (price ± 0.5% on the tick grid): lot size, freeze
  limit, price band (from a quote), 5-level book walk, 60 s follow-up; per
  trade: margin via Kite basket margins against the deployment's capital and
  the account's free funds, short placed before its hedge, leg-by-leg drift
  (0.4 s apart), order rate (10/s, 200/min across deployments). Verdict chip
  per fill: Would fill / ₹X worse / partly / wouldn't fill in 60 s / would be
  rejected / couldn't check. Rejections alert once per deployment, rule and
  day. **Ready for live** card (Standard thresholds, the owner's choice): ≥20
  sessions (intraday) or ≥3 complete cycles (positional), no rejects in the
  last 10 sessions, average shortfall ≤1%, no open repair case; shows P&L made
  on trades Kite would refuse or wouldn't fill. Past fills can be estimated
  from stored books ("(est.)", excluded from the record). Shadow's Kite calls
  wait for Kite to be idle 1 s. Never delays or changes a paper fill.
- **End-of-day check** (`app/repair/daycheck.py`, own process after the 15:45
  export): rules (intraday flat after its cut-off; nothing past expiry), then
  the strategy replayed from its state at the open on Kite 1-minute candles,
  trades matched (same action, contract, qty within 3 min; prices not
  compared). Results: as expected / rule broken / traded differently / to
  review / not replayed.
- **Repair** (Repair page + case page; CLI `custom_scripts/repair_stall.py` as
  fallback): from the first divergent minute, replay on 1-minute candles, each
  minute as 4 ticks (open → one extreme → other → close; CE high and PE low
  when the index is high); an ambiguous minute is played both ways and the
  owner picks; plan is a rolled-back transaction with proof charts and each
  step's reason; push re-runs and commits only if identical; undo restores the
  app's own fills. Only the latest traded day can be repaired.
- **Self-reporting strategies** and the **fault matrix** (12 failure types).
- **Crash-proof**: killed anywhere and restarted from the DB, same trades.
- **Tick stall self-heal**, stale-price guard, tick filter.

## Data the app collects

- 1-minute candles for every option/future held (live ticks + 15:45 Kite
  backfill with UI progress + import script). Indices/VIX fetched on demand.
- Open-leg marks every 1 min (intraday) / 5 min (positional); equity
  snapshots every 5 min.
- Market context on every fill (gap, range, move, VIX, DTE, strike distance)
  and signal context (candle, ATR). Logging only, proven not to change trades.
- Decision log rows when outcomes change (not every tick).

## Tools

SIP, step-up SIP, lumpsum + step-up, CAGR, brokerage calculator (generic,
`static/js/calculators.js`, usable elsewhere).
