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

## Relationship to the rest of the repo

This folder is intentionally isolated from the main `port` repo's Nifty
50 constituent-stock pipeline (`fetch_ohlcv.py`, `backtest.py`,
`analytics.py`, `generic/`). It fetches a different instrument (the index,
not individual stocks), a different interval (5-minute intraday, not
daily), and shares no code or data with those pipelines.
