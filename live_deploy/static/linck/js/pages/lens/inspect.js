// LinckLens Inspector (#/lens/inspect/:view): the whole screen, like
// LinckTrade's Geek mode (its shell and styles), over app/lens_inspect.py:
// your own money against what "invested" shows, the yearly tax-free
// long-term gain, your figures without some holdings, and what each
// holding did. Read-only.

import { api } from '../../core/api.js';
import { html, mount, $, $$, on, actions } from '../../core/dom.js';
import { inr, pct, num, plural, tone, dShort, fromDay } from '../../core/format.js';
import { go, href } from '../../core/router.js';
import { empty } from '../../ui/components.js';
import { line, yOf, centreBar } from '../../ui/charts.js';
import { registerScrub } from '../../ui/tooltip.js';
import { head, tiles } from '../geek/ui.js';
import { inr0, signed, pctOr } from './parts.js';

// [key, label, group, keyboard key]
export const VIEWS = [
  ['own', 'Your own money', 'Your money', 'O'],
  ['tax', 'Tax-free gains', 'Your money', 'T'],
  ['whatif', 'Remove holdings', 'What if', 'W'],
  ['drag', 'What each holding did', 'What if', 'D'],
];
const GROUPS = ['Your money', 'What if'];

// what's been read this visit: kept while Inspector is open
const st = { data: null, err: null, without: new Set(), wi: null, wiKey: null, wiErr: null };

const reduced = () => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; } };
const store = (k, v) => { try { sessionStorage.setItem(k, v); } catch (e) { /* private mode */ } };
const read = k => { try { return sessionStorage.getItem(k); } catch (e) { return null; } };

// From LinckLens: the page falls away and a circle opens from the button.
export function enterInspector(from) {
  const r = from && from.getBoundingClientRect ? from.getBoundingClientRect() : null;
  store('geekEnter', JSON.stringify(r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : { x: innerWidth - 80, y: 140 }));
  store('inspectBack', location.hash);
  st.data = null; st.wi = null; st.wiKey = null;
  if (reduced()) { go('lens/inspect'); return; }
  document.body.classList.add('geek-leaving');
  setTimeout(() => go('lens/inspect'), 300);
}

function leave(shell, from) {
  const back = read('inspectBack');
  const to = back && back.startsWith('#/lens') && !back.includes('/inspect') ? back.slice(2) : 'lens';
  if (!shell || reduced()) { go(to); return; }
  if (from && from.getBoundingClientRect) {
    const r = from.getBoundingClientRect();
    shell.style.setProperty('--ox', `${r.left + r.width / 2}px`);
    shell.style.setProperty('--oy', `${r.top + r.height / 2}px`);
  }
  shell.classList.add('gk-out');
  setTimeout(() => go(to), 380);
}

function takeEntry() {
  const v = read('geekEnter');
  try { sessionStorage.removeItem('geekEnter'); } catch (e) { /* ignore */ }
  try { return v ? JSON.parse(v) : null; } catch (e) { return null; }
}

// ── Your own money ─────────────────────────────────────────────────────

function ownView(d) {
  const o = d.own;
  if (!o) return noTrades();
  const extra = o.cost_now - o.own;
  const chartH = 220;
  const ms = o.months;
  let chart = '';
  if (ms.length > 1) {
    const all = ms.flatMap(m => [m.own, m.cost, m.pot]);
    const lo = 0, hi = Math.max(...all) * 1.06 || 1;
    registerScrub('ins-own', f => {
      const i = Math.round(f * (ms.length - 1)), m = ms[i];
      return { t: `${m.month}\nYour own money in  ${inr0(m.own)}\nWhat you hold, at cost  ${inr0(m.cost)}\nSale money not reused  ${inr0(m.pot)}`,
        ys: [yOf(m.own, chartH, lo, hi) / chartH, yOf(m.cost, chartH, lo, hi) / chartH], cs: ['var(--ink)', 'var(--b)'] };
    });
    chart = html`<div class="gk-panel"><h3>Month by month <span class="s">your own money against what you hold at cost</span></h3>
      <div class="lens-legend small"><span><i class="key ink"></i>Your own money in</span><span><i class="key b"></i>What you hold, at cost (“invested”)</span><span><i class="key dash"></i>Sale money not reused</span></div>
      <div class="lens-plot">${line({ series: [{ values: ms.map(m => m.pot), color: 'var(--line2)', width: 1.5, dash: '4 4', label: 'Not reused' },
        { values: ms.map(m => m.cost), color: 'var(--b)', width: 1.5, label: 'At cost' }, { values: ms.map(m => m.own), color: 'var(--ink)', width: 2, label: 'Own money' }],
        h: chartH, key: 'ins-own', lo, hi })}
        <div class="lens-axis tiny mute mono"><span>${ms[0].month}</span><span>${ms[ms.length - 1].month}</span></div></div></div>`;
  }
  return html`${head('Your own money', 'Every sale’s money goes into a pot; a buy is paid from the pot first, and only the rest is new money of yours. Reusing a sale (or selling and buying back for the tax-free gain) raises what “invested” shows, not your own money.', `${plural(d.trades, 'trade')} since ${dShort(fromDay(o.first_day))}`)}
    <p class="ins-headline" data-own>You hold <b>${inr0(o.cost_now)}</b> at cost. <b>${inr0(o.own)}</b> of that is your own money${o.pot > 0 ? html`, <b>${inr0(o.pot)}</b> of sale money sits unused` : ''};
      the other <b class="${tone(extra)}">${inr0(Math.abs(extra))}</b> is ${extra >= 0 ? 'profit you booked and put back in' : 'losses you booked'}.</p>
    ${tiles([
      ['Your own money in', inr0(o.own), 'new money, never from a sale'],
      ['What you hold, at cost', inr0(o.cost_now), 'what “invested” shows'],
      ['Booked profit back in', signed(o.profit_in_holdings, inr(o.profit_in_holdings)), `of ${inr(o.booked)} booked in all`],
      ['Worth now', inr0(o.value), o.unpriced.length ? `${o.unpriced.length} at cost: no price` : 'at today’s prices'],
    ])}
    ${tiles([
      ['Gain on your own money', signed(o.gain, inr(o.gain)), `${pctOr(o.gain_pct, 1)} of ${inr0(o.own)}`],
      ['A year, on your own money', o.xirr == null ? '—' : signed(o.xirr, pct(o.xirr, 1)), 'XIRR of what you put in and what it’s worth'],
      ['Sale money reused', inr0(o.reused), `${inr0(o.sold)} sold in all`],
      ['Bought for each rupee of yours', o.times_turned == null ? '—' : `₹${o.times_turned.toFixed(2)}`, `${inr0(o.bought)} bought in all`],
    ])}
    ${chart}
    <div class="gk-two even">
      <div class="gk-panel"><h3>By financial year</h3>
        <div class="tbl gk-tbl" role="table" data-ins="years">
          <div class="tr th ins-yr" role="row"><span>Year</span><span class="num">New money</span><span class="num">Sale money reused</span><span class="num">Booked</span></div>
          ${o.years.slice().reverse().map(y => html`<div class="tr ins-yr" role="row" data-fy="${y.fy}"><span><b>${y.fy}</b></span>
            <span class="num" data-l="New money">${inr0(y.fresh)}</span><span class="num" data-l="Reused">${inr0(y.reused)}</span>
            <span class="num ${tone(y.booked)}" data-l="Booked">${inr(y.booked)}</span></div>`)}
        </div></div>
      <div class="gk-panel"><h3>Sold and bought back <span class="s">within ${7} days: gains (or losses) booked on purpose</span></h3>
        ${o.harvests.length ? html`<div class="ins-harv">${o.harvests.map(h => html`<div class="ins-harv-i small" data-harvest="${h.symbol}">
          <b>${h.symbol}</b><span class="mono">${num(h.qty, h.qty % 1 ? 3 : 0)} sold ${dShort(fromDay(h.sold_on))}, ${num(h.bought_qty, h.bought_qty % 1 ? 3 : 0)} back ${dShort(fromDay(h.bought_on))}</span>
          <span class="mono ${tone(h.booked)}">${inr(h.booked)} ${h.kind === 'gain' ? 'booked' : 'loss booked'}</span></div>`)}</div>`
          : html`<p class="small mute">None in these trades.</p>`}
      </div>
    </div>
    <p class="gk-note">A tradebook doesn’t show money added to or taken out of the account. This assumes a sale’s money stayed there to be reused${o.pot > 0 ? '; what wasn’t reused shows as “not reused”, whether it’s in the account or you took it out' : ''}. If you took sale money out and later added new money, your own money reads lower than it was. Charges aren’t in a tradebook.${o.unmatched ? ` ${inr0(o.unmatched)} was sold beyond what the trades bought (a bonus or a transfer in): counted at no gain.` : ''}</p>`;
}

// ── The tax-free long-term gain ────────────────────────────────────────

function taxView(d) {
  const x = d.tax;
  if (!x) return noTrades();
  return html`${head('Tax-free long-term gains', `Each financial year, long-term gains on listed shares, equity ETFs, equity funds and REITs are tax-free up to ₹${(x.exemption / 100000).toFixed(2)} lakh. Booking them (and buying back) uses the room before the year ends.`, `${x.fy} · ${x.days_left} days left`)}
    ${tiles([
      ['Booked long term this year', signed(x.booked, inr(x.booked)), x.fy],
      ['Tax-free room left', inr0(x.room), `of ${inr0(x.exemption)}`],
      ['The lots below would book', signed(x.plan_gain, inr(x.plan_gain)), x.plan.length ? 'long term, within the room' : 'nothing long term in profit'],
      ['Year ends in', `${x.days_left} days`, '31 March'],
    ])}
    <div class="gk-panel"><h3>To use the room <span class="s">oldest units first, as a sale takes them; most gain for each rupee sold first</span></h3>
      ${x.plan.length ? html`<div class="tbl gk-tbl" role="table" data-ins="plan">
        <div class="tr th ins-plan" role="row"><span>Holding</span><span class="num">Sell</span><span class="num">For about</span><span class="num">Long-term gain</span></div>
        ${x.plan.map(p => html`<div class="tr ins-plan" role="row" data-plan="${p.symbol}"><span><b>${p.symbol}</b>${p.maybe ? html` <span class="tag warn" data-tip="A hybrid fund is covered only if it’s equity-oriented (65% or more in Indian shares)">if equity-oriented</span>` : ''}</span>
          <span class="num" data-l="Sell">${num(p.qty, p.qty % 1 ? 3 : 0)}</span><span class="num" data-l="For about">${inr0(p.sale)}</span>
          <span class="num pos" data-l="Gain">${inr(p.gain)}</span></div>`)}
      </div>` : html`<p class="small mute">Nothing held long term (over 12 months) is in profit right now.</p>`}
      <p class="gk-note">Buying back the same day leaves you holding the same thing at a higher cost, so that gain is never taxed. The units bought back start their own 12 months. Brokerage, STT and stamp duty apply to both legs (and an exit load to some funds). Prices move: check the gain before you sell. A guide, not tax advice.</p>
    </div>
    ${x.available.length ? html`<div class="gk-panel"><h3>Everything held long term <span class="s">covered by the exemption, by gain</span></h3>
      <div class="tbl gk-tbl" role="table" data-ins="available">${x.available.map(a => html`<div class="tr ins-av" role="row"><span><b>${a.symbol}</b></span>
        <span class="num">${num(a.qty, a.qty % 1 ? 3 : 0)} units</span><span class="num">${inr0(a.value)}</span><span class="num ${tone(a.gain)}">${inr(a.gain)}</span></div>`)}</div></div>` : ''}
    <div class="gk-panel"><h3>Year by year</h3>
      <div class="tbl gk-tbl" role="table" data-ins="taxyears">
        <div class="tr th ins-ty" role="row"><span>Year</span><span class="num">Long term</span><span class="num">Tax-free limit</span><span class="num">Above it</span><span class="num">Short term</span></div>
        ${x.years.slice().reverse().map(y => html`<div class="tr ins-ty" role="row"><span><b>${y.fy}</b></span><span class="num ${tone(y.long)}" data-l="Long term">${inr(y.long)}</span>
          <span class="num" data-l="Limit">${inr0(y.exemption)}</span><span class="num ${y.over ? 'neg' : ''}" data-l="Above it">${y.over ? inr0(y.over) : '—'}</span>
          <span class="num ${tone(y.short)}" data-l="Short term">${inr(y.short)}</span></div>`)}
      </div></div>`;
}

// ── Remove holdings ────────────────────────────────────────────────────

const ROWS = [
  ['trades', 'xirr', 'XIRR, everything', v => pctOr(v, 1), 'pct'],
  ['trades', 'own_gain', 'Gain on your own money', v => (v == null ? '—' : inr(v)), 'inr'],
  ['trades', 'own_xirr', 'A year, on your own money', v => pctOr(v, 1), 'pct'],
  ['trades', 'booked', 'Booked', v => (v == null ? '—' : inr(v)), 'inr'],
  ['trades', 'on_paper', 'On paper', v => (v == null ? '—' : inr(v)), 'inr'],
  ['trades', 'vs_nifty', 'Ahead of the same money in Nifty', v => (v == null ? '—' : inr(v)), 'inr'],
];
const NOW_ROWS = [
  ['now', 'value', 'Worth now', v => inr0(v), 'inr'],
  ['now', 'count', 'Holdings', v => String(v), 'n'],
  ['now', 'effective', 'Spread like', v => (v == null ? '—' : `${v} equal holdings`), 'n'],
  ['now', 'top_weight', 'Largest holding', (v, s) => (v == null ? '—' : `${v.toFixed(1)}% ${s.now.top}`), 'pct'],
  ['year', 'ret', 'Today’s shares over the year', v => pctOr(v, 1), 'pct'],
  ['year', 'vol', 'Swings (volatility)', v => (v == null ? '—' : `${v.toFixed(1)}%`), 'pctlow'],
  ['year', 'drawdown', 'Worst fall', v => pctOr(v, 1), 'pct'],
];

function cmpRows(rows, a, b) {
  return rows.map(([part, k, label, fmt, kind]) => {
    const x = a[part] ? a[part][k] : null, y = b[part] ? b[part][k] : null;
    const ch = x == null || y == null || kind === 'n' ? null : y - x;
    const good = ch == null ? 'flat' : kind === 'pctlow' ? tone(-ch) : tone(ch);
    return html`<div class="tr ins-cmp" role="row" data-row="${k}"><span>${label}</span><span class="num" data-l="Now">${fmt(x, a)}</span>
      <span class="num" data-l="Without them">${fmt(y, b)}</span>
      <span class="num ${good}" data-l="Change">${ch == null ? '' : kind === 'inr' ? inr(ch) : `${ch >= 0 ? '+' : '−'}${Math.abs(ch).toFixed(1)} pts`}</span></div>`;
  });
}

function whatifView(d) {
  if (d.holdings_error && !d.holdings.length) return html`${head('Remove holdings')}${empty('Kite isn’t connected', 'Today’s holdings are read from Kite. Connect it, then come back.')}`;
  const picked = st.without;
  const r = st.wi;
  return html`${head('Remove holdings', 'Pick holdings to see your figures without them: had you never bought them (from your trades), and today’s holdings without them over the last year (from the daily prices).')}
    <div class="ins-picks" role="group" aria-label="Holdings to remove">${d.holdings.map(h => html`<button type="button" class="chip ${picked.has(h.isin) ? 'on' : ''}" aria-pressed="${picked.has(h.isin)}" data-act="pick" data-v="${h.isin}">
      ${h.symbol}${h.kind === 'mf' ? html` <span class="n">fund</span>` : ''} <span class="n">${(h.weight || 0).toFixed(1)}%</span></button>`)}
      ${picked.size ? html`<button type="button" class="btn btn-sm btn-ghost" data-act="clear">Clear</button>` : ''}</div>
    ${!picked.size ? html`<p class="small mute" data-whatif="none">Pick one or more above. “What each holding did” ranks them all, one at a time.</p>`
      : st.wiErr ? html`<p class="small warn">${st.wiErr.message}</p>`
      : !r || st.wiKey !== [...picked].sort().join(',') ? html`<div class="skel" style="height:260px"></div>`
      : r.error ? html`<p class="small mute">${r.error}</p>`
      : html`<div class="gk-two even" data-whatif="ready">
        ${r.with.trades ? html`<div class="gk-panel"><h3>Had you never bought them <span class="s">from your trades</span></h3>
          <div class="tbl gk-tbl" role="table"><div class="tr th ins-cmp" role="row"><span></span><span class="num">With them</span><span class="num">Without</span><span class="num">Change</span></div>${cmpRows(ROWS, r.with, r.without_them)}</div></div>`
          : html`<div class="gk-panel"><h3>Had you never bought them</h3><p class="small mute">Import your tradebook (Trades) to see this.</p></div>`}
        <div class="gk-panel"><h3>Today’s holdings without them <span class="s">now, and over the last year</span></h3>
          <div class="tbl gk-tbl" role="table"><div class="tr th ins-cmp" role="row"><span></span><span class="num">With them</span><span class="num">Without</span><span class="num">Change</span></div>${cmpRows(NOW_ROWS, r.with, r.without_them)}</div>
          ${r.with.year ? html`<p class="gk-note">Nifty 50 over the same year: ${pctOr(r.with.year.nifty, 1)}. Funds have no daily prices from Kite, so the year is shares only${r.left_out_of_year.length ? `; listed within the year, left out: ${r.left_out_of_year.join(', ')}` : ''}.</p>` : ''}</div>
      </div>`}`;
}

// ── What each holding did ──────────────────────────────────────────────

function dragView(d) {
  const rows = d.contributions || [];
  if (!rows.length) return html`${head('What each holding did')}${empty('Nothing to rank yet', d.holdings_error ? 'Kite isn’t connected, so today’s holdings can’t be read.' : 'Import your tradebook (Trades) to rank by return.')}`;
  const mx = Math.max(1, ...rows.map(r => Math.abs(r.xirr_change ?? r.year_change ?? 0)));
  const my = Math.max(1, ...rows.map(r => Math.abs(r.year_change ?? 0)));
  return html`${head('What each holding did', 'How much your return would change without each one. Above zero it carried you; below, it held you back.')}
    <div class="tbl gk-tbl" role="table" data-ins="drag">
      <div class="tr th ins-drag" role="row"><span>Holding</span><span class="num">Weight</span><span>XIRR, everything</span><span class="num">Without it</span><span>Today’s shares, the year</span><span class="num">Without it</span></div>
      ${rows.map(r => html`<div class="tr ins-drag ${r.held ? '' : 'off'}" role="row" data-drag="${r.symbol}">
        <span class="cell-name"><b>${r.symbol}</b><span>${r.kind === 'mf' ? 'fund' : 'share'}${r.held ? '' : ' · sold out'}</span></span>
        <span class="num" data-l="Weight">${r.weight == null ? '—' : `${r.weight.toFixed(1)}%`}</span>
        <span data-l="XIRR effect" data-tip="${r.xirr_change == null ? 'No trades for it' : `${r.symbol}\nYour XIRR ${r.xirr_change >= 0 ? 'is' : 'would be'} ${Math.abs(r.xirr_change).toFixed(1)} points ${r.xirr_change >= 0 ? 'higher with it' : 'higher without it'}`}">${r.xirr_change == null ? html`<span class="mute tiny">—</span>` : html`${centreBar(r.xirr_change, mx)}<b class="mono tiny ${tone(r.xirr_change)}">${r.xirr_change >= 0 ? '+' : '−'}${Math.abs(r.xirr_change).toFixed(1)} pts</b>`}</span>
        <span class="num" data-l="XIRR without">${pctOr(r.xirr_without, 1)}</span>
        <span data-l="Year effect">${r.year_change == null ? html`<span class="mute tiny">—</span>` : html`${centreBar(r.year_change, my)}<b class="mono tiny ${tone(r.year_change)}">${r.year_change >= 0 ? '+' : '−'}${Math.abs(r.year_change).toFixed(1)} pts</b>`}</span>
        <span class="num" data-l="Year without">${pctOr(r.year_without, 1)}</span></div>`)}
    </div>
    <p class="gk-note">XIRR from your imported trades (each holding’s trades taken out, everything else as it was). The year: today’s shares at each day’s close, with and without it. Funds have no daily prices, so they’re only in the XIRR.</p>`;
}

const noTrades = () => html`${empty('No trades imported', 'This needs your Console tradebook. Import it on LinckLens › Trades, then come back.')}<p class="small"><a href="${href('lens/trades')}">Go to Trades →</a></p>`;

const VIEW = { own: ownView, tax: taxView, whatif: whatifView, drag: dragView };

export default {
  title: p => `${(VIEWS.find(v => v[0] === p.view) || VIEWS[0])[1]} · Inspector · LinckLens`,
  render(main, params) {
    const view = VIEWS.find(v => v[0] === params.view) || VIEWS[0];
    const entry = takeEntry();

    function top() {
      const o = st.data && st.data.own;
      return html`<img class="mark gk-mark" src="/icons/linck-mark.svg" alt="" width="24" height="24"><img class="mark gk-mark mark-paper" src="/icons/linck-mark-paper.svg" alt="" width="24" height="24">
        <span class="gk-badge">INSPECTOR</span><a class="gk-name" href="${href('lens')}">LinckLens</a>
        <span class="grow"></span>
        ${o ? html`<span class="gk-kv"><span>Own money</span><b>${inr0(o.own)}</b></span>
          <span class="gk-kv hide-m"><span>At cost</span><b>${inr0(o.cost_now)}</b></span>
          <span class="gk-kv hide-m"><span>Worth</span><b>${inr0(o.value)}</b></span>
          <span class="gk-kv hide-m"><span>Gain on yours</span><b class="${tone(o.gain)}">${pctOr(o.gain_pct, 1)}</b></span>` : ''}
        <button type="button" class="gk-exit" data-act="exit">Exit <kbd>Esc</kbd></button>`;
    }
    const rail = () => html`${GROUPS.map(gr => html`<div class="gk-grp"><h4>${gr}</h4>${VIEWS.filter(v => v[2] === gr).map(([k, l, , key]) => html`
      <a href="${href(`lens/inspect/${k}`)}" class="${k === view[0] ? 'on' : ''}" ${k === view[0] ? html`aria-current="page"` : ''}>${l}<span class="k">${key}</span></a>`)}</div>`)}
      <div class="foot">Keys switch views · Esc leaves<br>Read-only: nothing here trades</div>`;

    function body() {
      if (st.err) return empty(st.err.status === 409 ? 'Kite isn’t connected' : 'Couldn’t read it', st.err.message);
      if (!st.data) return html`<div class="skel" style="height:300px"></div>`;
      return VIEW[view[0]](st.data);
    }
    function paintView() {
      const el = $('#gkView', main);
      if (!el) return;
      mount(el, body());
      $$(':scope > *', el).forEach((c, i) => c.style.setProperty('--i', i));
      const t = $('.gk-top', main);
      if (t) mount(t, top());
    }
    mount(main, html`<div class="gk ins ${entry ? 'gk-in' : ''}" ${entry ? html`style="--ox:${entry.x}px;--oy:${entry.y}px"` : ''}>
      <div class="gk-top">${top()}</div>
      <nav class="gk-rail" aria-label="Inspector views">${rail()}</nav>
      <main class="gk-main"><div class="gk-view" id="gkView"></div></main>
    </div>`);
    if (entry) setTimeout(() => { const s = $('.gk', main); if (s) s.classList.remove('gk-in'); }, 1500);
    paintView();

    async function loadWhatif() {
      const key = [...st.without].sort().join(',');
      if (!key || key === st.wiKey) { paintView(); return; }
      st.wiErr = null; st.wi = null; st.wiKey = key; paintView();
      try {
        const r = await api.lensWhatif(key);
        if (st.wiKey === key) { st.wi = r; paintView(); }
      } catch (e) { if (st.wiKey === key) { st.wiErr = e; paintView(); } }
    }

    if (!st.data) {
      api.lensInspect().then(d => { st.data = d; st.err = null; paintView(); if (view[0] === 'whatif') loadWhatif(); })
        .catch(e => { st.err = e; paintView(); });
    } else if (view[0] === 'whatif') loadWhatif();

    const offActs = actions(main, {
      exit: (x, el) => leave($('.gk', main), el),
      pick: x => { st.without.has(x.v) ? st.without.delete(x.v) : st.without.add(x.v); loadWhatif(); },
      clear: () => { st.without.clear(); st.wi = null; st.wiKey = null; paintView(); },
    });
    const offKeys = on(document, 'keydown', 'body', e => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest('input, textarea, select, [contenteditable]')) return;
      if (e.key === 'Escape') { e.preventDefault(); leave($('.gk', main), $('[data-act="exit"]', main)); return; }
      const hit = VIEWS.find(v => v[3].toLowerCase() === e.key.toLowerCase());
      if (hit && hit[0] !== view[0]) { e.preventDefault(); go(`lens/inspect/${hit[0]}`); }
    });
    return () => { offActs(); offKeys(); };
  },
};
