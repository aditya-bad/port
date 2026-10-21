// Volatility (every strategy): how India VIX bears on this deployment
// (app/geek/volatility.py) — VIX now and what it implies, the results by
// VIX at entry and by what VIX did during each trade, ₹ per VIX point,
// days the market moved more or less than VIX priced, and week by week.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr, dStamp, dDay } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, svg, tone, tipAt, wide } from './ui.js';
import { bucketBars } from './conditions.js';

const pt = v => (v == null ? '—' : v.toFixed(2));
const sgn = v => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(2)}`);
const SIDE = { sold: 'sells premium (net): VIX rising works against it', bought: 'buys premium (net): VIX rising works for it', both: 'sells and buys premium about equally' };

// "−₹2,000 per point" in words, with how sure.
export function perPointText(pp) {
  if (!pp) return null;
  const strength = Math.abs(pp.r) >= 0.6 ? 'a strong link' : Math.abs(pp.r) >= 0.3 ? 'a loose link' : 'little link';
  return `${pp.slope < 0 ? 'Loses' : 'Makes'} about ${inr(Math.abs(pp.slope), { sign: false })} a day for every point VIX rises (${strength}, over ${pp.n} days)`;
}

// Dots: rows → x, y; a dashed best-fit line when `fit` = {slope, intercept}.
export function scatter(rows, { x, y, tip, label, fit = null, xfmt = sgn }) {
  const pts = rows.filter(r => r[x] != null && r[y] != null);
  if (pts.length < 2) return '';
  const W = 560, H = 300, L = 70, R = 12, T = 12, B = 30;
  const xs = pts.map(r => r[x]), ys = pts.map(r => r[y]);
  const x0 = Math.min(0, ...xs), x1 = Math.max(0, ...xs), y0 = Math.min(0, ...ys), y1 = Math.max(0, ...ys);
  const sx = v => L + (v - x0) / ((x1 - x0) || 1) * (W - L - R), sy = v => T + (y1 - v) / ((y1 - y0) || 1) * (H - T - B);
  let s = `<line x1="${L}" x2="${W - R}" y1="${sy(0)}" y2="${sy(0)}" stroke="var(--line2)"/><line x1="${sx(0)}" x2="${sx(0)}" y1="${T}" y2="${H - B}" stroke="var(--line2)"/>`;
  if (fit) s += `<line x1="${sx(x0)}" y1="${sy(fit.intercept + fit.slope * x0)}" x2="${sx(x1)}" y2="${sy(fit.intercept + fit.slope * x1)}" stroke="var(--ink)" stroke-dasharray="5 4"/>`;
  s += `<text x="${L - 6}" y="${sy(y1) + 4}" text-anchor="end">${inr(Math.round(y1))}</text><text x="${L - 6}" y="${sy(y0)}" text-anchor="end">${inr(Math.round(y0))}</text>`;
  s += `<text x="${sx(x0)}" y="${H - 8}">${xfmt(x0)}</text><text x="${sx(x1)}" y="${H - 8}" text-anchor="end">${xfmt(x1)}</text>`;
  for (const r of pts) s += `<circle cx="${sx(r[x]).toFixed(1)}" cy="${sy(r[y]).toFixed(1)}" r="5" fill="${r[y] >= 0 ? 'var(--g)' : 'var(--r)'}" fill-opacity=".75"${tipAt(tip(r))}/>`;
  return svg(W, H, s, label);
}

// VIX day by day: a line with dates, and a guide showing each day's close.
export function dailyLine(days) {
  const pts = days.filter(r => r.vix_close != null);
  if (pts.length < 2) return '';
  const W = 1200, H = 200, L = 60, R = 16, T = 12, B = 26;
  const vs = pts.map(r => r.vix_close), lo = Math.min(...vs) - 0.5, hi = Math.max(...vs) + 0.5;
  const x = i => L + i / (pts.length - 1) * (W - L - R), y = v => T + (hi - v) / (hi - lo) * (H - T - B);
  const line = pts.map((r, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(r.vix_close).toFixed(1)}`).join('');
  let s = `<path d="${line}" fill="none" stroke="var(--c5)" stroke-width="2"/>`;
  s += `<text x="${L - 6}" y="${y(hi - 0.5) + 4}" text-anchor="end">${(hi - 0.5).toFixed(1)}</text><text x="${L - 6}" y="${y(lo + 0.5) + 4}" text-anchor="end">${(lo + 0.5).toFixed(1)}</text>`;
  s += `<text x="${L}" y="${H - 6}">${dDay(pts[0].day)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${dDay(pts[pts.length - 1].day)}</text>`;
  const series = [{ name: 'VIX', color: 'var(--c5)', pts: pts.map((r, i) => [x(i), y(r.vix_close), `${pt(r.vix_close)} (${sgn(r.dvix)})`, dDay(r.day)]) }];
  return svg(W, H, s, 'India VIX by day', { series, title: (X, p) => (p ? p[3] : '') });
}

// "At this VIX level, its trades…": the line that tells you what today looks like.
export function hereText(h, vix) {
  if (!h || vix == null) return null;
  if (!h.n) return `It has no closed trades with VIX between ${pt(h.low)} and ${pt(h.high)}: today is new ground for it.`;
  return `With VIX between ${pt(h.low)} and ${pt(h.high)}, as now, its ${h.n} trade${h.n === 1 ? '' : 's'} made ${inr(h.pnl)}: ${inr(h.avg)} on average, ${Math.round(h.win_rate)}% won.`;
}

// The one filter that would have helped most: skipping the side of a
// level whose trades lost money in total, or none.
export function bestFilter(fs) {
  let best = null;
  for (const f of fs) {
    if (f.below_pnl < 0 && (!best || -f.below_pnl > best.gain)) best = { gain: -f.below_pnl, skip: 'below', level: f.level, n: f.below_n };
    if (f.above_pnl < 0 && (!best || -f.above_pnl > best.gain)) best = { gain: -f.above_pnl, skip: 'above', level: f.level, n: f.above_n };
  }
  if (!fs.length) return null;
  if (!best) return 'No VIX level would have helped: its trades made money on both sides of every level.';
  return `Skipping its ${best.n} trade${best.n === 1 ? '' : 's'} with VIX ${best.skip === 'below' ? `below ${best.level}` : `at ${best.level} or above`} would have added ${inr(best.gain, { sign: false })}: they lost that much together.`;
}

// Rich against cheap: the richest third of its premium against the cheapest.
export function richText(groups) {
  const g = groups.find(x => x.key === 'rich');
  if (!g || g.buckets.length < 2) return null;
  const lo = g.buckets[0], hi = g.buckets[g.buckets.length - 1];
  return `Trades whose premium was richest against VIX (${hi.label.split(' · ')[1]} points) averaged ${inr(hi.avg)}, ${Math.round(hi.win_rate)}% won; the cheapest (${lo.label.split(' · ')[1]}) averaged ${inr(lo.avg)}, ${Math.round(lo.win_rate)}% won.`;
}

const PART = [['time', 'Time decay', 'var(--b)'], ['move', 'The index’s move', 'var(--c4)'], ['vol', 'Volatility', 'var(--c5)']];
const splitLines = a => PART.map(([k, l]) => `${l}  ${inr(a[k])}`);

// Where the P&L came from, in words.
export function attrText(a) {
  if (!a) return null;
  const [t, m, v] = [a.time, a.move, a.vol];
  return `Across ${a.n} trade${a.n === 1 ? '' : 's'} it could split, time decay ${t >= 0 ? 'earned' : 'cost'} ${inr(Math.abs(t), { sign: false })}, the index’s moves ${m >= 0 ? 'earned' : 'cost'} ${inr(Math.abs(m), { sign: false })} and changes in volatility ${v >= 0 ? 'earned' : 'cost'} ${inr(Math.abs(v), { sign: false })}.`;
}

// The three parts' totals, as bars either side of zero, each with its share.
export function partRows(a) {
  const mx = Math.max(1, ...PART.map(([k]) => Math.abs(a[k])));
  return html`<div class="col" style="gap:6px">${PART.map(([k, l, c]) => html`
    <div class="vp-row" data-tip="${`${l}\nAcross ${a.n} trades  ${inr(a[k])}\nAll three  ${inr(a.total)}`}">
      <b><i class="vp-sw" style="background:${c}"></i>${l}</b>
      <span class="bar"><i style="${a[k] >= 0 ? 'left:50%' : 'right:50%'};width:${(Math.abs(a[k]) / mx * 50).toFixed(1)}%;background:${c}"></i><u></u></span>
      <span class="mono right ${tone(a[k])}">${inr(a[k])}</span></div>`)}</div>`;
}

// The last trades, each a column of its three parts (up: made, down: cost).
export function splitBars(rows) {
  const rs = rows.filter(r => r.attr).slice(0, 40).reverse();
  if (!rs.length) return '';
  const W = 1200, H = 240, L = 70, R = 12, T = 12, B = 22;
  const up = r => PART.reduce((a, [k]) => a + Math.max(0, r.attr[k]), 0), dn = r => PART.reduce((a, [k]) => a + Math.min(0, r.attr[k]), 0);
  const hi = Math.max(1, ...rs.map(up)), lo = Math.min(-1, ...rs.map(dn));
  const y = v => T + (hi - v) / (hi - lo) * (H - T - B), bw = (W - L - R) / rs.length;
  let s = `<line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line2)"/>`;
  s += `<text x="${L - 6}" y="${y(hi) + 4}" text-anchor="end">${inr(Math.round(hi))}</text><text x="${L - 6}" y="${y(lo)}" text-anchor="end">${inr(Math.round(lo))}</text>`;
  rs.forEach((r, i) => {
    const x = L + i * bw + bw * 0.15, w = bw * 0.7;
    let pos = 0, neg = 0;
    for (const [k, , c] of PART) {
      const v = r.attr[k];
      if (v >= 0) { s += `<rect x="${x.toFixed(1)}" y="${y(pos + v).toFixed(1)}" width="${w.toFixed(1)}" height="${(y(pos) - y(pos + v)).toFixed(1)}" fill="${c}"/>`; pos += v; }
      else { s += `<rect x="${x.toFixed(1)}" y="${y(neg).toFixed(1)}" width="${w.toFixed(1)}" height="${(y(neg + v) - y(neg)).toFixed(1)}" fill="${c}"/>`; neg += v; }
    }
    s += `<line x1="${x.toFixed(1)}" x2="${(x + w).toFixed(1)}" y1="${y(r.attr.total).toFixed(1)}" y2="${y(r.attr.total).toFixed(1)}" stroke="var(--ink)" stroke-width="2"/>`;
    s += `<rect class="hit" x="${(L + i * bw).toFixed(1)}" y="${T}" width="${bw.toFixed(1)}" height="${H - T - B}" fill="transparent"${tipAt([`${r.label} · ${dStamp(r.t)}`, `P&L  ${inr(r.attr.total)}`, ...splitLines(r.attr)].join('\n'))}/>`;
  });
  return svg(W, H, s, 'Each trade’s P&L split into time decay, the index’s move and volatility');
}

// What-if entry filter: one row per VIX level, P&L had it traded only at
// or above it (left) or only below it (right), each bar with its numbers.
export function filterRows(fs, total) {
  if (!fs.length) return '';
  const mx = Math.max(1, ...fs.flatMap(f => [Math.abs(f.above_pnl), Math.abs(f.below_pnl)]));
  const bar = v => html`<span class="bar"><i style="${v >= 0 ? 'left:50%' : 'right:50%'};width:${(Math.abs(v) / mx * 50).toFixed(1)}%;background:${v >= 0 ? 'var(--g)' : 'var(--r)'}"></i><u></u></span>`;
  return html`<div class="col" style="gap:4px">
    <div class="vf-row th"><span>VIX</span><span>Only at or above it</span><span></span><span>Only below it</span><span></span></div>
    ${fs.map(f => html`<div class="vf-row" data-tip="${`If it had only traded with VIX at ${f.level} or above\n  ${f.above_n} trades, ${inr(f.above_pnl)}, ${Math.round(f.above_win)}% won\nOnly below ${f.level}\n  ${f.below_n} trades, ${inr(f.below_pnl)}, ${Math.round(f.below_win)}% won\nEvery trade  ${inr(total)}`}">
      <b class="mono">${f.level}</b>${bar(f.above_pnl)}<span class="mono tiny ${tone(f.above_pnl)}">${inr(f.above_pnl)} · ${f.above_n}</span>${bar(f.below_pnl)}<span class="mono tiny ${tone(f.below_pnl)}">${inr(f.below_pnl)} · ${f.below_n}</span></div>`)}
  </div>`;
}

const tradeTip = r => [`${r.label} · ${dStamp(r.t)}`, `Result  ${r.pnl == null ? 'open' : inr(r.pnl)}`, `VIX at entry  ${pt(r.vix_in)}`,
  ...(r.iv != null ? [`Implied volatility traded  ${pt(r.iv)}`, `Against VIX  ${sgn(r.iv_spread)} points`] : []),
  ...(r.attr ? splitLines(r.attr) : []),
  `VIX at exit  ${pt(r.vix_out)}`, `Change  ${sgn(r.dvix)}`, `${r.side === 'sold' ? 'Sold' : r.side === 'bought' ? 'Bought' : 'Sold and bought'} premium`, r.symbols.join(', ')].join('\n');
const dayTip = r => [dDay(r.day), `P&L  ${inr(r.pnl)}`, `VIX  ${pt(r.vix_close)} (${sgn(r.dvix)})`,
  ...(r.implied_pct != null ? [`VIX priced  ±${r.implied_pct.toFixed(2)}%`] : []), ...(r.move_pct != null ? [`Index moved  ${r.move_pct.toFixed(2)}%`] : []),
  ...(r.ratio != null ? [`${r.ratio.toFixed(2)}× what was priced`] : [])].join('\n');

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Volatility')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Volatility')}${empty('Couldn’t load the volatility view', data.message)}`); return; }
      const d = data, n = d.now, pp = d.per_point;
      if (!d.has_vix) {
        mount(el, html`${head('Volatility')}${empty('No VIX yet', d.error || 'India VIX comes from Kite’s history when this view opens. Log in to Kite and it fills in.')}`);
        return;
      }
      const closed = d.rows.filter(r => r.pnl != null);
      mount(el, html`
        ${head('Volatility', 'How India VIX, the market’s priced volatility, bears on this deployment: VIX at entry, what it did during each trade, and days the market moved more or less than it priced.', d.side ? `this deployment ${SIDE[d.side]}` : '')}
        ${d.proxy ? html`<p class="gk-note">India VIX is built from Nifty options. This deployment trades another index, so VIX here is a stand-in for its volatility, usually a close one.</p>` : ''}
        ${tiles([
          ['VIX now', pt(n.vix), n.percentile != null ? `higher than ${n.percentile}% of the past year (${pt(n.year_low)}–${pt(n.year_high)})` : ''],
          ['Priced move today', n.expected_move_pct != null ? `±${n.expected_move_pct.toFixed(2)}%` : '—', 'one standard deviation, VIX ÷ √365'],
          ['₹ per VIX point', pp ? inr(pp.slope) : '—', pp ? perPointText(pp) : 'needs 10 days of P&L and VIX', pp ? tone(pp.slope) : ''],
          ['Moved more than priced', d.summary.days_compared ? `${d.summary.days_moved_more} of ${d.summary.days_compared}` : '—', 'days the index closed further than VIX priced'],
          ['Trades with VIX', `${d.summary.with_vix} of ${d.summary.trades}`, 'VIX at entry and exit known'],
        ], 5)}
        ${d.here || d.spikes ? html`<div class="gk-panel vf-now"><h3>What it says now</h3>
          <ul class="col" style="gap:6px;margin:0;padding-left:18px">
            ${hereText(d.here, n.vix) ? html`<li>${hereText(d.here, n.vix)}</li>` : ''}
            ${d.spikes ? html`<li>On the ${d.spikes.spike_n} day${d.spikes.spike_n === 1 ? '' : 's'} VIX jumped 10% or more, it averaged <b class="${tone(d.spikes.spike_avg)}">${inr(d.spikes.spike_avg)}</b> (worst ${inr(d.spikes.spike_worst)}), against <b class="${tone(d.spikes.rest_avg)}">${inr(d.spikes.rest_avg)}</b> on other days.</li>` : ''}
            ${pp ? html`<li>${perPointText(pp)}.</li>` : ''}
            ${attrText(d.attribution) ? html`<li>${attrText(d.attribution)}</li>` : ''}
            ${richText(d.groups) ? html`<li>${richText(d.groups)}</li>` : ''}
            ${bestFilter(d.filters) ? html`<li>${bestFilter(d.filters)} <span class="mute small">In hindsight.</span></li>` : ''}
          </ul></div>` : ''}
        ${d.filters.length ? html`<div class="gk-panel"><h3>If it had only traded at some VIX levels <span class="s">closed trades, in hindsight · hover a row</span></h3>
          ${filterRows(d.filters, closed.reduce((a, r) => a + r.pnl, 0))}
          <p class="gk-note">Hindsight on ${closed.length} trades: a level that would have paid here can be luck. Worth a look when one side is clearly better across several levels in a row, and only with plenty of trades on both sides.</p></div>` : ''}
        <div class="gk-panel"><h3>India VIX <span class="s">each day’s close · hover for the day</span></h3>
          ${dailyLine(d.days) ? wide(dailyLine(d.days)) : empty('', 'Needs two days of VIX.')}</div>
        <div class="gk-two even">
          <div class="gk-panel"><h3>VIX change during a trade against its result <span class="s">hover a dot</span></h3>
            ${scatter(closed, { x: 'dvix', y: 'pnl', tip: tradeTip, label: 'VIX change during each trade against its result' }) || empty('', 'Needs closed trades with VIX at entry and exit.')}
            <p class="gk-note">${d.side === 'bought' ? 'A buyer gains as VIX rises: dots up and to the right.' : 'A seller loses as VIX rises: dots down and to the right.'}</p></div>
          <div class="gk-panel"><h3>Each day’s P&amp;L against VIX’s change <span class="s">dashed: ₹ per VIX point</span></h3>
            ${scatter(d.days, { x: 'dvix', y: 'pnl', tip: dayTip, label: 'Daily P&L against VIX change', fit: pp }) || empty('', 'Needs daily equity snapshots and VIX.')}
            ${pp ? html`<p class="gk-note">${perPointText(pp)}. ${d.pnl_source === 'equity' ? 'P&L here is the change in end-of-day equity, so open positions count.' : 'P&L here is what was booked each day (no end-of-day equity recorded yet), so open positions don’t count.'}</p>` : ''}</div>
        </div>
        ${d.attribution ? html`<div class="gk-panel"><h3>Where the P&amp;L came from <span class="s">each option repriced one change at a time · the parts add up to the P&amp;L</span></h3>
          ${partRows(d.attribution)}
          ${wide(splitBars(d.rows))}
          <p class="gk-note">The last ${Math.min(40, d.attribution.n)} trades: blue time decay, teal the index’s move, magenta volatility; the black bar is the result. ${d.attribution.n < d.attribution.closed ? `Split for ${d.attribution.n} of ${d.attribution.closed} closed trades (${inr(d.attribution.total)} of ${inr(d.attribution.pnl_all)}): the rest lack a known strike, expiry or index level.` : `Every closed trade is split (${inr(d.attribution.total)}).`} Time decay: the clock alone, at the entry’s implied volatility. The index’s move: then the index moved. Volatility: the rest, the option repricing as implied volatility changed. The index’s move and volatility often pull opposite ways in the same trade (a fall lifts implied volatility), so read them together.</p></div>` : ''}
        ${closed.some(r => r.iv_spread != null) ? html`<div class="gk-panel"><h3>Premium against VIX, and the result <span class="s">implied volatility traded less VIX at entry, in points → P&amp;L · hover a dot</span></h3>
          ${scatter(closed, { x: 'iv_spread', y: 'pnl', tip: tradeTip, label: 'Implied volatility less VIX against the result' })}
          <p class="gk-note">Implied volatility is worked out from each leg’s price, strike, the index and days to expiry (Black-Scholes). Right of zero: the premium was richer than VIX; a seller wants to sell rich. ${d.proxy ? 'For a non-Nifty index its own volatility runs above VIX: compare trades with each other, not with zero.' : ''}</p></div>` : ''}
        ${d.groups.length ? html`<div class="cd-grid">${d.groups.map(gr => html`<div class="gk-panel"><h3>${gr.label} <span class="s">average trade · ${gr.covered} trades</span></h3>${bucketBars(gr)}</div>`)}</div>` : ''}
        <div class="gk-two even">
          <div class="gk-panel"><h3>The market’s move against what VIX priced <span class="s">× priced → day’s P&amp;L</span></h3>
            ${scatter(d.days, { x: 'ratio', y: 'pnl', tip: dayTip, label: 'Move against priced move, and the day’s P&L', xfmt: x => `${x.toFixed(1)}×` }) || empty('', 'Needs the index’s daily closes.')}</div>
          <div class="gk-panel"><h3>Days by how far the market moved <span class="s">against what VIX priced the evening before</span></h3>
            ${d.moved.length ? bucketBars({ label: 'Move against priced', buckets: d.moved }) : empty('', 'Needs the index’s daily closes and P&L.')}
            <p class="gk-note">VIX prices a one-standard-deviation move: about two days in three should stay inside it. A seller earns on the quiet days and gives back on the big ones.</p></div>
        </div>
        <div class="gk-panel"><h3>Week by week</h3>
          ${d.weeks.length ? table([['Week of', '110px'], ['VIX open', '80px', 'num'], ['VIX close', '80px', 'num'], ['Change', '74px', 'num'], ['High', '70px', 'num'], ['Index', '80px', 'num'], ['Trades', '64px', 'num'], ['P&L', '100px', 'num']],
            d.weeks.slice().reverse().map(w => [html`<span class="mono tiny">${dDay(w.week)}</span>`, pt(w.vix_open), pt(w.vix_close),
              html`<span class="${w.dvix > 0 ? 'neg' : w.dvix < 0 ? 'pos' : ''}">${sgn(w.dvix)}</span>`, pt(w.vix_high),
              w.index_move_pct == null ? '—' : `${w.index_move_pct > 0 ? '+' : ''}${w.index_move_pct.toFixed(2)}%`, String(w.trades),
              html`<span class="${tone(w.pnl)}">${w.pnl == null ? '—' : inr(w.pnl)}</span>`]),
            { minw: 700, rowAttrs: i => { const w = d.weeks[d.weeks.length - 1 - i]; return html`data-tip="${`Week of ${dDay(w.week)}\nVIX  ${pt(w.vix_open)} → ${pt(w.vix_close)} (${sgn(w.dvix)}), high ${pt(w.vix_high)}\nIndex  ${w.index_move_pct == null ? '—' : `${w.index_move_pct.toFixed(2)}%`}\nTrades closed  ${w.trades}${w.trades ? ` (${w.wins} won)` : ''}\nP&L  ${w.pnl == null ? '—' : inr(w.pnl)}`}"`; } })
            : empty('', 'Needs VIX and daily equity.')}
          <p class="gk-note">Change: VIX red when it rose (bad for a seller). P&amp;L: change in end-of-day equity over the week.</p></div>`);
    }
    return poll(() => api.geek(g.id, 'volatility'), 120000, x => { data = x; paint(); });
  },
};
