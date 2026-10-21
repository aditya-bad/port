// Exits & timing (DTT): when each straddle ended and why, and how far
// the combined premium had fallen by then.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, poll, svg, tone, wide, tipAt } from './ui.js';
import { mins } from './st.js';
import { exitName, EXIT_COLOR } from './dtt.js';
import { straddleTable, straddleTip } from './straddles.js';

// Each straddle as a dot: the time it ended (x) against how far the
// combined premium had fallen (y), coloured by what ended it.
function endings(rows) {
  const pts = rows.filter(r => r.exit_t && r.decay_pct != null);
  if (!pts.length) return '';
  const W = 1200, H = 280, L = 60, R = 16, T = 14, B = 30;
  const minOf = r => +r.exit_t.slice(11, 13) * 60 + +r.exit_t.slice(14, 16);
  const t0 = Math.min(600, ...pts.map(minOf)), t1 = Math.max(900, ...pts.map(minOf));
  const ds = pts.map(r => r.decay_pct), lo = Math.min(-10, ...ds), hi = Math.max(15, ...ds);
  const x = m => L + (m - t0) / (t1 - t0) * (W - L - R), y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  let s = `<line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)"/>`;
  for (let m = Math.ceil(t0 / 60) * 60; m <= t1; m += 60) s += `<line x1="${x(m)}" x2="${x(m)}" y1="${T}" y2="${H - B}" stroke="var(--line)"/><text x="${x(m)}" y="${H - 8}" text-anchor="middle">${String(m / 60).padStart(2, '0')}:00</text>`;
  s += `<text x="${L - 8}" y="${y(hi) + 4}" text-anchor="end">−${Math.round(hi)}%</text><text x="${L - 8}" y="${y(0) + 4}" text-anchor="end">0</text><text x="${L - 8}" y="${y(lo) + 4}" text-anchor="end">+${Math.round(-lo)}%</text>`;
  for (const r of pts) s += `<circle cx="${x(minOf(r)).toFixed(1)}" cy="${y(r.decay_pct).toFixed(1)}" r="6" fill="${EXIT_COLOR[r.reason] || 'var(--mute)'}" fill-opacity=".8"${tipAt(straddleTip(r))}/>`;
  return wide(svg(W, H, s, 'When each straddle ended against how far the combined premium fell'));
}

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Exits & timing')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Exits & timing')}${empty('Couldn’t load the exits', data.message)}`); return; }
      const closed = data.straddles.filter(r => !r.open), ex = data.by_exit;
      const keys = Object.keys(ex).sort((a, b) => ex[b].n - ex[a].n);
      const hours = data.by_exit_hour, mx = Math.max(1, ...hours.map(h => h.n));
      mount(el, html`
        ${head('Exits & timing', 'When each straddle ended and why: the profit target, a leg’s stop or the square-off, and how far the combined premium had fallen by then.', `${closed.length} closed`)}
        ${closed.length ? html`
        ${tiles(keys.map(k => [ex[k].label, String(ex[k].n), `${Math.round(ex[k].win_rate)}% won · ${inr(ex[k].pnl)} · usually ${ex[k].median_exit || '—'} · held ${mins(ex[k].median_held)}`, tone(ex[k].pnl)]), keys.length)}
        <div class="gk-panel"><h3>When it ended, and how far the premium had fallen <span class="s">
          ${keys.map(k => html`<b style="color:${EXIT_COLOR[k] || 'var(--mute)'}">● ${ex[k].label}</b> `)} · hover a dot</span></h3>${endings(closed)}</div>
        <div class="gk-panel"><h3>Hour it ended <span class="s">straddles · P&amp;L</span></h3>
          <div class="col" style="gap:8px">${hours.map(h => html`
            <div style="display:grid;grid-template-columns:70px minmax(0,1fr) 140px;gap:10px;align-items:center" class="small" data-tip="${`Ended ${String(h.hour).padStart(2, '0')}:00–${String(h.hour + 1).padStart(2, '0')}:00\nStraddles  ${h.n}\nWon  ${h.wins} (${Math.round(h.win_rate)}%)\nP&L  ${inr(h.pnl)}\nHeld (median)  ${mins(h.median_held)}`}">
              <b>${String(h.hour).padStart(2, '0')}:00</b>
              <div style="height:16px;background:var(--soft)"><i style="display:block;height:100%;width:${(h.n / mx * 100).toFixed(1)}%;background:var(--ink)"></i></div>
              <span class="mono tiny right">${h.n} · <span class="${tone(h.pnl)}">${inr(h.pnl)}</span></span></div>`)}</div></div>
        <div class="gk-panel"><h3>Every closed straddle</h3>${straddleTable(closed, g)}</div>`
        : empty('No closed straddles yet', 'Exits appear once a straddle has been bought back.')}`);
    }
    return poll(() => api.geek(g.id, 'straddles'), 60000, d => { data = d; paint(); });
  },
};
