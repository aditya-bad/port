# NiftyShop

Backtesting infrastructure for Nifty 50 constituent stocks.

## Stage 1 — Fetch OHLCV Data

Fetch daily OHLCV (open, high, low, close, volume) via Kite Connect for every
stock that was ever a Nifty 50 constituent during the backtest window
(2009-11-01 → today). This avoids survivorship bias by including stocks that
were later removed from the index.

### Setup

```bash
pip install -r requirements.txt
```

### Configuration

Copy the example config and fill in your Kite Connect credentials:

```bash
cp config.example.json config.json
```

```json
{
  "api_key": "your_kite_api_key",
  "api_secret": "your_kite_api_secret",
  "access_token": "your_daily_access_token"
}
```

The `access_token` expires daily — you'll need to log in to Kite and update it
each session. If no config file is present, the script prompts interactively.

### Usage

```bash
# Full fetch
python fetch_ohlcv.py

# Explicit config path
python fetch_ohlcv.py -c /path/to/config.json

# Resolve symbols only — no data fetch (useful to preview what resolves)
python fetch_ohlcv.py --dry-run

# Resume a partial run (skip symbols whose output file already exists)
python fetch_ohlcv.py --resume
```

### Output

- `data/ohlcv/{SYMBOL}.json` — one file per symbol, array of candles:
  ```json
  [{"date": "2009-11-02", "open": 1050.0, "high": 1065.5, "low": 1042.0, "close": 1058.3, "volume": 2340000}, ...]
  ```
- `data/ohlcv/_fetch_log.json` — manifest with per-symbol resolution status,
  fetch outcome, date ranges, and candle counts.

### Symbol resolution

The script pulls `kite.instruments("NSE")` and resolves each of the 95
universe symbols in order:

1. **Exact** tradingsymbol match
2. **Partial split** for underscore-separated symbols (e.g. `BHARTIARTL_INFRATEL` → tries `INFRATEL`)
3. **Prefix/substring** overlap on tradingsymbol
4. **Fuzzy** company-name match (SequenceMatcher ≥ 0.5)

Symbols that don't resolve to any current NSE instrument (delisted, merged,
defunct) are logged in `_fetch_log.json` and skipped gracefully.

## Stage 2/3 — Backtest Engine

Config-driven backtest: buy below DMA, average down, exit at target — across
point-in-time Nifty 50 constituents with survivorship-bias-free universe.

### Setup

```bash
cp backtest_config.example.json backtest_config.json
# Edit parameters as needed — every value is read from config, no hardcoded defaults
```

### Usage

```bash
python backtest.py                               # default config
python backtest.py -c my_config.json             # custom config
python backtest.py --validate                    # check config + data only
```

### Daily algorithm (exact order)

1. **Build universe** — PIT constituent filter for today, excluding `no_kite_data`
2. **Exits** — sell positions at/above `avg_cost × (1 + target%)`, tie-break by
   highest capital invested, max `max_sells_per_day`
3. **Entries** — stocks ≥ `min_pct_below_dma` below their DMA, top N candidates,
   buy up to `max_new_positions_per_day` NOT already held (throttle/cap rules apply)
4. **Averaging** — only if step 3 bought zero NEW stocks; average down on held
   stocks based on drop from LAST BUY price (not avg cost, intentional)
5. **Corporate actions** — HDFC merger force-exit or conversion
6. **Capital recalc** — every 365 days from inception, lot size adjusts to
   `(initial + cumulative realized P&L) / divisor`

### Output per run

```
data/runs/{run_name}/
├── trade_log.jsonl         # every buy, sell, skip, recalc
├── daily_portfolio.jsonl   # end-of-day snapshots
└── summary.json            # CAGR, max DD, win rate, IS/OOS split, BnH comparison
```

### Config reference

See `backtest_config.example.json` for the full schema. Key sections:
- `capital` — initial amount, lot divisor, max lots, throttle rules
- `entry` — DMA period, min % below, candidates per day, max new per day
- `averaging` — trigger %, max buys/day, optional per-stock lot cap + stop loss
- `exit` — target % above avg cost, max sells/day, tie-break rule
- `costs` — brokerage, STT, STCG/LTCG rates (default to 0 for baseline)

## Stage 4 — Analytics UI

Local web app for inspecting runs, visualizing results, triggering backtests,
and running parameter sweeps.

### Setup

```bash
pip install -r requirements.txt    # adds flask
```

### Usage

```bash
python analytics.py                # http://127.0.0.1:5000
python analytics.py --port 8080    # custom port
python analytics.py --debug        # Flask debug mode
```

### Views

| Tab | What it shows |
|-----|--------------|
| Overview | CAGR, max DD, win rate, realized P&L, IS/OOS split, BnH comparison |
| Equity Curve | Portfolio value over time, IS/OOS color-coded |
| Drawdown | Drawdown % from peak |
| Trade List | Paginated, filterable trade log (buy/sell/skip/recalc) |
| XIRR | Cash-flow-based IRR (Newton's method) — overall, IS, OOS |
| Durations | Trade duration histogram + scatter (quantity-weighted per-lot, not first-lot-to-exit) |
| Stats | Profit factor, avg win/loss, max consecutive losses, largest trades |
| Returns | Monthly heatmap + yearly bar chart |
| Exposure | Open lots over time + invested-vs-cash stacked area |

### Run trigger

The UI can spawn `backtest.py` as a subprocess — paste a config JSON and click
Run. Stage 1 (Kite fetch) is **not** wired into this trigger.

### Parameter sweep

Sweeps are **in-sample only** — `backtest_end` is forced to `in_sample_end`.
Out-of-sample data is structurally unavailable to the sweep. The UI warns when
the grid exceeds 25 combinations.

Sweep outputs go to `data/runs/{run_name}_sweep_{param}={val}_…/`.

### XIRR computation

XIRR uses Newton's method on actual cash-flow dates (buys = negative outflows,
sells = positive inflows, open positions = terminal mark-to-market). It does
**not** reuse the CAGR function — CAGR assumes a single lump-sum, XIRR handles
irregular cash flows.

## Data files

| File | Purpose |
|------|---------|
| `data/union_universe_2010_2026.json` | 95 symbols — union of every Nifty 50 constituent in the backtest window |
| `data/pit_universe_intervals.json` | Point-in-time constituent intervals `[start, end)` for universe filtering |

## Open items

1. **`max_lots_per_stock` / `stop_loss_pct_from_avg_cost`** — both `null` by
   default (uncapped averaging). Run baseline first, inspect trade log for
   concentration risk, then decide cap values as a config change.
2. **Stop-loss vs daily sell cap** — if stop-loss is set later, confirm whether
   it competes with `max_sells_per_day` or bypasses it.
3. **Costs at zero** — must be set to real STT/brokerage/tax rates before
   treating any run as final.
4. **HDFC merger** — `force_exit` implemented and is the default.
   `convert_to_hdfcbank` (share-swap) is flagged but not yet built.
