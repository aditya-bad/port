# Generic Multi-Universe Pipeline

Run the same backtest strategy (buy below DMA, average down, exit at target)
against **any** user-defined stock universe — not just Nifty 50.

This is a **separate, parallel pipeline** from the Nifty-specific one. Running
a generic-universe backtest doesn't touch or depend on any Nifty data under
`data/`, and vice versa. The Nifty pipeline (`fetch_ohlcv.py`, `backtest.py`,
`analytics.py`) is not modified.

## Quick Start

```bash
# 1. Create a universe
mkdir -p generic/universes/my_basket

# 2. Drop in a symbol list (one symbol per line)
cat > generic/universes/my_basket/universe_input.txt << 'EOF'
RELIANCE
TCS
INFY
HDFCBANK
ICICIBANK
EOF

# 3. Fetch OHLCV data
python -m generic.fetch_ohlcv_generic \
    --universe universes/my_basket \
    --start-date 2018-01-01 \
    -c config.json

# 4. Create a backtest config
mkdir -p generic/universes/my_basket/configs
cp backtest_config.example.json generic/universes/my_basket/configs/backtest_config.json
# Edit dates and parameters as needed

# 5. Run the backtest
python -m generic.backtest_generic --universe universes/my_basket

# 6. Launch the analytics UI
python -m generic.analytics_generic --universe universes/my_basket --port 5001
```

All commands are run from the project root (`port/`).

## Adding a New Universe

1. **Create the folder:**
   ```bash
   mkdir -p generic/universes/{your_name}
   ```

2. **Add a symbol list** — one of three formats, auto-detected by extension:

   | Format | File | Shape |
   |--------|------|-------|
   | Plain text | `universe_input.txt` | One NSE symbol per line. `#` lines and blanks are ignored. |
   | CSV | `universe_input.csv` | Single column, or a column named `symbol`. |
   | JSON | `universe_input.json` | Array of strings: `["RELIANCE", "TCS", ...]` |

3. **Create the config directory:**
   ```bash
   mkdir -p generic/universes/{your_name}/configs
   cp backtest_config.example.json generic/universes/{your_name}/configs/backtest_config.json
   ```
   The config schema is **identical** to the Nifty pipeline's
   `backtest_config.example.json` — same capital, entry, averaging, exit, and
   cost parameters. Adjust `backtest_start`, `backtest_end`, and `in_sample_end`
   for your universe's date range.

Multiple universes coexist side by side:
```
generic/universes/
├── midcapshop/
│   ├── universe_input.txt
│   ├── configs/backtest_config.json
│   └── data/ohlcv/...
├── my_custom_basket/
│   ├── universe_input.csv
│   ├── configs/backtest_config.json
│   └── data/ohlcv/...
└── sectoral_bank/
    └── ...
```

## Universe Modes

### Mode 1 — Static List (default)

When you supply a `.txt`, `.csv`, or `.json` array of symbol strings, every
symbol is treated as eligible for **the entire backtest window**. There is no
concept of membership changes — if you list 15 symbols, all 15 are in the
universe on every trading day from `backtest_start` to `backtest_end`.

### Mode 2 — Point-in-Time Intervals (advanced)

If your `universe_input.json` is a list of objects with `{symbol, start_date,
end_date}` fields (half-open `[start, end)` intervals, `end_date: null` = still
active), it is detected automatically and used for point-in-time membership
filtering — the same interval logic already proven in the Nifty pipeline's
`PITUniverse` class.

```json
[
  {"symbol": "RELIANCE", "start_date": "2015-01-01", "end_date": null},
  {"symbol": "YESBANK",  "start_date": "2015-01-01", "end_date": "2020-09-25"},
  ...
]
```

### ⚠ Survivorship Bias Warning

**Mode 1 does NOT protect against survivorship bias.** If you hand-pick
symbols based on what you know performed well, the backtest results are
meaningless as a predictive signal. Mode 1 is correct for a hand-crafted
basket where there is genuinely no concept of "index membership churn" —
e.g. "I want to test this strategy on these specific 10 stocks." It is
**your responsibility** to supply Mode 2 data if historical membership
changes matter for your use case.

The generic pipeline has no way to reconstruct real historical index
membership for an arbitrary universe — there is no equivalent of the Nifty 50
Wikipedia replacement table for every possible basket. Don't treat Mode 1
results as survivorship-bias-free unless you know for certain the universe
never changed.

## Scripts

### `fetch_ohlcv_generic.py`

Fetches daily OHLCV data from Kite Connect for the symbols in a universe.

```bash
python -m generic.fetch_ohlcv_generic \
    --universe universes/{name} \
    --start-date YYYY-MM-DD \    # default: 2015-01-01
    -c config.json \              # Kite credentials
    --dry-run                     # resolve only, no fetch
    --resume                      # skip symbols with existing files
```

**Reuses from `fetch_ohlcv.py`:** symbol resolution chain (manual override →
exact → underscore-split → prefix → fuzzy), `SYMBOL_OVERRIDES` mechanism,
chunked fetching with retry/backoff, rate limiting.

**New:** `--start-date` flag (no hardcoded 2009-11-01 — set based on your
own backtest window + DMA warmup), output to `universes/{name}/data/ohlcv/`.

**Output:**
- `data/ohlcv/{SYMBOL}.json` — one file per symbol
- `data/ohlcv/_fetch_log.json` — resolution + fetch manifest

### `backtest_generic.py`

Runs the backtest engine against a universe's data.

```bash
python -m generic.backtest_generic \
    --universe universes/{name} \
    -c path/to/config.json \      # default: universes/{name}/configs/backtest_config.json
    --validate                    # check config + data only
```

**Reuses from `backtest.py`:** the entire `BacktestEngine` — entry, averaging,
exit, position sizing, cost model, corporate actions, daily snapshots, summary
writing. Subclasses only to redirect data paths and support static universes.

**Output:** `universes/{name}/data/runs/{run_name}/` with `trade_log.jsonl`,
`daily_portfolio.jsonl`, and `summary.json`.

### `analytics_generic.py`

Launches the analytics web UI for a universe's runs.

```bash
python -m generic.analytics_generic \
    --universe universes/{name} \
    --port 5001                   # default: 5001 (5000 = Nifty pipeline)
```

**Reuses from `analytics.py`:** XIRR computation (Newton's method), trade
duration calculation (quantity-weighted per-lot), sweep with IN-SAMPLE ONLY
guardrail.

**Reuses `static/index.html`** as-is — the same frontend serves both the
Nifty and generic pipelines (same API contract, different data directory).

## Folder Layout

```
generic/
├── README.md                         # this file
├── __init__.py
├── fetch_ohlcv_generic.py            # OHLCV fetcher
├── backtest_generic.py               # backtest runner
├── analytics_generic.py              # analytics UI
└── universes/
    └── {universe_name}/
        ├── universe_input.{txt|csv|json}   # ← what you provide
        ├── configs/
        │   └── backtest_config.json        # ← same schema as Nifty's
        └── data/
            ├── ohlcv/
            │   ├── {SYMBOL}.json
            │   └── _fetch_log.json
            └── runs/
                └── {run_name}/
                    ├── trade_log.jsonl
                    ├── daily_portfolio.jsonl
                    └── summary.json
```

## Relationship to the Nifty Pipeline

| | Nifty Pipeline | Generic Pipeline |
|---|---|---|
| Universe | 95 Nifty 50 constituents (PIT intervals from Wikipedia) | Any user-defined basket |
| Data dir | `data/ohlcv/`, `data/runs/` | `generic/universes/{name}/data/` |
| Fetch | `python fetch_ohlcv.py` | `python -m generic.fetch_ohlcv_generic --universe ...` |
| Backtest | `python backtest.py` | `python -m generic.backtest_generic --universe ...` |
| Analytics | `python analytics.py` (port 5000) | `python -m generic.analytics_generic --universe ...` (port 5001) |
| Config schema | `backtest_config.example.json` | Same schema, different file location |
| Strategy logic | `BacktestEngine` in `backtest.py` | Imported from `backtest.py` (subclass) |

The two pipelines share no data. Running one never affects the other.
