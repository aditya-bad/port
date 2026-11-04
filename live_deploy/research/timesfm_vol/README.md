# TimesFM volatility experiment

Can Google's TimesFM forecast how much NIFTY, NIFTY BANK and SENSEX will move
over the next 5 and 20 trading days better than the simple methods do, and well
enough to be worth adding to Linck? This folder answers it. It isn't part of the
app: nothing here runs on the server unless you run it, and the app's image
doesn't include it.

## What's measured

Each trading day's variance: the overnight gap squared, plus the day's own from
its 5-minute candles (or, on a day with too few, from its high and low). At each
of the last two years' trading days, every model forecasts the mean daily
variance of the next 5 and of the next 20 trading days, from the days up to then
only. Scored against what then happened:

| | |
|---|---|
| QLIKE | the usual loss for volatility forecasts; missing a big move costs the most |
| RMSE | the error in annualized volatility, in VIX points |
| p vs HAR / VIX | Diebold-Mariano: under 0.05, the difference is real |
| coverage | how often the outcome fell inside TimesFM's 10-90% band (80% is right) |
| calm / volatile | QLIKE on days with India VIX below / above its median |
| strangle | how well (VIX minus the forecast) tells which 20-day stretches VIX overpaid for |

Against: the last 20 days' mean, EWMA (lambda 0.94), GARCH(1,1), HAR and India
VIX itself (for NIFTY BANK and SENSEX, India VIX stands in). TimesFM 3.0 (alone,
and with VIX as an input) and TimesFM 2.5.

The rule, set before any result: worth adding only if, on most indices, TimesFM
beats both HAR and VIX on QLIKE with p < 0.05 on at least one horizon, its band
holds 70-90% of outcomes, and it does better than the best baseline on the
strangle check.

## 1. The data, from the server

While the app is logged in to Kite:

    docker exec live-deploy python3 custom_scripts/export_vol_history.py > vol_history.csv.gz

About 90 Kite requests, under a minute: daily and 5-minute candles for the three
indices and daily India VIX, from 2019. It only reads Kite. (`--from` and `--to`
change the range; `--no-intraday` takes daily candles only.)

## 2. The baselines

    python -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python run.py vol_history.csv.gz

Writes `results/report.md` (and prints it), `results/scores.csv` and
`results/forecasts.csv`. A minute or two; GARCH is the slow one.

## 3. TimesFM, where Hugging Face can be reached

    .venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu   # no GPU
    .venv/bin/pip install -r requirements-timesfm.txt
    .venv/bin/python forecast_timesfm.py vol_history.csv.gz --out forecasts_timesfm.csv
    .venv/bin/python run.py vol_history.csv.gz --timesfm forecasts_timesfm.csv

The weights (google/timesfm-3.0-pytorch, google/timesfm-2.5-200m-pytorch)
download on first use. Weights already downloaded: `--weights3 /path/to/folder`,
`--weights25 /path/to/folder`. One model only: `--models tfm3`. On a CPU, a few
thousand forecasts take minutes.

TimesFM 3.0's weights are licensed for non-commercial, non-production use; 2.5's
are Apache-2.0.

## Tests

    .venv/bin/python -m pytest

On a synthetic market whose volatility clusters like a real one: each day's
variance, no model looking past the day it forecasts from, the scores, both
TimesFM versions' outputs read right (with a stand-in model of the same
interface), and the whole run end to end. The export script's own tests run in
the app's gate (`tests/unit/test_export_vol_history.py`).
