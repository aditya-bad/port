// LinckLens › Overview: the whole at a glance (what it's worth, what it
// made, today), the shares against Nifty over the year, what needs a
// look, where it is, and who moved it.

import { html } from '../../core/dom.js';
import { inr, pct, price, plural, tone, dShort, fromDay } from '../../core/format.js';
import { sect, stat, seg } from '../../ui/components.js';
import { line, yOf } from '../../ui/charts.js';
import { registerScrub } from '../../ui/tooltip.js';
import { href } from '../../core/router.js';
import { pctOr, inr0, signed, yearProgress, attentionList, bars, PERIOD_LABEL } from './parts.js';

const CH = 260;
const WINDOWS = ['1M', '3M', '6M', '1Y'];

// The curve, cut to the chosen window and both started at 100 there: on
// the day the server measured that period from, so the legend and the
// returns table agree.
export function windowed(curve, k) {
  const p = curve.periods.find(x => x.k === k);
  let i = p && p.from ? curve.days.indexOf(p.from) : 0;
  if (i < 0) i = 0;
  const p0 = curve.portfolio[i], n0 = curve.nifty[i];
  return {
    days: curve.days.slice(i), value: curve.value.slice(i),
    p: curve.portfolio.slice(i).map(v => v / p0 * 100), n: curve.nifty.slice(i).map(v => v / n0 * 100),
  };
}

export function curveChart(curve, k) {
  const w = windowed(curve, k);
  const all = [...w.p, ...w.n];
  const lo = Math.min(...all), hi = Math.max(...all), pad = (hi - lo) * 0.08 || 1;
  const L = lo - pad, H = hi + pad;
  const pe = w.p[w.p.length - 1] - 100, ne = w.n[w.n.length - 1] - 100;
  registerScrub('lens-curve', f => {
    const i = Math.round(f * (w.days.length - 1));
    return {
      t: `${dShort(fromDay(w.days[i]))}\nYour shares  ${pct(w.p[i] - 100)}  ·  ${inr0(w.value[i])}\nNifty 50  ${pct(w.n[i] - 100)}`,
      ys: [yOf(w.p[i], CH, L, H) / CH, yOf(w.n[i], CH, L, H) / CH], cs: ['var(--ink)', 'var(--b)'],
    };
  });
  return html`<div class="lens-curve">
    <div class="lens-legend small">
      <span><i class="key ink"></i>Your shares <b class="mono ${tone(pe)}">${pct(pe, 1)}</b></span>
      <span><i class="key b"></i>Nifty 50 <b class="mono ${tone(ne)}">${pct(ne, 1)}</b></span>
      <span class="grow"></span>${seg(WINDOWS.filter(x => curve.periods.some(p => p.k === x)).map(x => [x, x]), k, 'window', 'sm')}
    </div>
    <div class="lens-plot">
      ${line({ series: [{ values: w.n, color: 'var(--b)', width: 1.5, label: 'Nifty 50' }, { values: w.p, color: 'var(--ink)', width: 2, label: 'Your shares' }],
        h: CH, key: 'lens-curve', lo: L, hi: H, refs: [100] })}
      <div class="lens-axis tiny mute mono"><span>${dShort(fromDay(w.days[0]))}</span><span>${dShort(fromDay(w.days[w.days.length - 1]))}</span></div>
    </div>
  </div>`;
}

function periodsTable(curve) {
  return html`<div class="lens-periods" role="table" aria-label="Returns by period">
    <div class="th" role="row"><span></span>${curve.periods.map(p => html`<span class="num">${p.k}</span>`)}</div>
    <div role="row"><span>Your shares</span>${curve.periods.map(p => html`<span class="num ${tone(p.portfolio)}">${pctOr(p.portfolio, 1)}</span>`)}</div>
    <div role="row"><span>Nifty 50</span>${curve.periods.map(p => html`<span class="num ${tone(p.nifty)}">${pctOr(p.nifty, 1)}</span>`)}</div>
    <div role="row" class="diff"><span>Ahead of Nifty by</span>${curve.periods.map(p => {
      const d = p.portfolio == null || p.nifty == null ? null : p.portfolio - p.nifty;
      return html`<span class="num ${tone(d)}">${d == null ? '—' : html`${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(1)}<span class="pts"> pts</span>`}</span>`;
    })}</div>
  </div>`;
}

function riskTiles(curve) {
  const dd = curve.drawdown, ndd = curve.nifty_drawdown;
  return html`<div class="lens-risk">
    ${stat('Swings (volatility)', curve.vol == null ? '—' : `${curve.vol.toFixed(1)}%`, 'a year, from daily moves')}
    ${stat('Beta to Nifty', curve.beta == null ? '—' : curve.beta.toFixed(2), curve.beta == null ? '' : curve.beta > 1.05 ? 'moves more than the market' : curve.beta < 0.95 ? 'moves less than the market' : 'moves with the market')}
    ${stat('Worst fall in the year', dd.pct == null ? '—' : html`<span class="neg">${pct(dd.pct, 1)}</span>`,
      dd.pct ? `${dShort(fromDay(dd.from))} → ${dShort(fromDay(dd.to))} · Nifty’s ${pct(ndd.pct, 1)}` : '')}
  </div>`;
}

function headline(curve) {
  const y = curve.periods.find(p => p.k === '1Y') || curve.periods[curve.periods.length - 1];
  if (!y || y.portfolio == null || y.nifty == null) return '';
  const d = y.portfolio - y.nifty;
  return html`<p class="lens-headline">Over ${PERIOD_LABEL[y.k] || y.k}, your shares as they stand today went <b class="${tone(y.portfolio)}">${pct(y.portfolio, 1)}</b>;
    Nifty 50 went <b class="${tone(y.nifty)}">${pct(y.nifty, 1)}</b>. That’s <b class="${tone(d)}">${Math.abs(d).toFixed(1)} points ${d >= 0 ? 'ahead of' : 'behind'}</b> the market.</p>`;
}

function movers(rows, key, n = 5) {
  const ok = rows.filter(r => r[key] != null && r[key] !== 0);
  const up = [...ok].filter(r => r[key] > 0).sort((a, b) => b[key] - a[key]).slice(0, n);
  const down = [...ok].filter(r => r[key] < 0).sort((a, b) => a[key] - b[key]).slice(0, n);
  const mx = Math.max(1, ...ok.map(r => Math.abs(r[key])));
  const list = (xs, side) => (xs.length ? xs.map(r => html`<a class="lens-mv ${side}" href="${href(`lens/shares?open=${encodeURIComponent(r.symbol)}`)}"
      data-tip="${`${r.symbol}${r.name ? ` · ${r.name}` : ''}\n${key === 'day_change' ? 'Today' : 'Since you bought'}  ${inr(r[key])}${key === 'day_change' ? `  (${pctOr(r.day_change_pct)})` : `  (${pctOr(r.pnl_pct)})`}\nValue  ${inr0(r.value)}`}">
      <span class="lens-mv-n"><b>${r.symbol}</b></span><span class="lens-mv-b"><i style="width:${Math.abs(r[key]) / mx * 100}%"></i></span>
      <span class="lens-mv-v mono ${tone(r[key])}">${inr(r[key])}</span><span class="lens-mv-p mono tiny ${tone(r[key])}">${key === 'day_change' ? pctOr(r.day_change_pct) : pctOr(r.pnl_pct, 1)}</span></a>`)
    : html`<p class="tiny mute" style="margin:6px 0">None ${side === 'up' ? 'up' : 'down'}.</p>`);
  return html`<div class="lens-movers"><div class="col">${list(up, 'up')}</div><div class="col">${list(down, 'down')}</div></div>`;
}

export function render(st, ui) {
  const h = st.h, t = h.totals, y = st.y, ready = y && y.state === 'ready';
  const curve = ready ? y.insights.curve : null;
  const attn = ready ? y.insights.attention : h.attention;
  const c = h.concentration;
  return html`
    <div class="strip lens-strip">
      ${stat('Everything you hold', inr0(t.all.value), `${plural(t.equity.count, 'share')} · ${plural(t.mf.count, 'fund')}`)}
      ${stat('Gain on what you put in', signed(t.all.pnl, inr(t.all.pnl)), `${pctOr(t.all.pnl_pct)} on ${inr0(t.all.invested)}`)}
      ${stat('Shares today', t.equity.day_change == null ? '—' : signed(t.equity.day_change, inr(t.equity.day_change)),
        t.equity.day_change == null ? 'no previous close' : `${pctOr(t.equity.day_change_pct)} · ${t.equity.advancing} up, ${t.equity.declining} down`)}
      ${h.sip_monthly ? stat('Going in by SIP', inr0(h.sip_monthly), `a month, ${plural(h.sips.filter(s => s.status === 'ACTIVE').length, 'active SIP')}`)
        : stat('Funds', inr0(t.mf.value), t.mf.count ? `${pctOr(t.mf.pnl_pct)} on ${inr0(t.mf.invested)}` : 'none held')}
    </div>

    ${sect('Your shares against the market', 'Today’s shares at each day’s close, and Nifty 50, both from 100')}
    ${curve ? html`${headline(curve)}${curveChart(curve, ui.window)}
        <p class="tiny mute lens-note">Kite keeps no record of what you held on past days, so this is what <b>today’s</b> shares would have done. Funds aren’t in it: Kite has no NAV history.
          ${curve.left_out.length ? `${plural(curve.left_out.length, 'share')} listed within the year ${curve.left_out.length === 1 ? 'is' : 'are'} left out of the line: ${curve.left_out.join(', ')}.` : ''}
          It covers ${inr0(curve.covered_value)} of your ${inr0(t.equity.value)} in shares.</p>
        ${periodsTable(curve)}${riskTiles(curve)}`
      : ready ? html`<p class="small mute">${y.error || (t.equity.count ? 'Not enough price history to draw it.' : 'No shares held.')}</p>`
      : yearProgress(y, st.yErr)}

    ${sect('Worth a look', ready ? 'From your holdings and their year' : 'From your holdings; more once the year of prices is in')}
    ${attentionList(attn)}

    <div class="lens-two">
      <div class="col" style="gap:12px;min-width:0">
        ${sect('Where it is', 'By what each holding is')}
        ${h.allocation.length ? bars(h.allocation) : html`<p class="small mute">Nothing held.</p>`}
      </div>
      <div class="col" style="gap:12px;min-width:0">
        ${sect('How much rides on a few')}
        ${c.top ? html`<div class="lens-conc">
          ${stat('Largest holding', `${c.top.weight.toFixed(1)}%`, c.top.name)}
          ${stat('Top five', `${c.top5.toFixed(1)}%`, `of ${plural(c.count, 'holding')}`)}
          ${stat('Spread like', `${c.effective}`, 'equal holdings', {})}
        </div>
        <p class="tiny mute lens-note">“Spread like” is how many equal-sized holdings would carry the same concentration (1 ÷ the sum of the squared weights). ${c.effective < 8 ? 'Under 8: a few names decide how it goes.' : 'Well spread.'}</p>` : html`<p class="small mute">Nothing held.</p>`}
      </div>
    </div>

    ${t.equity.count ? html`
      ${sect('Today', t.equity.day_change == null ? '' : `Shares ${inr(t.equity.day_change)} · ${t.equity.advancing} up, ${t.equity.declining} down`)}
      ${movers(h.equity, 'day_change')}
      ${sect('Gainers and losers', 'since you bought')}
      ${movers(h.equity, 'pnl')}` : ''}`;
}
