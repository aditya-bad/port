// Pieces the SuperTrend options views share: the pivot ladder with the
// index and the SuperTrend line on it, a scatter, and number formats.
// The geometry is pure and tested on its own.

import { html } from '../../core/dom.js';
import { inr } from '../../core/format.js';
import { svg, tiles, tone } from './ui.js';

export const EXIT = { st_flip: 'SuperTrend flip', force_exit: 'Square-off' };
export const exitLabel = k => (k == null ? 'Open' : EXIT[k] || k.replace(/_/g, ' '));
const num = (v, d = 0) => Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: d });
export const pts = (v, d = 0) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${num(v, d)}`);
export const lvl = v => (v == null ? '—' : num(v, 2));
export function mins(m) {
  if (m == null) return '—';
  const t = Math.round(m);
  return t < 60 ? `${t}m` : `${Math.floor(t / 60)}h ${String(t % 60).padStart(2, '0')}m`;
}

// Label positions at least `gap` apart, each as close to its own y as it
// can be, in the original order.
export function spread(ys, gap) {
  const order = ys.map((y, i) => [y, i]).sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const [y] of order) out.push(out.length ? Math.max(y, out[out.length - 1] + gap) : y);
  const back = new Array(ys.length);
  order.forEach(([, i], k) => { back[i] = out[k]; });
  return back;
}

// x (0..1) for any price on a ladder covering the levels and the marks.
export function ladderScale(levels, marks = []) {
  const vals = [...levels.map(l => l.value), ...marks.filter(v => v != null)];
  if (!vals.length) return () => 0.5;
  const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo) * 0.06 || 1;
  return v => (v - (lo - pad)) / (hi - lo + 2 * pad);
}

// The pivot levels left to right, the index (▼) and the SuperTrend line
// (dashed): `trigger` is the level an entry waits for.
export function ladder({ ladder: levels, close, line, trigger, trend }) {
  if (!levels || !levels.length) return '';
  const W = 1000, H = 132, X = 20, w = W - 40;
  const fx = ladderScale(levels, [close, line]);
  const x = v => X + fx(v) * w;
  let s = `<line x1="${X}" x2="${X + w}" y1="70" y2="70" stroke="var(--line2)"/>`;
  if (line != null && close != null) {
    const a = Math.min(x(line), x(close)), b = Math.max(x(line), x(close));
    s += `<rect x="${a}" y="58" width="${b - a}" height="24" fill="${(trend === 'up') === (close >= line) ? 'var(--gs)' : 'var(--rs)'}"/>`;
  }
  for (const l of levels) {
    const on = trigger && trigger.key === l.key;
    const c = on ? 'var(--acc)' : l.key === 'P' ? 'var(--ink)' : l.key[0] === 'R' ? 'var(--c5)' : 'var(--c4)';
    s += `<line x1="${x(l.value)}" x2="${x(l.value)}" y1="52" y2="88" stroke="${c}" stroke-width="${on ? 4 : 2}"/>`;
    s += `<text class="lab" x="${x(l.value)}" y="44" text-anchor="middle" style="fill:${c}">${l.key}</text>`;
    s += `<text x="${x(l.value)}" y="104" text-anchor="middle">${Math.round(l.value).toLocaleString('en-IN')}</text>`;
  }
  if (line != null) {
    s += `<line x1="${x(line)}" x2="${x(line)}" y1="20" y2="96" stroke="var(--r)" stroke-width="2" stroke-dasharray="5 4"/>`;
    s += `<text class="lab" x="${x(line)}" y="124" text-anchor="middle" style="fill:var(--r)">ST ${Math.round(line).toLocaleString('en-IN')}</text>`;
  }
  if (close != null) {
    s += `<path d="M${x(close)} 66 l-7 -12 h14z" fill="var(--ink)"/><line x1="${x(close)}" x2="${x(close)}" y1="66" y2="88" stroke="var(--ink)" stroke-width="2"/>`;
    s += `<text class="lab" x="${x(close)}" y="18" text-anchor="middle">index ${Math.round(close).toLocaleString('en-IN')}</text>`;
  }
  return svg(W, H, s, 'Pivot levels, the index and the SuperTrend line');
}

// The SuperTrend panel for the Engine view.
export function stPanel(st) {
  const gap = st.gap == null ? null : st.trend === 'up' ? st.gap : -st.gap;
  return html`<div class="gk-panel" data-st><h3>SuperTrend &amp; pivots <span class="s">${st.source === 'live' ? (st.close_is_live ? 'live' : 'last candle close') : st.source === 'persisted' ? 'as last saved' : 'not available'}${st.bracket ? ` · index ${st.bracket}` : ''}</span></h3>
    ${st.ladder && st.ladder.length ? ladder(st) : html`<p class="gk-note">No pivot levels yet: they come from the previous day’s prices.</p>`}
    ${tiles([
      ['Trend', st.trend ? (st.trend === 'up' ? 'Up' : 'Down') : '—', st.trend === 'up' ? 'sells PEs on R breaks' : st.trend === 'down' ? 'sells CEs on S breaks' : 'warming up', st.trend === 'up' ? 'pos' : st.trend === 'down' ? 'neg' : ''],
      ['SuperTrend line', lvl(st.line), st.atr != null ? `ATR ${num(st.atr, 1)}` : ''],
      ['To a flip', gap == null ? '—' : `${num(gap)} pts`, gap == null ? '' : gap < 0 ? 'through the line' : `${(gap / st.close * 100).toFixed(2)}% of the index`, gap != null && gap < 0 ? 'neg' : ''],
      ['Entry level', st.trigger ? `${st.trigger.key} ${lvl(st.trigger.value)}` : '—', st.trigger && st.close != null ? `${num(Math.abs(st.trigger.value - st.close))} pts from the index` : ''],
      ['Entries today', st.max_trades ? `${st.trades_today ?? 0} / ${st.max_trades}` : String(st.trades_today ?? 0), st.max_trades ? 'daily cap' : 'no cap'],
      ['Square-off', st.force_exit, st.open_leg ? `then ${st.open_leg.symbol} is bought back` : 'nothing open'],
    ], 6)}</div>`;
}

// A scatter: [{x, y, c, tip}] with zero lines; x and y formatted by fx, fy.
export function scatter(points, { xLabel = '', yLabel = '', fx = v => num(v), fy = v => inr(Math.round(v)) } = {}) {
  if (!points.length) return '';
  const W = 600, H = 260, L = 64, R = 12, T = 12, B = 34;
  const xs = points.map(p => p.x), ys = points.map(p => p.y);
  const x0 = Math.min(0, ...xs), x1 = Math.max(0, ...xs), y0 = Math.min(0, ...ys), y1 = Math.max(0, ...ys);
  const x = v => L + (v - x0) / ((x1 - x0) || 1) * (W - L - R), y = v => T + (1 - (v - y0) / ((y1 - y0) || 1)) * (H - T - B);
  let s = `<line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line2)"/><line x1="${x(0)}" x2="${x(0)}" y1="${T}" y2="${H - B}" stroke="var(--line2)"/>`;
  s += `<text x="${L - 6}" y="${y(y1) + 4}" text-anchor="end">${fy(y1)}</text><text x="${L - 6}" y="${y(y0) + 4}" text-anchor="end">${fy(y0)}</text>`;
  s += `<text x="${x(x0)}" y="${H - 18}" text-anchor="start">${fx(x0)}</text><text x="${x(x1)}" y="${H - 18}" text-anchor="end">${fx(x1)}</text>`;
  s += `<text x="${(L + W - R) / 2}" y="${H - 4}" text-anchor="middle">${xLabel}</text>`;
  for (const p of points) s += `<circle cx="${x(p.x).toFixed(1)}" cy="${y(p.y).toFixed(1)}" r="5" fill="${p.c}" fill-opacity=".8"><title>${p.tip || ''}</title></circle>`;
  return svg(W, H, s, `${yLabel} against ${xLabel}`);
}

// One group's line: trades, win rate, total and average.
export const groupRow = (name, g) => [name, String(g.n), g.win_rate == null ? '—' : `${Math.round(g.win_rate)}%`,
  html`<span class="${tone(g.pnl)}">${inr(g.pnl)}</span>`, html`<span class="${tone(g.avg)}">${g.avg == null ? '—' : inr(g.avg)}</span>`];
export const GROUP_COLS = [['', 'minmax(120px,1fr)'], ['Trades', '70px', 'num'], ['Won', '64px', 'num'], ['P&L', '100px', 'num'], ['Avg', '96px', 'num']];
