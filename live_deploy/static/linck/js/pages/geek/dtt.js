// Pieces the DTT straddle views share: the straddle against its lines
// (each leg's stop, the combined target), a chart of several premium
// lines, and names.

import { html } from '../../core/dom.js';
import { price, hhmm } from '../../core/format.js';
import { svg, path, tiles, wide, hit } from './ui.js';
import { spread } from './st.js';

export const EXIT = { profit_target_decay: 'Profit target', leg_spike_stop: 'Leg stop', force_exit: 'Square-off' };
export const exitName = k => (k == null ? 'Open' : EXIT[k] || k.replace(/_/g, ' '));
export const EXIT_COLOR = { profit_target_decay: 'var(--g)', leg_spike_stop: 'var(--r)', force_exit: 'var(--b)' };

// Where `now` sits from `from` to `to` (0..1), for a leg heading to its
// stop or the combined premium heading to its target.
export const progress = (from, to, now) => (from == null || to == null || now == null || from === to ? 0
  : Math.max(0, Math.min(1, (now - from) / (to - from))));

function bar(label, from, to, now, color, l, r) {
  const f = progress(from, to, now);
  return html`<div class="dd-bar" data-tip="${`${label}\nSold  ${price(from)}\nNow  ${now == null ? 'no price' : price(now)}\n${r.startsWith('target') ? 'Target' : 'Stop'}  ${price(to)}\nOf the way there  ${Math.round(f * 100)}%`}">
    <div class="row" style="gap:8px;align-items:baseline"><b class="small">${label}</b><span class="grow"></span><span class="mono small">${now == null ? 'no price' : price(now)}</span></div>
    <div style="position:relative;height:14px;background:var(--soft);margin:6px 0 4px"><i style="position:absolute;left:0;top:0;bottom:0;width:${(f * 100).toFixed(1)}%;background:${color}"></i></div>
    <div class="row mono tiny mute"><span>${l}</span><span class="grow"></span><span>${r}</span></div></div>`;
}

// The Engine view's straddle panel.
export function ddPanel(dd) {
  const st = dd.state;
  if (!st) {
    const last = dd.last;
    return html`<div class="gk-panel" data-dd><h3>The straddle <span class="s">nothing open</span></h3>
      <p class="small">${dd.entered_today ? 'Today’s straddle is done.' : `Sells the ATM CE and PE at ${dd.entry_time}.`}
      Targets ${Math.round(dd.profit_pct * 100)}% decay of the combined premium; either leg up ${Math.round(dd.stop_pct * 100)}% stops both; squares off at ${dd.force_exit}.</p>
      ${last ? html`<p class="gk-note">Last: ${last.day} · ${last.strike ? last.strike.toLocaleString('en-IN') : ''} straddle · ${exitName(last.reason)}${last.pnl != null ? ` · ₹${Math.round(last.pnl).toLocaleString('en-IN')}` : ''}</p>` : ''}</div>`;
  }
  const c = st.combined, ce = st.legs.CE, pe = st.legs.PE;
  return html`<div class="gk-panel" data-dd><h3>The straddle <span class="s">each leg against its stop · the combined premium against the target</span></h3>
    <div class="dd-bars">
      ${ce ? bar(`CE ${ce.symbol}`, ce.entry, ce.stop, ce.now, 'var(--r)', `sold ${price(ce.entry)}`, `stop ${price(ce.stop)}`) : ''}
      ${pe ? bar(`PE ${pe.symbol}`, pe.entry, pe.stop, pe.now, 'var(--r)', `sold ${price(pe.entry)}`, `stop ${price(pe.stop)}`) : ''}
      ${c ? bar('Combined', c.entry, c.target, c.now, 'var(--g)', `sold ${price(c.entry)}`, `target ${price(c.target)}`) : html`<p class="gk-note">One leg only: the combined target waits for both.</p>`}
    </div>
    ${c ? tiles([
      ['Combined now', c.now == null ? '—' : price(c.now), c.decay_pct == null ? '' : `${c.decay_pct > 0 ? 'down' : 'up'} ${Math.abs(c.decay_pct)}% from ${price(c.entry)}`, c.decay_pct > 0 ? 'pos' : c.decay_pct < 0 ? 'neg' : ''],
      ['To the target', c.to_target == null ? '—' : price(c.to_target), `target ${price(c.target)}`],
      ['CE to its stop', ce && ce.to_stop != null ? price(ce.to_stop) : '—', ce ? `stop ${price(ce.stop)}` : ''],
      ['PE to its stop', pe && pe.to_stop != null ? price(pe.to_stop) : '—', pe ? `stop ${price(pe.stop)}` : ''],
    ], 4) : ''}</div>`;
}

// Several lines over time with horizontal levels and marks.
// lines: [{data: [[iso, v]], color, width}]; levels: [{key, value, color, dash}].
export function linesChart(lines, { levels = [], marks = [], label = '', fmt = v => price(v), h = 240 } = {}) {
  const all = lines.flatMap(l => l.data);
  if (all.length < 2) return '';
  const W = 1200, H = h, L = 70, R = 80, T = 14, B = 26;
  const ts = all.map(p => new Date(p[0]).getTime()), vs = all.map(p => p[1]);
  const lo0 = Math.min(...vs), hi0 = Math.max(...vs), span = hi0 - lo0 || hi0 * 0.01 || 1;
  const near = levels.filter(l => l.value != null && l.value >= lo0 - span * 0.8 && l.value <= hi0 + span * 0.8);
  const lo = Math.min(lo0, ...near.map(l => l.value)) - span * 0.06, hi = Math.max(hi0, ...near.map(l => l.value)) + span * 0.06;
  const t0 = Math.min(...ts), t1 = Math.max(...ts);
  const x = t => L + (t - t0) / ((t1 - t0) || 1) * (W - L - R), y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  let s = '';
  for (const l of near) s += `<line x1="${L}" x2="${W - R}" y1="${y(l.value)}" y2="${y(l.value)}" stroke="${l.color}" ${l.dash ? 'stroke-dasharray="6 4"' : ''}/>`;
  const ly = spread(near.map(l => y(l.value)), 13);
  near.forEach((l, i) => { s += `<text class="lab" x="${W - R + 6}" y="${ly[i] + 4}" style="fill:${l.color}">${l.key}</text>`; });
  s += `<text x="${L - 8}" y="${y(hi0) + 4}" text-anchor="end">${fmt(hi0)}</text><text x="${L - 8}" y="${y(lo0) + 4}" text-anchor="end">${fmt(lo0)}</text>`;
  for (const m of marks) {
    const mt = new Date(m.t).getTime();
    if (mt < t0 || mt > t1) continue;
    s += `<line x1="${x(mt)}" x2="${x(mt)}" y1="${T}" y2="${H - B}" stroke="${m.color}" stroke-width="2"/><text class="lab" x="${x(mt) + 5}" y="${T + 10}" style="fill:${m.color}">${m.label}</text>`;
    s += hit(x(mt) - 6, T, 12, H - T - B, `${m.label}  ${hhmm(new Date(m.t))}`);
  }
  for (const l of lines) {
    if (l.data.length < 2) continue;
    s += `<path d="${path(l.data.map(p => [x(new Date(p[0]).getTime()), y(p[1])]))}" fill="none" stroke="${l.color}" stroke-width="${l.width || 1.8}"/>`;
  }
  s += `<text x="${L}" y="${H - 6}">${hhmm(new Date(t0))} ${new Date(t0).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' })}</text>`;
  s += `<text x="${W - R}" y="${H - 6}" text-anchor="end">${hhmm(new Date(t1))}</text>`;
  const at = t => `${new Date(t).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' })} ${hhmm(new Date(t))}`;
  return wide(svg(W, H, s, label, {
    series: lines.filter(l => l.data.length).map(l => ({ name: l.name || 'Value', color: l.color, pts: l.data.map(p => [x(new Date(p[0]).getTime()), y(p[1]), fmt(p[1]), at(p[0])]) })),
    extra: () => near.map(l => `${l.key}  ${fmt(l.value)}`),
  }));
}
