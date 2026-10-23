# 12 · Glossary

| Term | Meaning here |
|---|---|
| Strategy | A Python class with fixed rules (`app/strategies/`), registered by name |
| Deployment | One running copy of a strategy with its own name, capital, config and state |
| Intraday / positional | Mode label. Intraday squares off the same day; positional holds across days |
| Cycle | Intraday: one day. Positional: one entry through adjustments and rolls to flat. The unit of P&L everywhere |
| Episode | The code's name for a positional cycle (`queries._group_into_episodes`); also "a trade" in Reports/Compare |
| Leg | One option position (e.g. short NIFTY 25600 CE) |
| Checkpoint | Strangle profit target: cycle P&L ≥ 0.5% of capital → flatten and re-enter |
| Roll / adjustment | Close one leg and sell a replacement (strangle) or add a leg (DTT adjusted) |
| EOD check | Strangle's 15:13 balance check |
| EOD roll-out | Rolling the tested side out instead of converging into a straddle at 15:13 |
| Tested / untested side | The side the market is moving toward / away from |
| Convergence | Strangle strikes meet → a straddle; sticky for the cycle |
| Stale-price guard | No strangle decision while any leg hasn't traded since today's 09:15 |
| P&L basis | Gross / After charges / After charges + slippage |
| Slippage | Fill at LTP vs walking the 5-level order book |
| Shadow mode | Checking each paper fill as the LIMIT order Kite would have received |
| Ready for live | Shadow scorecard verdict under the Standard thresholds |
| Day check | End-of-day replay of each deployment on Kite 1-minute candles, after the 15:45 export |
| Repair / repair case | Replaying a day from its divergence, choosing paths, pushing with undo |
| Flagged / Repaired | Day check found a problem not yet repaired / a repair was applied |
| Ambiguous minute | A 1-minute candle whose order (up first / down first) changes what the strategy does |
| Decision log | `strategy_decisions`: each rule check, acted or not |
| Strategy issue / report | A strategy saying what went wrong (`strategy_issues`) |
| Ops issue | Health monitor problem (`ops_issues`), alerts every minute until fixed |
| Fault matrix | `tests/strategies/test_faults.py`, 9 strategies × 12 failures |
| Geek mode | Full-screen, read-only deep view of one deployment |
| Desk | Linck's home page with session lanes and "things need you" |
| Session lanes | One P&L line per deployment across the trading day |
| Strategy Lab | A tag for experiments, usually hidden |
| Classic | The old UI at `/`, kept as a fallback |
| Gate | `./run_tests.sh --e2e` → SAFE TO DEPLOY / DO NOT DEPLOY |
| P0 | Next step: latest-value tick delivery in today's app |
| Conflation | Keeping only the newest value per instrument for a slow consumer |
| Journal (runtime) | Every batch a strategy was given, numbered, written before sending (design, not built) |
| Journal (UI) | Trade notes and tags (`journal_entries`) |
| Epoch | Number bumped on every strategy (re)start; old-epoch intents are refused |
| Zygote | Preloaded Python parent that forks strategy processes |
| Cell | Self-contained copy of the runtime, market cache, OMS partition and ledger shard |
| OMS | Orders & risk service, the only writer of money (target) |
| Sim venue | Paper fills as just another broker (target) |
| DTT | The intraday straddle family's name (`intraday_dtt_*`) |
| BTST | Buy today, sell tomorrow (`calendar_btst`) |
| NFO / BFO | NSE / BSE F&O segments |
| Freeze limit | The largest quantity allowed in one order |
| MAE / MFE | Worst / best point a trade reached before it closed |
