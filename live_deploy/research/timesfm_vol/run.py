"""The experiment: every baseline's forecasts, TimesFM's (from
forecast_timesfm.py's CSV, when there is one), scored on the same days,
and a report.

    python run.py vol_history.csv.gz                                   # the baselines alone
    python run.py vol_history.csv.gz --timesfm forecasts_timesfm.csv   # with TimesFM

Writes, to --out (default results/): forecasts.csv (every forecast and
outcome), scores.csv (one row per index, horizon and model) and
report.md, which it also prints.

The rule, set before any result was seen: TimesFM is worth adding only
if, on at least one horizon, its QLIKE beats both HAR's and VIX's with
Diebold-Mariano p < 0.05, its 10-90% band holds 70-90% of outcomes, and
on the strangle check it tells cycles apart better than the best
baseline does. On most of the indices, not one.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

import baselines
import score
import vol

HORIZONS = (5, 20)
REFS = ("har", "vix")


def baseline_forecasts(daily: dict, intraday: dict, years: float) -> pd.DataFrame:
    rows = []
    vixd = daily.get(vol.VIX)
    for sym in vol.INDICES:
        if sym not in daily:
            continue
        d = daily[sym]
        var = vol.daily_variance(d, intraday.get(sym))
        pos = d.index.get_indexer(var.index)
        close = pd.Series(d["close"].to_numpy()[np.r_[pos[0] - 1, pos]])
        vix_close = vixd["close"].reindex(var.index).ffill().bfill() if vixd is not None else None
        days = vol.test_days(var, years, max(HORIZONS))
        for h in HORIZONS:
            y = vol.targets(var, h).to_numpy()
            print(f"{sym} h={h}: {len(days)} days", file=sys.stderr, flush=True)
            for model, f in baselines.all_baselines(var, close, vix_close, days, h).items():
                for i, fv in zip(days, f):
                    rows.append({"symbol": sym, "date": var.index[i].date().isoformat(), "model": model, "h": h,
                                 "var": fv, "lo": np.nan, "hi": np.nan, "y": y[i],
                                 "vix_var": float(vol.var_of_vix(vix_close.iloc[i])) if vix_close is not None else np.nan})
    return pd.DataFrame(rows)


def attach_outcomes(tfm: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    """TimesFM's rows with the outcome and VIX of the same day."""
    keys = base[["symbol", "date", "h", "y", "vix_var"]].drop_duplicates(["symbol", "date", "h"])
    return tfm.merge(keys, on=["symbol", "date", "h"], how="inner")


def scores(fc: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (sym, h), g in fc.groupby(["symbol", "h"]):
        wide = g.pivot_table(index="date", columns="model", values="var")
        wide = wide.dropna()   # the same days for every model
        if wide.empty:
            continue
        y = g.drop_duplicates("date").set_index("date")["y"].reindex(wide.index)
        vix_var = g.drop_duplicates("date").set_index("date")["vix_var"].reindex(wide.index)
        loss = {m: score.qlike(y, wide[m]) for m in wide.columns}
        calm = vix_var <= vix_var.median() if vix_var.notna().all() else None
        bands = g.dropna(subset=["lo", "hi"]).set_index(["model", "date"])
        vrp = (vix_var - y) if h == 20 and vix_var.notna().all() else None
        for m in wide.columns:
            row = {"symbol": sym, "h": h, "model": m, "days": len(wide),
                   "qlike": float(loss[m].mean()),
                   "rmse_vol": score.rmse(vol.vol_of(y), vol.vol_of(wide[m]))}
            for ref in REFS:
                if ref in wide.columns and ref != m:
                    stat, p = score.dm_test(loss[m], loss[ref], h)
                    row[f"dm_vs_{ref}"], row[f"p_vs_{ref}"] = stat, p
            if calm is not None:
                row["qlike_calm"], row["qlike_volatile"] = float(loss[m][calm].mean()), float(loss[m][~calm].mean())
            if m in bands.index.get_level_values(0):
                b = bands.loc[m].reindex(wide.index)
                row["coverage"] = score.coverage(y, b["lo"], b["hi"])
            if vrp is not None and m != "vix":
                signal = vix_var - wide[m]
                row["strangle_corr"] = float(np.corrcoef(signal, vrp)[0, 1])
                row["strangle_hit"] = float(np.mean(np.sign(signal) == np.sign(vrp)))
            out.append(row)
    return pd.DataFrame(out)


def verdict(sc: pd.DataFrame) -> list[str]:
    """The rule above, applied to each TimesFM model."""
    lines = []
    tfm_models = sorted(m for m in sc["model"].unique() if m.startswith("tfm"))
    if not tfm_models:
        return ["No TimesFM forecasts yet: run forecast_timesfm.py, then this with --timesfm."]
    syms = sorted(sc["symbol"].unique())
    for m in tfm_models:
        passed = []
        for sym in syms:
            s = sc[(sc["symbol"] == sym)]
            beats = False
            for h in HORIZONS:
                r = s[(s["h"] == h) & (s["model"] == m)]
                if r.empty:
                    continue
                r = r.iloc[0]
                ok = all(r.get(f"dm_vs_{ref}", np.nan) < 0 and r.get(f"p_vs_{ref}", 1) < 0.05 for ref in REFS)
                beats = beats or bool(ok)
            band = s[(s["model"] == m)]["coverage"].dropna()
            band_ok = len(band) > 0 and bool(((band >= 0.7) & (band <= 0.9)).all())
            st = s[(s["h"] == 20)]
            strangle_ok = False
            if "strangle_corr" in st and st["strangle_corr"].notna().any():
                mine = st[st["model"] == m]["strangle_corr"]
                best_base = st[~st["model"].str.startswith("tfm")]["strangle_corr"].max()
                strangle_ok = len(mine) > 0 and float(mine.iloc[0]) > best_base
            passed.append(beats and band_ok and strangle_ok)
            lines.append(f"- {m} on {sym}: beats HAR and VIX {'yes' if beats else 'no'} · band "
                         f"{'right' if band_ok else 'off'} · strangle check {'better' if strangle_ok else 'not better'}")
        n = sum(passed)
        lines.append(f"**{m}: {'worth adding' if n * 2 > len(syms) else 'not worth adding'}** "
                     f"(passes on {n} of {len(syms)} indices)")
    return lines


def report(sc: pd.DataFrame) -> str:
    out = ["# TimesFM volatility experiment", "",
           "Lower QLIKE and RMSE are better. p: Diebold-Mariano against HAR / VIX (under 0.05 is a real "
           "difference; the sign of the statistic says which way). Coverage: share of outcomes inside the "
           "10-90% band (80% is right). Strangle: correlation between (VIX minus the forecast) and how much "
           "VIX overpaid over the next 20 days.", ""]
    cols = ["model", "days", "qlike", "rmse_vol", "p_vs_har", "p_vs_vix", "qlike_calm", "qlike_volatile", "coverage",
            "strangle_corr", "strangle_hit"]
    for (sym, h), g in sc.groupby(["symbol", "h"]):
        g = g.sort_values("qlike")
        out += [f"## {sym}, next {h} trading days", "", "| " + " | ".join(cols) + " |",
                "|" + "---|" * len(cols)]
        for _, r in g.iterrows():
            cells = []
            for c in cols:
                v = r.get(c, np.nan)
                cells.append(str(v) if isinstance(v, str) else ("" if pd.isna(v) else f"{v:.3f}" if c != "days" else str(int(v))))
            out.append("| " + " | ".join(cells) + " |")
        out.append("")
    out += ["## Verdict", ""] + verdict(sc)
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data", help="the export: vol_history.csv.gz")
    ap.add_argument("--timesfm", help="forecast_timesfm.py's CSV")
    ap.add_argument("--test-years", type=float, default=2.0)
    ap.add_argument("--out", default="results")
    args = ap.parse_args(argv)

    daily, intraday = vol.load(args.data)
    base = baseline_forecasts(daily, intraday, args.test_years)
    fc = base
    if args.timesfm:
        fc = pd.concat([base, attach_outcomes(pd.read_csv(args.timesfm), base)], ignore_index=True)
    sc = scores(fc)
    os.makedirs(args.out, exist_ok=True)
    fc.to_csv(os.path.join(args.out, "forecasts.csv"), index=False)
    sc.to_csv(os.path.join(args.out, "scores.csv"), index=False)
    text = report(sc)
    with open(os.path.join(args.out, "report.md"), "w") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
