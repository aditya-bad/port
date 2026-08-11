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
- **Both entries and exits execute at the *next* candle's open** — never
  on the signal candle's own close. You can't place a real-time order on
  a close price the instant it prints, so a signal detected on candle *i*
  (an entry condition met, or SuperTrend flipping color) always executes
  at candle *i+1*'s open.
- Force-exit at 3:00 PM, at that candle's open — same convention.
- Only 1 open position at a time. Multiple trades/day are allowed — a new
  entry (either direction) can fire immediately after an exit, same candle
  or later, exactly as in your own example (long exits on ST flip, a few
  candles later price closes below S1 with ST red → short entry).

### Configurable settings (confirmed to move the numbers — CLI flags, not baked in)

| Flag | Options | Default | Why it's a param |
|---|---|---|---|
| `--pivot-type` | `classic`, `fibonacci`, `camarilla`, `woodie` | `classic` | "R1/R2/R3" doesn't name one formula — these 4 produce materially different price levels from the same day's H/L/C |
| `--atr-smoothing` | `wilder`, `sma`, `ema` | `wilder` | Shifts SuperTrend's ATR bands and therefore exactly when it flips color — confirmed to matter |
| `--force-exit` | `HH:MM` | `15:00` | In case the cutoff time itself needs to change (the "exit at candle open" convention is fixed either way) |

### Confirmed (not configurable — checked, not guessed)

- SuperTrend(7,3) runs **continuously across days**, not reset each morning
  (matches TradingView/Kite chart default).
- Force-exit price = **open of the cutoff candle**, same convention as the
  ST-flip exit.
- Entry price = **next candle's open**, matching the exit convention —
  changed from an earlier draft that used the signal candle's own close,
  which isn't realistically tradeable.
- Previous-day OHLC (for pivots) is derived from that day's 5-min candles
  (max high, min low, last 5-min close as EOD proxy) — this dataset has no
  separate daily bar. The **first day in any dataset is excluded from
  trading** — no prior day exists inside the window to compute its pivots.
- Re-entry cooldown: **none** — a new entry can fire the same candle as
  an exit.

### Usage

```bash
python strategy_pivot_supertrend.py                          # classic pivots, wilder ATR, 15:00 cutoff
python strategy_pivot_supertrend.py --input other.json
python strategy_pivot_supertrend.py --pivot-type fibonacci
python strategy_pivot_supertrend.py --atr-smoothing ema
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

- All 4 pivot formulas checked: monotonic (R1<R2<R3, S1>S2>S3) and
  genuinely distinct from each other on the same H/L/C
- All 3 ATR smoothing methods checked: correctly seeded at the warmup
  boundary, correctly `None` before it, and diverge from each other
  downstream (confirming the choice actually changes results)
- Day 1 correctly excluded (no prior-day pivots)
- A rally through R1 correctly triggered a long entry — verified the
  entry price equals the **next** candle's open, not the signal candle's
  own close (the signal candle's close crossing R1 was confirmed
  separately, one candle earlier than the actual fill)
- The subsequent crash correctly flipped SuperTrend and exited the long
  at the *next* candle's open (`exit_reason: "st_flip"`)
- A short entered a few candles later once price closed below S1 with
  SuperTrend red, filled at the following candle's open — same-day
  re-entry after an exit, mirroring your own example
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
