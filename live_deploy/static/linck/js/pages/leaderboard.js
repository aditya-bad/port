// Leaderboard — who's winning. One board per question (returns,
// risk-adjusted, drawdown, win rate, profit factor, consistency, P&L),
// each with a podium and a plain explanation of what it rewards, over
// this month, this year or all-time. Young deployments are listed, never ranked.

import { html, mount, actions } from '../core/dom.js';
import { inr, inrS, pct, plural, tone, dayKey } from '../core/format.js';
import { pnlOf } from '../core/basis.js';
import { ui, listen } from '../core/store.js';
import { go, href } from '../core/router.js';
import * as book from '../data/book.js';
import { periodMetrics, verdictRdd, verdictPf, fmtRatio } from '../data/metrics.js';
import { STREAK_ALERT } from '../data/config.js';
import { seg, check, empty } from '../ui/components.js';
import { say } from '../ui/toast.js';
import { cyclesMark } from '../ui/repairs.js';

const st = ui('leaderboard', { p: 'year', sk: 'rdd', lab: false, board: 'overview' });

// key: [label, format, colour?, hint, board title, what it rewards]
const K = {
  cons: ['Consistency', m => (m.n ? `${m.cons.toFixed(0)}%` : '—'), null, 'green months', 'Consistency board', 'Share of months that ended green. Rewards steady earners over one-lucky-month wonders.'],
  ret: ['Return', m => pct(m.ret), m => tone(m.ret), 'on capital', 'Returns board', 'Who made the most on the capital given to them. Raw return ignores how bumpy the ride was.'],
  dd: ['Max DD', m => (m.ddp < 0 ? pct(m.ddp) : '0.00%'), null, 'shallowest', 'Lowest drawdown board', 'The shallowest worst dip, as a share of capital. Rewards capital protection. A deployment that barely trades can look good here; check its returns too.'],
  rdd: ['Return/DD', m => (m.ddp < 0 ? fmtRatio(m.rdd) : '—'), null, 'higher wins', 'Risk-adjusted board', 'Return divided by the worst peak-to-trough fall. Rewards earning a lot without deep dips. The fairest single number for comparing strategies.'],
  win: ['Win rate', m => `${m.win.toFixed(0)}%`, null, 'of trades', 'Win rate board', 'Share of trades that closed in profit. High win rates are common for option sellers; pair it with profit factor to see if losses are kept small.'],
  pf: ['Profit factor', m => fmtRatio(m.pf), null, 'wins ÷ losses', 'Profit factor board', 'Gross profits divided by gross losses. Above 1.5 is healthy, above 2 strong.'],
  pnl: ['P&L', m => inrS(m.pnl), m => tone(m.pnl), 'realized', 'Biggest P&L board', 'Rupees made, regardless of capital. Bigger allocations naturally rank higher.'],
};
const BOARDS = [['overview', 'All boards'], ['ret', 'Returns'], ['rdd', 'Risk-adjusted'], ['dd', 'Lowest drawdown'], ['win', 'Win rate'], ['pf', 'Profit factor'], ['cons', 'Consistency'], ['pnl', 'Biggest P&L']];
const PERIOD_WORD = { month: 'this month', year: 'this year to date', all: 'all-time' };
const val = (k, m) => (k === 'dd' ? m.ddp : k === 'pf' && m.pf >= 99 ? 0 : k === 'n' ? m.n : m[k]);

// The first day of the period ranked (month, year), for its repaired cycles.
const since = p => { const k = dayKey(new Date()); return p === 'month' ? `${k.slice(0, 8)}01` : p === 'year' ? `${k.slice(0, 5)}01-01` : undefined; };

export function rankRows(period, lab) {
  return book.deployments().filter(v => lab || !v.isLab).map(v => {
    const m = periodMetrics({ trades: v.trades, daily: v.daily, cap: v.cap, pnlOf, period });
    return { v, m, unranked: v.young || !m.n };
  });
}

export default {
  title: 'Leaderboard',
  render(main) {
    const sel = new Set();
    function paint() {
      if (!book.isLoaded('deployments', 'book')) { mount(main, html`<div class="page"><div class="skel" style="height:300px"></div></div>`); return; }
      const s = st.get();
      const L = rankRows(s.p, s.lab);
      const judged = L.filter(x => !x.unranked);
      const rank = (k, list) => [...list].sort((a, b) => (a.unranked - b.unranked) || (val(k, b.m) - val(k, a.m)));
      const board = s.board || 'overview';
      const item = (k, x) => ({ name: x.v.name, id: x.v.id, v: K[k][1](x.m), c: K[k][2] ? K[k][2](x.m) : '' });
      let rk = 0;
      const rows = rank(s.sk, L).map(x => ({ ...x, rank: x.unranked ? '—' : ++rk }));
      const flags = x => {
        const f = [];
        if (x.v.young) f.push(['New', '']);
        if (x.v.streak >= STREAK_ALERT) f.push([`Losing streak ${x.v.streak}`, 'bad']);
        if (x.v.quiet) f.push([`Quiet ${x.v.quietDays}d`, '']);
        if (x.v.rawStatus === 'stopped') f.push(['Stopped', '']);
        if (x.v.isLab) f.push(['Lab', 'lab']);
        return f;
      };
      let boardHtml = '';
      if (board === 'overview') {
        boardHtml = html`<div class="tops pad-x">${Object.keys(K).map(k => {
          const r = rank(k, judged).slice(0, 3);
          const first = r[0] ? item(k, r[0]) : null;
          return html`<button type="button" class="top-card" data-act="sk" data-v="${k}">
            <span class="row small" style="justify-content:space-between"><b>${K[k][0]}</b><span class="mute">${K[k][3]}</span></span>
            <span class="top-v ${first ? first.c : ''}">${first ? first.v : '—'}</span>
            <b class="small">${first ? first.name : 'No ranked deployments'}</b>
            ${r.slice(1).map((x, j) => { const it = item(k, x); return html`<span class="top-rest"><span class="mono mute">${j + 2}</span><span class="ell mute">${it.name}</span><span class="mono">${it.v}</span></span>`; })}
          </button>`;
        })}</div>`;
      } else {
        const k = board, r = rank(k, judged), top3 = r.slice(0, 3), mx = Math.max(1e-9, ...r.map(x => Math.abs(val(k, x.m))));
        const H = ['150px', '111px', '84px'];
        const sub = m => ((k === 'ret' || k === 'pnl') ? `R/DD ${K.rdd[1](m)} · ${plural(m.n, 'trade')}` : k === 'win' ? `PF ${K.pf[1](m)} · ${plural(m.n, 'trade')}` : k === 'rdd' ? `return ${pct(m.ret)} · DD ${K.dd[1](m)}` : `return ${pct(m.ret)}`);
        boardHtml = html`<div class="board pad-x"><div class="board-in">
          <div class="board-l">
            <h2 class="t-h2">${K[k][4]}</h2>
            <p class="mute pretty" style="margin:0;max-width:560px">${K[k][5]} Period: ${PERIOD_WORD[s.p]}.</p>
            ${top3.length ? html`<div class="podium">${[1, 0, 2].filter(i => top3[i]).map(i => { const x = top3[i], it = item(k, x); return html`
              <a class="pod" href="${href(`d/${x.v.id}/performance`)}">
                <span class="col" style="gap:2px;min-width:0"><span class="pod-v ${it.c}">${it.v}</span><b class="small ell">${it.name}</b><span class="tiny mute">${sub(x.m)}</span></span>
                <span class="pod-bar grow-y p${i + 1}" style="height:${H[i]};animation-delay:${i * 120}ms"><span>${i + 1}</span></span>
              </a>`; })}</div>` : empty('', 'No deployment has enough history to rank in this period.')}
          </div>
          <div class="board-r">
            <b style="padding-bottom:8px">Rest of the field</b>
            ${r.slice(3, 13).map((x, j) => { const q = val(k, x.m), it = item(k, x); return html`
              <a class="field-rank" href="${href(`d/${x.v.id}/performance`)}"><span class="mono tiny mute">${j + 4}</span>
                <span class="col" style="gap:3px;min-width:0"><span class="small ell" style="font-weight:600">${it.name}</span>
                  <span class="meter" style="height:4px"><i style="width:${k === 'dd' ? (1 - Math.abs(q) / mx) * 100 : Math.max(0, q) / mx * 100}%;background:${q < 0 && k !== 'dd' ? 'var(--r)' : k === 'dd' ? 'var(--ink)' : 'var(--pos)'}"></i></span></span>
                <span class="mono small right ${it.c}">${it.v}</span></a>`; })}
            <span class="tiny mute" style="padding-top:8px">${plural(L.length - judged.length, 'deployment')} unranked (under 14 days old or no trades in the period).</span>
          </div>
        </div></div>`;
      }
      const cols = [['pnl', 'P&L'], ['ret', 'Return'], ['dd', 'Max DD'], ['rdd', 'Return/DD'], ['win', 'Win rate'], ['pf', 'PF'], ['cons', 'Green months'], ['n', 'Trades']];
      const n = sel.size, ok = n >= 2 && n <= 5;
      mount(main, html`
        <div class="page-head">
          <div class="col"><h1 class="t-h1">Leaderboard</h1><span class="mute">Realized results. Deployments under 14 days old are listed but not ranked.</span></div>
          <span class="grow"></span>
          ${seg([['month', 'Month'], ['year', 'Year'], ['all', 'All-time']], s.p, 'period', 'lg')}
          ${check(s.lab, 'Include Strategy Lab', 'lab')}
        </div>
        <div class="pad-x boards noscroll">${BOARDS.map(([k, l]) => html`<button type="button" class="chip strong ${board === k ? 'on' : ''}" data-act="board" data-v="${k}">${l}</button>`)}</div>
        ${boardHtml}
        <div class="pad-x scroll-x" style="padding-top:22px;padding-bottom:var(--px)">
          <div class="row wrap" style="padding-bottom:12px"><h3 class="t-h3">Ranked by ${(K[s.sk] || ['Trades'])[0]}</h3>
            <span class="mute small hide-phone">click a column to re-rank · tick 2 to 5 rows to compare</span><span class="grow"></span>
            <button type="button" class="btn ${ok ? 'btn-primary' : ''}" data-act="compare" ${ok ? '' : html`aria-disabled="true" data-reason="Tick 2 to 5 rows to compare."`}>Compare ${n} selected</button></div>
          ${L.length ? html`
          <div class="lb-tbl">
            <div class="tr th"><span></span><span>#</span><span>Deployment</span>
              ${cols.map(([k, l]) => html`<button type="button" class="right ${s.sk === k ? 'on' : ''}" data-act="sk" data-v="${k}">${l}${s.sk === k ? ' ↓' : ''}</button>`)}
              <span style="padding-left:16px">Flags</span></div>
            ${rows.map(x => html`
              <div class="tr hover ${sel.has(x.v.id) ? 'sel' : ''} ${x.unranked ? 'unranked' : ''}">
                <button type="button" class="box ${sel.has(x.v.id) ? 'on' : ''}" data-act="sel" data-id="${x.v.id}" aria-label="Select ${x.v.name}"></button>
                <span class="lb-rank ${x.rank !== '—' && x.rank <= 3 ? 'big' : ''}">${x.rank}</span>
                <a class="cell-name" href="${href(`d/${x.v.id}/performance`)}"><b>${x.v.name}</b><span>${x.v.strategy}</span></a>
                <span class="num ${tone(x.m.pnl)}">${inr(x.m.pnl)}</span>
                <span class="num ${tone(x.m.ret)}">${pct(x.m.ret)}</span>
                <span class="num">${K.dd[1](x.m)}</span>
                <span class="row" style="justify-content:flex-end;gap:6px;align-items:baseline"><span class="mono small">${K.rdd[1](x.m)}</span><span class="tiny mute" style="width:56px">${x.m.ddp < 0 ? verdictRdd(x.m.rdd) : ''}</span></span>
                <span class="num">${x.m.n ? `${x.m.win.toFixed(0)}%` : '—'}</span>
                <span class="num" title="${verdictPf(x.m.pf)}">${K.pf[1](x.m)}</span>
                <span class="num">${K.cons[1](x.m)}</span>
                <span class="num mute">${x.m.n}</span>
                <span class="row wrap" style="gap:4px;padding-left:16px">${cyclesMark(x.v, since(s.p))}${flags(x).map(([t, c]) => html`<span class="flag ${c}">${t}</span>`)}</span>
              </div>`)}
          </div>
          <p class="tiny mute" style="margin:10px 0 0">Return/DD is return on capital divided by max drawdown in the period: above 3 excellent, 1.5–3 good, 0.8–1.5 fair, below that weak.</p>`
          : empty('No deployments yet', 'Rankings appear once deployments close trades.')}
        </div>`);
    }
    const off = actions(main, {
      period: d => { st.set({ p: d.v }); paint(); },
      lab: () => { st.set({ lab: !st.get().lab }); paint(); },
      board: d => { st.set(d.v === 'overview' ? { board: d.v } : { board: d.v, sk: d.v }); paint(); },
      sk: d => { st.set({ sk: d.v }); paint(); },
      sel: d => { sel.has(d.id) ? sel.delete(d.id) : sel.size < 5 ? sel.add(d.id) : say('Compare takes up to 5.'); paint(); },
      compare: (d, el) => (el.dataset.reason ? say(el.dataset.reason) : go(`compare?ids=${[...sel].join(',')}`)),
    });
    paint();
    const offs = [listen('data', paint)];
    return () => { off(); offs.forEach(f => f()); };
  },
};
