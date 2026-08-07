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

## Data files

| File | Purpose |
|------|---------|
| `data/union_universe_2010_2026.json` | 95 symbols — union of every Nifty 50 constituent in the backtest window |
| `data/pit_universe_intervals.json` | Point-in-time constituent intervals — used in Stage 2, not Stage 1 |

## Open decision (Stage 2)

HDFC Ltd merged into HDFC Bank on 2023-07-13 (not a normal index replacement).
Currently treated as a plain exclusion. Whether to model the actual share-swap
conversion is undecided.
