# tg_int_st_pp

Standalone folder — does **not** import anything from the rest of the
`port` repo. Self-contained fetch script, own config template, own
requirements.

## What's here

Fetches 5-minute OHLCV candles for the **NIFTY 50 index** (the index
itself, not the 50 constituent stocks) via Kite Connect, for a recent
lookback window.

## Why chunking matters here

Kite's historical-data API caps how much history you can pull per request,
and the cap depends on the candle interval. For `5minute` candles this
script uses a **200-day-per-request** cap (`MAX_DAYS_PER_REQUEST` in
`fetch_nifty_5min.py`) — every fetch is chunked into ≤200-day windows,
each with its own retry/backoff, even when the requested window is small
enough to fit in one chunk. The default lookback is 10 days, so in
practice one chunk is fetched — but the chunking logic is there so this
keeps working correctly if the lookback is later widened past 200 days.

## Setup

```bash
cd tg_int_st_pp
pip install -r requirements.txt
```

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

The `access_token` expires daily — log in to Kite and refresh it each
session. If no `config.json` is present, the script prompts interactively.

## Usage

```bash
# Fetch last 10 days (default) of 5-min NIFTY 50 candles
python fetch_nifty_5min.py

# Explicit config path
python fetch_nifty_5min.py -c /path/to/config.json

# Custom lookback window
python fetch_nifty_5min.py --days 15

# Resolve the NIFTY 50 index instrument only — no fetch
python fetch_nifty_5min.py --dry-run
```

## Instrument resolution

The NIFTY 50 index instrument is resolved dynamically from
`kite.instruments("NSE")`:

1. Exact match: `segment == "INDICES"` and `tradingsymbol == "NIFTY 50"`
2. Fallback: `segment == "INDICES"` and `"NIFTY 50"` in the instrument name
3. Last resort: a hardcoded `instrument_token` (256265), used only if
   both dynamic checks fail — and always flagged loudly in the console
   output and in `_fetch_log.json` when it happens, never applied silently.

Every run's resolution outcome (which path matched, the token used) is
recorded in `data/_fetch_log.json`.

## Output

```
data/
├── NIFTY50_5minute.json   # array of 5-min candles
└── _fetch_log.json        # instrument resolution + per-chunk pagination log
```

**`NIFTY50_5minute.json`** — one entry per candle:

```json
[
  {"date": "2026-08-01 09:15:00", "open": 24812.3, "high": 24830.1, "low": 24805.0, "close": 24821.5, "volume": 0},
  ...
]
```

(Index candles typically report `volume: 0` — there's no traded volume
on the index itself, only on its derivatives/constituents.)

**`_fetch_log.json`** — resolution outcome, requested date range, and a
per-chunk pagination record (each chunk's date range, candle count, and
success/error status), so you can see exactly how the fetch was split up
and verify nothing silently dropped a chunk.

## Data retention

The fetched data files (`data/NIFTY50_5minute.json`, `data/_fetch_log.json`)
are committed to the repo, not gitignored — this session runs in an
ephemeral container, and the data needs to survive across sessions so
strategy code can be built on top of it later without refetching. Only
`config.json` (credentials) is gitignored.

## Strategy — Pivot Points + SuperTrend(7,3)

`strategy_pivot_supertrend.py` runs an intraday strategy over the fetched
5-min candles:

- **Long entry** — 5-min close above any of R1/R2/R3, AND SuperTrend(7,3) green.
- **Short entry** — 5-min close below any of S1/S2/S3, AND SuperTrend(7,3) red.
- **Exit** — SuperTrend flips color → exit at the *next* candle's open, OR
  force-exit at the end-of-day cutoff — whichever comes first.
- Only 1 open position at a time. Multiple trades/day are allowed — a new
  entry (either direction) can fire immediately after an exit, same candle
  or later, exactly as in your own example (long exits on ST flip, a few
  candles later price closes below S1 with ST red → short entry).

### Assumptions made (spec had a few gaps — flagged here, not guessed silently)

The clarifying question about these got dismissed rather than answered, so
these are the **defaults actually implemented**. All are easy to change —
see the constants at the top of `strategy_pivot_supertrend.py`.

| Gap | Default used | Where to change it |
|---|---|---|
| Pivot formula | **Classic/Standard** (`P=(H+L+C)/3`, `R1=2P-L`, `S1=2P-H`, etc.) | `compute_classic_pivots()` |
| Previous-day OHLC source | Derived from that day's 5-min candles (max high, min low, **last 5-min close as EOD proxy**) — this dataset has no separate daily bar | `daily_ohlc()` |
| First day in dataset | **Excluded from trading** — no prior-day OHLC exists inside the window to compute its pivots | `build_pivots_by_day()` |
| SuperTrend ATR smoothing | **Wilder (RMA)**, the standard on every charting platform | `_atr_wilder()` |
| SuperTrend reset | **Continuous across days** (not reset each morning) — matches TradingView/Kite chart default | `compute_supertrend()` |
| Force-exit time | **3:00 PM (15:00)** | `DEFAULT_FORCE_EXIT_TIME`, or `--force-exit HH:MM` |
| Force-exit price | **Open of the 15:00 candle** — same "exit at candle open" convention as the ST-flip exit | `run_strategy()` step 2 |
| Entry price | **Close of the signal candle** — the spec's "next candle" language only applies to exits | `run_strategy()` step 4 |
| Re-entry cooldown | **None** — a new entry can fire the same candle as an exit | `run_strategy()` |

If any of these don't match what you intended, say so and they're a
one-line change each — nothing here is architecturally locked in.

### Usage

```bash
python strategy_pivot_supertrend.py                   # reads data/NIFTY50_5minute.json
python strategy_pivot_supertrend.py --input other.json
python strategy_pivot_supertrend.py --force-exit 15:15
```

### Output

```
data/
├── trades_pivot_supertrend.json    # every trade: entry/exit time+price, side, points, exit reason, pivots used
└── summary_pivot_supertrend.json   # day-by-day breakdown + aggregate stats
```

Console output prints a day-by-day table (trades, total points, avg points,
win/loss count) plus aggregate stats: total trades, total points, average
points/trade, win rate, average win/loss points, long vs short split, and
an exit-reason breakdown (`st_flip` vs `force_exit`).

### Validated against synthetic data (not yet run on real market data)

Since no live NIFTY 50 5-min data exists yet (see "Data retention" below —
no Kite credentials in this environment), the strategy engine was verified
end-to-end against a hand-constructed 2-day synthetic candle series
designed to exercise every rule:

- Pivot formula checked against a hand-computed example
- Day 1 correctly excluded (no prior-day pivots)
- A rally through R1 correctly triggered a long entry at the signal
  candle's close, with SuperTrend green
- The subsequent crash correctly flipped SuperTrend and exited the long
  at the *next* candle's open (`exit_reason: "st_flip"`)
- A short entered a few candles later once price closed below S1 with
  SuperTrend red — same-day re-entry after an exit, mirroring your own
  example
- The short ran until the 15:00 cutoff and was force-exited at that
  candle's open (`exit_reason: "force_exit"`)
- No overlapping positions at any point; points math (sign per side)
  verified on every trade

This confirms the mechanics are correct — the actual day-wise trades and
average points **from real NIFTY 50 data** still require a fetch to run
first (see Setup above).

## Relationship to the rest of the repo

This folder is intentionally isolated from the main `port` repo's Nifty
50 constituent-stock pipeline (`fetch_ohlcv.py`, `backtest.py`,
`analytics.py`, `generic/`). It fetches a different instrument (the index,
not individual stocks), a different interval (5-minute intraday, not
daily), and shares no code or data with those pipelines.
