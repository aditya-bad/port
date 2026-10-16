// Compare — how does A compare to B. Starts empty with quick picks;
// results lead with a verdict, then best-for, equity (₹ or % of capital,
// optional combined line), correlation, month by month and metric winners.

import { html, raw, mount, $, actions, on } from '../core/dom.js';
import { inr, inrS, pct, dShort, MONTHS, ist, fromDay, plural, tone } from '../core/format.js';
import { pnlOf } from '../core/basis.js';
import { ui, listen } from '../core/store.js';
import { go, href } from '../core/router.js';
import * as book from '../data/book.js';
import { periodMetrics, periodStart, verdictRdd, fmtRatio, correlation, ddSeries } from '../data/metrics.js';
import { seg, check, empty } from '../ui/components.js';
import { registerScrub } from '../ui/tooltip.js';
import { yOf } from '../ui/charts.js';
import { say } from '../ui/toast.js';

const st = ui('compare', { ids: [], p: 'all', norm: false, comb: false, mode: 'all', q: '', view: false });
const C = ['var(--ink)', 'var(--wr)', 'var(--b)', 'var(--c4)', 'var(--c5)'];
const MAX = 5;
const short = n => (n.length > 16 ? n.replace(/^(Nifty|BankNifty|Sensex|FinNifty|Midcap|Bankex) /, '') : n);

function quickPicks(all) {
  const judged = all.filter(v => !v.young && v.trades.length).map(v => ({ v, m: periodMetrics({ trades: v.trades, daily: v.daily, cap: v.cap, pnlOf, period: 'all' }) }))
    .sort((a, b) => b.m.rdd - a.m.rdd);
  const P = [['Top 3 risk-adjusted', 'best return/drawdown, all-time', judged.slice(0, 3).map(x => x.v.id)]];
  const byStrat = {};
  all.forEach(v => { (byStrat[v.strategy] = byStrat[v.strategy] || []).push(v); });
  Object.entries(byStrat).filter(([, L]) => L.length >= 2).sort((a, b) => b[1].length - a[1].length).slice(0, 2)
    .forEach(([k, L]) => P.push([`Every ${(book.strategy(k) || { label: k }).label}`, 'same strategy, different settings', L.slice(0, MAX).map(v => v.id)]));
  const bi = judged.find(x => x.v.modeKey === 'intraday'), bp = judged.find(x => x.v.modeKey === 'positional');
  if (bi && bp) P.push(['Best intraday vs best positional', 'which style is working', [bi.v.id, bp.v.id]]);
  book.tags().forEach(t => {
    const L = all.filter(v => v.tags.includes(t.name));
    if (L.length >= 2) P.push([`Tagged ${t.name}`, `${L.length} deployments`, L.slice(0, MAX).map(v => v.id)]);
  });
  const byIdx = {};
  all.forEach(v => { (byIdx[v.index] = byIdx[v.index] || []).push(v); });
  Object.entries(byIdx).filter(([k, L]) => L.length >= 2 && k !== '—').forEach(([k, L]) => P.push([`Everything on ${k}`, 'same index exposure', L.slice(0, MAX).map(v => v.id)]));
  return P.filter(p => p[2].length >= 2).slice(0, 7);
}

export default {
  title: 'Compare',
  render(main, params) {
    if (params.ids) st.set({ ids: params.ids.split(',').filter(Boolean).slice(0, MAX), view: true });

    function picker(all) {
      const s = st.get(), ids = s.ids;
      const q = s.q.toLowerCase();
      const L = all.filter(v => (s.mode === 'all' || v.modeKey === s.mode) && (!q || `${v.name} ${v.strategy}`.toLowerCase().includes(q)));
      return html`
        <div class="page stack" style="gap:18px;padding-bottom:140px">
          <div class="col" style="gap:6px"><h1 class="t-h1">What do you want to compare?</h1>
            <span class="mute" style="max-width:720px">Pick 2 to 5 deployments. You’ll see who wins each metric, whether they move together, and how they’d look combined.</span></div>
          <div class="col" style="gap:8px"><b class="small">Quick picks</b><div class="row wrap" style="gap:6px">${quickPicks(all).map((p, i) => html`
            <button type="button" class="quick" data-act="preset" data-i="${i}"><span>${p[0]}</span><span class="tiny mute">${p[1]}</span></button>`)}</div></div>
          <div class="row wrap"><input class="input" style="width:320px;max-width:100%" type="search" placeholder="Search deployments" value="${s.q}" data-q aria-label="Search deployments">
            <div class="chips">${[['all', 'All'], ['intraday', 'Intraday'], ['positional', 'Positional']].map(([k, l]) => html`<button type="button" class="chip ${s.mode === k ? 'on' : ''}" data-act="mode" data-v="${k}">${l}</button>`)}</div></div>
          <div class="tiles">${L.map(v => {
            const m = periodMetrics({ trades: v.trades, daily: v.daily, cap: v.cap, pnlOf, period: 'all' });
            const i = ids.indexOf(v.id);
            return html`<button type="button" class="tile ${i >= 0 ? 'on' : ''}" data-act="toggle" data-id="${v.id}" aria-pressed="${i >= 0}">
              <span class="tile-box">${i >= 0 ? i + 1 : ''}</span>
              <span class="col" style="gap:3px;min-width:0"><b class="ell">${v.name}</b><span class="mono mute ell" style="font-size:11px">${v.strategy} · ${v.mode}</span>
                <span class="row mono tiny" style="gap:14px;padding-top:4px"><span class="${tone(m.ret)}">${pct(m.ret)}</span><span>R/DD ${m.ddp < 0 ? fmtRatio(m.rdd) : '—'}</span><span>Win ${m.n ? `${m.win.toFixed(0)}%` : '—'}</span></span></span>
            </button>`;
          })}</div>
        </div>
        <div class="cmp-bar">
          <b>${ids.length ? `${ids.length} of ${MAX} selected` : 'Nothing selected yet'}</b>
          ${ids.map((id, i) => { const v = book.find(id); return v ? html`<span class="cmp-chip"><span class="swatch" style="background:${C[i]}"></span>${v.name}<button type="button" data-act="toggle" data-id="${id}" aria-label="Remove ${v.name}">×</button></span>` : ''; })}
          <span class="grow"></span>
          ${s.wasOn && ids.length >= 2 ? html`<button type="button" class="btn btn-quiet" data-act="back">Back to comparison</button>` : ''}
          <button type="button" class="btn ${ids.length >= 2 ? 'btn-primary' : ''}" data-act="go" ${ids.length >= 2 ? '' : html`aria-disabled="true" data-reason="Pick at least 2 deployments."`}>${ids.length >= 2 ? `Compare ${ids.length}` : 'Pick at least 2'}</button>
        </div>`;
    }

    function results(all) {
      const s = st.get();
      const deps = s.ids.map(id => all.find(v => v.id === id)).filter(Boolean).map((v, i) => ({ v, c: C[i], m: periodMetrics({ trades: v.trades, daily: v.daily, cap: v.cap, pnlOf, period: s.p }) }));
      const from = periodStart(s.p);
      // Equity series: cumulative realized from the period start.
      const S = deps.map(x => {
        let cu = 0;
        const pts = x.v.daily.filter(r => fromDay(r.day) >= from).map(r => { cu += pnlOf(r); return { t: fromDay(r.day).getTime(), v: s.norm ? cu / x.v.cap * 100 : cu, d: pnlOf(r) }; });
        return { pts, c: x.c, w: 2.5, dash: '', name: x.v.name };
      });
      if (s.comb) {
        const map = {};
        S.forEach(x => x.pts.forEach(p => { map[p.t] = (map[p.t] || 0) + p.d; }));
        const capSum = deps.reduce((a, x) => a + x.v.cap, 0);
        let cu = 0;
        S.push({ pts: Object.keys(map).map(Number).sort((a, b) => a - b).map(t => { cu += map[t]; return { t, v: s.norm ? cu / capSum * 100 : cu }; }), c: 'var(--mute)', w: 2, dash: '6 5', name: 'All combined' });
      }
      const allPts = S.flatMap(x => x.pts);
      const t0 = allPts.length ? Math.min(...allPts.map(p => p.t)) : from.getTime(), t1 = allPts.length ? Math.max(...allPts.map(p => p.t)) : Date.now();
      const vmax = Math.max(s.norm ? 0.1 : 1, ...allPts.map(p => p.v)), vmin = Math.min(0, ...allPts.map(p => p.v));
      const X = t => ((t - t0) / ((t1 - t0) || 1) * 1000).toFixed(1);
      const dds = S.map(x => ddSeries(x.pts.map(p => p.v)));
      const ddl = Math.min(s.norm ? -0.01 : -1, ...dds.flat());
      const fmt = q => (s.norm ? pct(q, 1) : inrS(q));
      registerScrub('cmp', f => {
        const t = t0 + f * (t1 - t0), L = [dShort(t)], ys = [], cs = [];
        S.forEach(x => {
          let p = null;
          for (const q of x.pts) { if (q.t <= t) p = q; else break; }
          if (p) { L.push(`${x.name}  ${fmt(p.v)}`); ys.push(yOf(p.v, 240, vmin, vmax) / 240); cs.push(x.c); }
        });
        return { t: L.join('\n'), ys, cs };
      });

      // Metric by metric
      const R = [
        ['Return on capital', 'higher is better', m => pct(m.ret), m => m.ret, m => tone(m.ret)],
        ['Realized P&L', '', m => inrS(m.pnl), m => m.pnl, m => tone(m.pnl)],
        ['Max drawdown', 'shallower is better', m => pct(m.ddp), m => m.ddp],
        ['Return / drawdown', 'reward per unit of pain', m => fmtRatio(m.rdd), m => m.rdd],
        ['Win rate', '', m => `${m.win.toFixed(1)}%`, m => m.win],
        ['Profit factor', 'gross wins ÷ gross losses', m => fmtRatio(m.pf), m => (m.pf >= 99 ? 0 : m.pf)],
        ['Average win', '', m => inr(m.aw), m => m.aw],
        ['Average loss', 'smaller is better', m => inr(m.al), m => m.al],
        ['Win / loss size', 'average win ÷ average loss', m => (m.al ? `${(m.aw / -m.al).toFixed(2)}×` : '—'), m => (m.al ? m.aw / -m.al : 0)],
        ['Current losing streak', 'fewer is better', m => (m.streak ? String(m.streak) : 'none'), m => -m.streak],
        ['Capital', 'not scored', null, null],
        ['Trades', 'sample size, not scored', m => String(m.n), null],
      ];
      const wins = deps.map(() => 0);
      const mrows = R.map(([l, h, f, val, col]) => {
        let b = -1;
        if (val) {
          const vs = deps.map(x => val(x.m)), mx = Math.max(...vs);
          if (vs.filter(q => q === mx).length === 1) { b = vs.indexOf(mx); wins[b]++; }
        }
        return { l, h, win: b >= 0 ? deps[b].v.name : val ? 'Tie' : '—', cells: deps.map((x, i) => ({ v: l === 'Capital' ? inrS(x.v.cap, { sign: false }) : f(x.m), cls: col ? col(x.m) : '', best: i === b })) };
      });
      const scored = R.filter(r => r[3]).length, bi = wins.indexOf(Math.max(...wins)), Ld = deps[bi], others = deps.filter((_, i) => i !== bi);
      const leaders = deps.filter((_, i) => wins[i] === wins[bi]);
      const verdict = leaders.length > 1
        ? `${leaders.map(x => x.v.name).join(' and ')} tie, each winning ${wins[bi]} of ${scored} metrics. On return/drawdown: ${deps.map(x => `${x.v.name} ${fmtRatio(x.m.rdd)}`).join(', ')}.`
        : `${Ld.v.name} wins ${wins[bi]} of ${scored} metrics. Its return/drawdown of ${fmtRatio(Ld.m.rdd)} is ${verdictRdd(Ld.m.rdd) || 'not measurable yet'}, against ${others.map(x => `${fmtRatio(x.m.rdd)} for ${x.v.name}`).join(' and ')}.`;
      const best = (fn, fm) => { const x = deps.reduce((p, q) => (fn(q.m) > fn(p.m) ? q : p), deps[0]); return { name: x.v.name, c: x.c, v: fm(x.m) }; };
      const bestFor = [
        ['Highest return', best(m => m.ret, m => pct(m.ret))], ['Shallowest drawdown', best(m => m.ddp, m => `DD ${pct(m.ddp)}`)],
        ['Best risk-adjusted', best(m => m.rdd, m => `R/DD ${fmtRatio(m.rdd)}`)], ['Most often right', best(m => m.win, m => `${m.win.toFixed(0)}% wins`)],
      ];
      // Correlation of daily P&L
      const dmaps = deps.map(x => { const o = {}; x.v.daily.filter(r => fromDay(r.day) >= from).forEach(r => { o[r.day] = pnlOf(r); }); return o; });
      const CM = deps.map((_, i) => deps.map((__, j) => (i === j ? 1 : correlation(dmaps[i], dmaps[j]))));
      let hi = null;
      CM.forEach((row, i) => row.forEach((r, j) => { if (j > i && r !== null && (!hi || r > hi.r)) hi = { i, j, r }; }));
      const corNote = !hi ? 'Not enough overlapping trading days to judge.' : hi.r > 0.6
        ? `${deps[hi.i].v.name} and ${deps[hi.j].v.name} move together (${hi.r.toFixed(2)}). Running both is close to doubling one bet.`
        : hi.r > 0.3 ? `Some overlap: the closest pair is ${deps[hi.i].v.name} and ${deps[hi.j].v.name} at ${hi.r.toFixed(2)}.`
          : `These diversify each other well. No pair is above ${hi.r.toFixed(2)}.`;
      // Last 12 months, return on capital
      const now = ist(new Date());
      const months = Array.from({ length: 12 }, (_, k) => { const mo = now.mo - 11 + k; return [now.y + Math.floor(mo / 12), ((mo % 12) + 12) % 12]; });
      const grid = deps.map(x => {
        const mm = {};
        x.v.daily.forEach(r => { const k = r.day.slice(0, 7); mm[k] = (mm[k] || 0) + pnlOf(r); });
        return { x, cells: months.map(([y, mo]) => {
          const k = `${y}-${String(mo + 1).padStart(2, '0')}`, p = mm[k];
          const running = x.v.created <= new Date(y, mo + 1, 0);
          if (p === undefined && !running) return { v: '', bg: 'transparent', tip: `${MONTHS[mo]} ${y}\nnot running` };
          const r2 = (p || 0) / x.v.cap * 100, a2 = Math.min(1, Math.abs(r2) / 2.5);
          return { v: r2 ? `${r2 > 0 ? '+' : '−'}${Math.abs(r2).toFixed(1)}` : '0', tip: `${MONTHS[mo]} ${y}\nP&L  ${inr(p || 0)}\nOf capital  ${pct(r2)}`,
            bg: r2 === 0 ? 'var(--soft)' : `color-mix(in oklch, ${r2 > 0 ? 'var(--pos)' : 'var(--r)'} ${Math.round(12 + a2 * 55)}%, var(--paper))` };
        }) };
      });
      const colsTpl = `repeat(${deps.length}, minmax(110px, 1fr))`;
      const eqPath = x => x.pts.map((p, j) => `${j ? 'L' : 'M'}${X(p.t)} ${yOf(p.v, 240, vmin, vmax).toFixed(1)}`).join(' ');
      const ddPath = (x, i) => x.pts.map((p, j) => `${j ? 'L' : 'M'}${X(p.t)} ${(dds[i][j] / ddl * 76 + 2).toFixed(1)}`).join(' ');
      return html`
        <div class="page stack" style="gap:18px;padding-bottom:0">
          <div class="row wrap end" style="gap:14px">
            <h1 class="t-h1">Compare</h1>
            <div class="row wrap" style="gap:6px">${deps.map(x => html`<span class="cmp-chip"><span class="swatch" style="background:${x.c}"></span>${x.v.name}<button type="button" data-act="remove" data-id="${x.v.id}" aria-label="Remove ${x.v.name}">×</button></span>`)}
              <button type="button" class="cmp-chip dashed" data-act="edit">Change selection</button></div>
            <span class="grow"></span>
            ${seg([['abs', '₹'], ['pct', '% of capital']], s.norm ? 'pct' : 'abs', 'norm')}
            ${seg([['month', 'Month'], ['year', 'Year'], ['all', 'All-time']], s.p, 'period', 'lg')}
          </div>
          <div class="verdict">${verdict}</div>
          <div class="stats" style="grid-template-columns:var(--g-4);border-bottom:1px solid var(--line);padding-bottom:14px">${bestFor.map(([l, b]) => html`
            <div class="col" style="gap:3px"><span class="label">${l}</span><b class="row" style="gap:7px"><span class="swatch" style="background:${b.c}"></span>${b.name}</b><span class="mono tiny">${b.v}</span></div>`)}</div>
          <div>
            <div class="sect"><h3 class="t-h3">Equity</h3><span class="mute small">${s.norm ? 'return on each one’s capital' : 'cumulative realized ₹'} · top ${fmt(vmax)}${s.comb ? ' · dashed = all of them together' : ''}</span>
              <span class="grow"></span>${check(s.comb, 'Show combined', 'comb')}</div>
            ${allPts.length ? html`
              <svg data-scrub="cmp" viewBox="0 0 1000 240" preserveAspectRatio="none" style="width:100%;height:240px;border-bottom:1.5px solid var(--ink);overflow:visible" role="img" aria-label="Equity curves">
                ${raw(`<line x1="0" x2="1000" y1="${yOf(0, 240, vmin, vmax).toFixed(1)}" y2="${yOf(0, 240, vmin, vmax).toFixed(1)}" stroke="var(--line2)" stroke-dasharray="3 5" vector-effect="non-scaling-stroke"/>`)}
                ${S.map(x => raw(`<path d="${eqPath(x)}" fill="none" stroke="${x.c}" stroke-width="${x.w}" ${x.dash ? `stroke-dasharray="${x.dash}"` : ''} vector-effect="non-scaling-stroke"/>`))}
              </svg>
              <svg viewBox="0 0 1000 80" preserveAspectRatio="none" style="width:100%;height:80px" aria-hidden="true">
                ${S.map((x, i) => raw(`<path d="${ddPath(x, i)}" fill="none" stroke="${x.c}" stroke-width="1.5" ${x.dash ? `stroke-dasharray="${x.dash}"` : ''} vector-effect="non-scaling-stroke"/>`))}
              </svg>
              <div class="axis-row"><span>${dShort(t0)}</span><span>drawdown from peak · low ${fmt(ddl)}</span><span>${dShort(t1)}</span></div>`
              : empty('', 'No closed trades in this period.')}
          </div>
          <div class="grid-two" style="padding-top:10px">
            <div class="scroll-x">
              <h3 class="t-h3" style="padding-bottom:4px">Do they move together?</h3>
              <p class="mute small" style="margin:0 0 12px">Correlation of daily P&L. Near 1 means the same bet twice; near 0 or below means they diversify each other.</p>
              <div class="corr" style="grid-template-columns:120px repeat(${deps.length}, minmax(60px, 1fr))">
                <span></span>${deps.map(x => html`<span class="corr-h"><span class="swatch" style="background:${x.c}"></span>${short(x.v.name)}</span>`)}
                ${deps.map((x, i) => html`<span class="corr-h"><span class="swatch" style="background:${x.c}"></span>${short(x.v.name)}</span>
                  ${CM[i].map((r, j) => html`<span class="corr-c" data-tip="${r === null ? 'Too few shared days' : `${x.v.name} vs ${deps[j].v.name}`}"
                    style="background:${i === j ? 'var(--soft)' : r === null ? 'transparent' : r > 0.6 ? 'var(--y)' : r > 0.3 ? 'var(--ys)' : 'var(--gs)'}">${r === null ? '—' : i === j ? '' : r.toFixed(2)}</span>`)}`)}
              </div>
              <p class="small pretty" style="margin:12px 0 0">${corNote}</p>
            </div>
            <div class="scroll-x">
              <h3 class="t-h3" style="padding-bottom:4px">Month by month</h3>
              <p class="mute small" style="margin:0 0 12px">Return on capital each month, last 12 months.</p>
              <div class="mgrid">
                <span></span>${months.map(([, mo]) => html`<span class="hh">${MONTHS[mo]}</span>`)}
                ${grid.map(g => html`<span class="corr-h"><span class="swatch" style="background:${g.x.c}"></span>${short(g.x.v.name)}</span>
                  ${g.cells.map(c => html`<span class="mc" data-tip="${c.tip}" style="background:${c.bg}">${c.v}</span>`)}`)}
              </div>
            </div>
          </div>
        </div>
        <div class="pad-x scroll-x" style="padding-top:26px;padding-bottom:var(--px)">
          <div class="mrow head" style="grid-template-columns:240px ${colsTpl} 200px;min-width:${440 + deps.length * 118}px">
            <h3 class="t-h3">Metric by metric</h3>${deps.map(x => html`<span class="row" style="justify-content:flex-end;gap:6px;font-size:13px"><span class="swatch" style="background:${x.c}"></span>${short(x.v.name)}</span>`)}<span class="tiny mute" style="padding-left:16px">Winner</span>
          </div>
          ${mrows.map(m => html`<div class="mrow" style="grid-template-columns:240px ${colsTpl} 200px;min-width:${440 + deps.length * 118}px">
            <span class="col" style="gap:0"><b>${m.l}</b><span class="tiny mute">${m.h}</span></span>
            ${m.cells.map(c => html`<span class="mcell ${c.best ? 'best' : ''}"><span class="dia sm" style="opacity:${c.best ? 1 : 0}"></span><span class="mono ${c.cls}">${c.v}</span></span>`)}
            <span class="mute" style="padding-left:16px;font-weight:600">${m.win}</span></div>`)}
          <div class="mrow tot" style="grid-template-columns:240px ${colsTpl} 200px;min-width:${440 + deps.length * 118}px">
            <b>Metrics won</b>${wins.map(w => html`<span class="right" style="font-family:var(--f-display);font-size:26px;font-weight:700">${w}</span>`)}<b style="padding-left:16px">${leaders.length > 1 ? 'Tie' : `${Ld.v.name} leads`}</b>
          </div>
        </div>`;
    }

    function paint() {
      if (!book.isLoaded('deployments', 'book')) { mount(main, html`<div class="page"><div class="skel" style="height:300px"></div></div>`); return; }
      const all = book.deployments();
      const s = st.get();
      const ids = s.ids.filter(id => all.some(v => v.id === id));
      if (ids.length !== s.ids.length) st.set({ ids });
      mount(main, s.view && ids.length >= 2 ? results(all) : picker(all));
    }
    const set = p => { st.set(p); paint(); };
    const off = actions(main, {
      preset: d => { const all = book.deployments(); set({ ids: quickPicks(all)[+d.i][2] }); },
      mode: d => set({ mode: d.v }),
      toggle: d => {
        const ids = st.get().ids;
        if (ids.includes(d.id)) set({ ids: ids.filter(i => i !== d.id) });
        else if (ids.length >= MAX) say(`Compare takes up to ${MAX}.`);
        else set({ ids: [...ids, d.id] });
      },
      go: (d, el) => (el.dataset.reason ? say(el.dataset.reason) : set({ view: true })),
      back: () => set({ view: true }),
      edit: () => set({ view: false, wasOn: true }),
      remove: d => { const ids = st.get().ids; if (ids.length <= 2) return say('Compare needs at least 2.'); set({ ids: ids.filter(i => i !== d.id) }); },
      norm: d => set({ norm: d.v === 'pct' }),
      period: d => set({ p: d.v }),
      comb: () => set({ comb: !st.get().comb }),
    });
    const offQ = on(main, 'input', '[data-q]', (e, el) => {
      st.set({ q: el.value });
      const pos = el.selectionStart;
      paint();
      const n = $('[data-q]', main); if (n) { n.focus(); n.setSelectionRange(pos, pos); }
    });
    paint();
    const offs = [listen('data', paint)];
    if (params.ids) history.replaceState(null, '', '#/compare');
    return () => { off(); offQ(); offs.forEach(f => f()); };
  },
};
