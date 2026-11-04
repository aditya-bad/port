"""The experiment's results as one standalone HTML page: no network, no
library, light and dark, a phone's width. Reads run.py's results folder.

    python report_html.py results/ --out timesfm_report.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys

import numpy as np
import pandas as pd

import run
import vol

NAMES = {"vix": "India VIX", "har": "HAR", "garch": "GARCH(1,1)", "ewma": "EWMA", "last20": "Last 20 days",
         "tfm3": "TimesFM 3.0", "tfm3_vix": "TimesFM 3.0 + VIX", "tfm25": "TimesFM 2.5"}
BASELINES = ("vix", "har", "garch", "ewma", "last20")


def name(m: str) -> str:
    return NAMES.get(m, m)


def fmt(v, d=3) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{d}f}"


def series(fc: pd.DataFrame, sc: pd.DataFrame) -> dict:
    """{symbol: {h: {dates, actual, models: {m: {f, lo, hi}}}}}: each test
    day's outcome and every model's forecast, as annualized volatility."""
    out = {}
    for (sym, h), g in fc.groupby(["symbol", "h"]):
        dates = sorted(g["date"].unique())
        y = g.drop_duplicates("date").set_index("date")["y"].reindex(dates)
        entry = {"dates": dates, "actual": np.round(vol.vol_of(y.to_numpy()), 2).tolist(), "models": {}}
        for m, gm in g.groupby("model"):
            gm = gm.set_index("date").reindex(dates)
            d = {"f": np.round(vol.vol_of(gm["var"].to_numpy()), 2).tolist()}
            if gm["lo"].notna().any():
                d["lo"] = np.round(vol.vol_of(gm["lo"].to_numpy()), 2).tolist()
                d["hi"] = np.round(vol.vol_of(gm["hi"].to_numpy()), 2).tolist()
            entry["models"][m] = d
        out.setdefault(sym, {})[int(h)] = entry
    return out


def best_baseline(sc: pd.DataFrame, sym: str, h: int) -> str:
    s = sc[(sc["symbol"] == sym) & (sc["h"] == h) & sc["model"].isin(BASELINES) & (sc["model"] != "vix")]
    return s.sort_values("qlike").iloc[0]["model"]


def table(sc: pd.DataFrame, sym: str, h: int) -> str:
    s = sc[(sc["symbol"] == sym) & (sc["h"] == h)].sort_values("qlike")
    best = s["qlike"].min()
    rows = []
    for _, r in s.iterrows():
        tfm = str(r["model"]).startswith("tfm")
        cov = r.get("coverage", np.nan)
        cov_ok = "" if pd.isna(cov) else (" ok" if 0.7 <= cov <= 0.9 else " off")

        def p(col):
            v = r.get(col, np.nan)
            if pd.isna(v):
                return '<td class="num mute">—</td>'
            stat = r.get(col.replace("p_", "dm_"), 0)
            real = v < 0.05
            word = ("better" if stat < 0 else "worse") if real else "same"
            return f'<td class="num"><span class="pv {word}">{word}</span> <span class="mute">p {v:.2f}</span></td>'
        rows.append(
            f'<tr class="{"tfm" if tfm else ""}{" best" if r["qlike"] == best else ""}">'
            f'<th scope="row">{html.escape(name(r["model"]))}</th>'
            f'<td class="num">{fmt(r["qlike"])}</td><td class="num">{fmt(r["rmse_vol"], 2)}</td>'
            f'{p("p_vs_vix")}{p("p_vs_har")}'
            f'<td class="num{cov_ok}">{"—" if pd.isna(cov) else f"{cov * 100:.0f}%"}</td>'
            f'<td class="num">{fmt(r.get("qlike_calm"))} / {fmt(r.get("qlike_volatile"))}</td></tr>')
    return ('<div class="scroll"><table><thead><tr><th scope="col">Model</th><th scope="col" class="num">QLIKE</th>'
            '<th scope="col" class="num">Error (vol pts)</th><th scope="col" class="num">vs India VIX</th>'
            '<th scope="col" class="num">vs HAR</th><th scope="col" class="num">In its band</th>'
            '<th scope="col" class="num">Calm / volatile</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div>")


def strangle_rows(sc: pd.DataFrame) -> str:
    s = sc[(sc["h"] == 20) & sc["strangle_corr"].notna()] if "strangle_corr" in sc else sc.iloc[0:0]
    out = []
    for sym, g in s.groupby("symbol"):
        g = g.sort_values("strangle_corr", ascending=False)
        cells = "".join(f'<li><span>{html.escape(name(r["model"]))}</span><b class="num">{r["strangle_corr"]:+.2f}</b></li>'
                        for _, r in g.iterrows())
        out.append(f'<div class="card"><h4>{html.escape(sym)}</h4><ol class="rank">{cells}</ol></div>')
    return "".join(out)


def headline(sc: pd.DataFrame) -> tuple[str, str]:
    lines = run.verdict(sc)
    tfm = [l for l in lines if l.startswith("**")]
    if not tfm:
        return "TimesFM not run yet", "Only the baselines are scored."
    good = [l for l in tfm if "worth adding**" in l and "not worth" not in l]
    if good:
        return "TimesFM is worth adding", " ".join(l.strip("*") for l in good)
    return "TimesFM isn't worth adding", "By the rule set before the run, no TimesFM variant passed on most indices."


def page(results: str) -> str:
    fc = pd.read_csv(os.path.join(results, "forecasts.csv"))
    sc = pd.read_csv(os.path.join(results, "scores.csv"))
    data = series(fc, sc)
    syms = [s for s in vol.INDICES if s in data]
    title, sub = headline(sc)
    lines = run.verdict(sc)
    detail = "".join(f"<li>{html.escape(l.strip('- *'))}</li>" for l in lines)
    span = f'{fc["date"].min()} to {fc["date"].max()}'
    n = int(sc["days"].max())
    picks = {s: {h: best_baseline(sc, s, h) for h in data[s]} for s in syms}
    tfm_models = sorted(m for m in fc["model"].unique() if m.startswith("tfm"))
    main_tfm = "tfm3" if "tfm3" in tfm_models else (tfm_models[0] if tfm_models else None)
    sections = []
    for s in syms:
        tables = "".join(f'<h4>Next {h} trading days</h4>{table(sc, s, h)}' for h in sorted(data[s]))
        sections.append(f'''<section class="index" id="{html.escape(s.replace(" ", "-"))}">
<h3>{html.escape(s)}</h3>
<figure class="chart" data-sym="{html.escape(s)}"><div class="legend"></div><svg role="img" aria-label="{html.escape(s)}: forecasts against what followed"></svg><div class="tip" hidden></div></figure>
{tables}</section>''')
    payload = json.dumps({"series": data, "picks": picks, "tfm": main_tfm, "tfms": tfm_models, "names": NAMES})
    return TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__SUB__", html.escape(sub)) \
        .replace("__DETAIL__", detail).replace("__SPAN__", html.escape(span)).replace("__N__", str(n)) \
        .replace("__SECTIONS__", "".join(sections)).replace("__STRANGLE__", strangle_rows(sc)) \
        .replace("__DATA__", payload.replace("</", "<\\/"))


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TimesFM volatility test</title>
<style>
:root{--bg:#f4f5f2;--card:#fffffd;--ink:#1b1f1c;--mute:#5f675f;--line:#d9ddd5;--grid:#e8ebe4;
--actual:#1b1f1c;--vix:#b4650f;--base:#3f5f8f;--tfm:#0f7a62;--band:rgba(15,122,98,.16);
--good:#1f7a3a;--bad:#a5342b;--same:#6b726a;--hi:#eef4ec}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121513;--card:#1a1e1b;--ink:#e6eae3;--mute:#9aa397;
--line:#2c322d;--grid:#242a25;--actual:#e6eae3;--vix:#e0954a;--base:#8aa8d8;--tfm:#43c6a2;--band:rgba(67,198,162,.18);
--good:#5fc77a;--bad:#ef8077;--same:#9aa397;--hi:#1f2a22}}
:root[data-theme="dark"]{--bg:#121513;--card:#1a1e1b;--ink:#e6eae3;--mute:#9aa397;--line:#2c322d;--grid:#242a25;
--actual:#e6eae3;--vix:#e0954a;--base:#8aa8d8;--tfm:#43c6a2;--band:rgba(67,198,162,.18);--good:#5fc77a;--bad:#ef8077;
--same:#9aa397;--hi:#1f2a22}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1060px;margin:0 auto;padding:28px 16px 64px;display:grid;gap:28px}
h1,h2,h3,h4{text-wrap:balance;margin:0;line-height:1.2}
h1{font-size:clamp(26px,5vw,38px);letter-spacing:-.01em}
h2{font-size:22px}h3{font-size:20px}h4{font-size:14px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin:18px 0 8px}
p{margin:0;max-width:68ch}.mute{color:var(--mute)}
.num{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.eyebrow{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
.verdict{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:22px;display:grid;gap:12px}
.verdict ul{margin:0;padding-left:18px;color:var(--mute);font-size:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
section.index{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;display:grid;gap:6px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:620px}
th,td{padding:7px 8px;border-bottom:1px solid var(--grid);text-align:left}
thead th{font-size:12px;color:var(--mute);font-weight:600;border-bottom:1px solid var(--line)}
tr.tfm th{color:var(--tfm)}tr.best td:nth-child(2){font-weight:700}
.pv{font-size:12px;font-weight:600;padding:1px 6px;border-radius:999px;border:1px solid currentColor}
.pv.better{color:var(--good)}.pv.worse{color:var(--bad)}.pv.same{color:var(--same)}
td.ok{color:var(--good)}td.off{color:var(--bad)}
.chart{margin:6px 0 0;position:relative}
.chart svg{width:100%;height:300px;display:block;touch-action:pan-y}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--mute);margin-bottom:6px}
.legend i{display:inline-block;width:18px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.legend i.band{height:10px;background:var(--band)}
.tip{position:absolute;top:28px;pointer-events:none;background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:8px 10px;font-size:13px;box-shadow:0 4px 14px rgba(0,0,0,.12);min-width:170px}
.tip b{display:block;margin-bottom:4px}.tip div{display:flex;justify-content:space-between;gap:12px}
.controls{display:flex;gap:8px;flex-wrap:wrap}
button{font:inherit;font-size:14px;padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
button:focus-visible{outline:2px solid var(--tfm);outline-offset:2px}
.grid3{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
ol.rank{margin:8px 0 0;padding:0;list-style:none;display:grid;gap:4px;font-size:14px}
ol.rank li{display:flex;justify-content:space-between;border-bottom:1px solid var(--grid);padding:3px 0}
.notes{font-size:14px;color:var(--mute);display:grid;gap:8px}
</style></head><body><main>
<header style="display:grid;gap:10px">
<span class="eyebrow">Linck research · index volatility</span>
<h1>Can TimesFM forecast how much the market moves?</h1>
<p class="mute">Each trading day from __SPAN__ (__N__ days per index), every model forecast the volatility of the next 5 and 20 trading days from the days up to then only, and was scored against what then happened.</p>
</header>
<div class="verdict"><span class="eyebrow">Verdict</span><h2>__TITLE__</h2><p>__SUB__</p><ul>__DETAIL__</ul></div>
<div class="controls" role="group" aria-label="Horizon"><button type="button" data-h="20" aria-pressed="true">Next 20 days</button><button type="button" data-h="5" aria-pressed="false">Next 5 days</button></div>
__SECTIONS__
<section style="display:grid;gap:12px"><h3>The strangle check</h3>
<p class="mute">How well “India VIX minus the model's 20-day forecast” lined up with how much VIX then overpaid (VIX's variance minus what followed). 1 would be perfect; 0, no help at all.</p>
<div class="grid3">__STRANGLE__</div></section>
<section class="notes"><h3 style="color:var(--ink)">How to read it</h3>
<p><b>QLIKE</b>: the usual score for volatility forecasts, lower is better; missing a big move costs the most. <b>Error</b>: the average miss in annualized volatility, in VIX points.</p>
<p><b>vs India VIX / vs HAR</b>: a Diebold–Mariano test. “better” or “worse” only when p is under 0.05; otherwise the difference could be noise.</p>
<p><b>In its band</b>: how often what happened fell inside TimesFM's 10–90% range. 80% is right; 70–90% passes.</p>
<p><b>Volatility</b>: each day's overnight gap and its 5-minute moves, annualized like India VIX. India VIX is NIFTY's own index; for NIFTY BANK and SENSEX it stands in.</p>
</section>
</main>
<script>
const D=__DATA__;let H=20;
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
function draw(fig){
  const sym=fig.dataset.sym,s=D.series[sym]&&D.series[sym][H];if(!s)return;
  const svg=fig.querySelector('svg'),tip=fig.querySelector('.tip'),W=svg.clientWidth||800,Hh=300,L=40,R=10,T=10,B=26;
  const pick=D.picks[sym][H],lines=[['actual','What followed','--actual',s.actual,2.2],['vix',D.names.vix,'--vix',s.models.vix&&s.models.vix.f,1.4],
    [pick,(D.names[pick]||pick)+' (best baseline)','--base',s.models[pick]&&s.models[pick].f,1.4]];
  const tm=D.tfm&&s.models[D.tfm];if(tm)lines.push([D.tfm,D.names[D.tfm],'--tfm',tm.f,1.8]);
  const all=lines.flatMap(l=>l[3]||[]).concat(tm&&tm.hi?tm.hi:[]).filter(v=>v!=null&&isFinite(v));
  const lo=0,hi=Math.ceil(Math.max(...all)/5)*5,n=s.dates.length,x=i=>L+i*(W-L-R)/(n-1),y=v=>T+(1-(v-lo)/(hi-lo))*(Hh-T-B);
  const path=a=>a.map((v,i)=>v==null?'':(i&&a[i-1]!=null?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1)).join('');
  let g='';for(let v=0;v<=hi;v+=5){g+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="${css('--grid')}"/><text x="${L-6}" y="${y(v)+4}" text-anchor="end" font-size="11" fill="${css('--mute')}">${v}</text>`;}
  [0,Math.floor(n/2),n-1].forEach(i=>{g+=`<text x="${x(i)}" y="${Hh-6}" text-anchor="${i?i==n-1?'end':'middle':'start'}" font-size="11" fill="${css('--mute')}">${s.dates[i]}</text>`;});
  if(tm&&tm.lo){const up=tm.hi.map((v,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1)).join(''),dn=tm.lo.map((v,i)=>[i,v]).reverse().map(([i,v])=>'L'+x(i).toFixed(1)+' '+y(v).toFixed(1)).join('');g+=`<path d="${up}${dn}Z" fill="${css('--band')}"/>`;}
  lines.forEach(([k,_,c,a,w])=>{if(a)g+=`<path d="${path(a)}" fill="none" stroke="${css(c)}" stroke-width="${w}" stroke-linejoin="round"/>`;});
  g+=`<line class="xh" y1="${T}" y2="${Hh-B}" stroke="${css('--mute')}" stroke-dasharray="3 3" visibility="hidden"/><rect x="${L}" y="${T}" width="${W-L-R}" height="${Hh-T-B}" fill="transparent"/>`;
  svg.setAttribute('viewBox',`0 0 ${W} ${Hh}`);svg.innerHTML=g;
  fig.querySelector('.legend').innerHTML=lines.filter(l=>l[3]).map(([k,t,c])=>`<span><i style="background:var(${c})"></i>${t}</span>`).join('')+(tm&&tm.lo?'<span><i class="band"></i>its 10–90% band</span>':'');
  const xh=svg.querySelector('.xh'),move=e=>{const r=svg.getBoundingClientRect(),px=(e.touches?e.touches[0].clientX:e.clientX)-r.left,i=Math.max(0,Math.min(n-1,Math.round((px*W/r.width-L)/((W-L-R)/(n-1)))));
    xh.setAttribute('x1',x(i));xh.setAttribute('x2',x(i));xh.setAttribute('visibility','visible');
    tip.innerHTML=`<b>${s.dates[i]}</b>`+lines.filter(l=>l[3]).map(([k,t,c,a])=>`<div><span style="color:var(${c})">${t.replace(' (best baseline)','')}</span><span class="num">${a[i]==null?'—':a[i].toFixed(1)}</span></div>`).join('')+(tm&&tm.lo?`<div><span class="mute">band</span><span class="num">${tm.lo[i].toFixed(1)}–${tm.hi[i].toFixed(1)}</span></div>`:'');
    tip.hidden=false;const left=(x(i)/W)*r.width;tip.style.left=Math.min(r.width-tip.offsetWidth,Math.max(0,left+(left>r.width/2?-tip.offsetWidth-12:12)))+'px';};
  svg.onmousemove=move;svg.ontouchmove=move;svg.ontouchstart=move;svg.onmouseleave=()=>{tip.hidden=true;xh.setAttribute('visibility','hidden');};
}
const all=()=>document.querySelectorAll('figure.chart').forEach(draw);
document.querySelectorAll('[data-h]').forEach(b=>b.onclick=()=>{H=+b.dataset.h;document.querySelectorAll('[data-h]').forEach(c=>c.setAttribute('aria-pressed',c===b));all();});
addEventListener('resize',all);matchMedia('(prefers-color-scheme: dark)').addEventListener('change',all);all();
</script></body></html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", help="run.py's --out folder")
    ap.add_argument("--out", default="timesfm_report.html")
    args = ap.parse_args(argv)
    with open(args.out, "w") as f:
        f.write(page(args.results))
    print(args.out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
