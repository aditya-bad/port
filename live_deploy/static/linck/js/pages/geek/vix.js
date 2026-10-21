// A "VIX" panel for a page about one day or one stretch (a straddle, a
// session, a trade, a cycle): India VIX through it, at the same marks as
// the page's other charts, and what it did between entry and exit. From
// Kite via /v2/vix (app/vix.py: in memory, never stored). Built once and
// re-attached after each redraw, like the notes panel:
//   const vx = vixPanel({ start, end, interval });  … ${vx.html()} … vx.attach(el, marks)

import { api } from '../../core/api.js';
import { html, mount, $ } from '../../core/dom.js';
import { empty } from '../../ui/components.js';
import { linesChart } from './dtt.js';

const f2 = v => v.toFixed(2);

// What VIX did between two moments: {at, then, change} from [[t ms, v]].
export function vixMove(series, from, to) {
  if (!series.length || from == null) return null;
  const at = t => { let v = null; for (const [ts, x] of series) { if (ts <= t) v = x; else break; } return v; };
  const a = at(new Date(from).getTime()) ?? series[0][1];
  const b = to != null ? at(new Date(to).getTime()) : series[series.length - 1][1];
  return a == null || b == null ? null : { from: a, to: b, change: b - a };
}

export function vixText(m, open) {
  if (!m) return '';
  const word = Math.abs(m.change) < 0.05 ? 'held at' : m.change > 0 ? 'rose to' : 'fell to';
  return `VIX was ${f2(m.from)} at entry and ${word} ${f2(m.to)} ${open ? 'since' : 'by the exit'} (${m.change >= 0 ? '+' : '−'}${f2(Math.abs(m.change))}).`;
}

export function vixPanel({ start, end = null, interval = '5minute', title = 'India VIX', sub = 'through it · the same marks as above' }) {
  const root = document.createElement('div');
  let series = null, error = null, marks = [], alive = true, timer = null;
  const entry = () => (marks[0] ? marks[0].t : null);
  const exit = () => (end && marks.length > 1 ? marks[marks.length - 1].t : null);

  function paint() {
    if (series === null) { mount(root, html`<div class="skel" style="height:120px"></div>`); return; }
    if (!series.length) { mount(root, empty('', error || 'No VIX for this stretch.')); return; }
    const m = vixMove(series, entry(), exit());
    mount(root, html`${linesChart([{ name: 'VIX', data: series, color: 'var(--c5)', width: 2 }], { marks, label: 'India VIX', fmt: f2, h: 150 })}
      ${m ? html`<p class="gk-note" data-vix-move>${vixText(m, !end)}</p>` : ''}`);
  }
  async function load() {
    try {
      const d = await api.vix({ start: new Date(start).toISOString(), end: new Date(end || Date.now()).toISOString(), interval });
      if (!alive) return;
      series = (d.candles || []).map(c => [c.t, c.c]);
      error = d.error || null;
    } catch (e) { if (!alive) return; series = []; error = e.message; }
    paint();
  }
  load();
  if (!end) timer = setInterval(load, 60000);   // still open: VIX moves on
  return {
    html: () => html`<div class="gk-panel"><h3>${title} <span class="s">${sub}</span></h3><div data-vix-slot></div></div>`,
    attach(el, m = []) {
      const slot = $('[data-vix-slot]', el);
      if (JSON.stringify(m) !== JSON.stringify(marks)) { marks = m; paint(); }
      if (slot && root.parentNode !== slot) slot.appendChild(root);
    },
    destroy() { alive = false; if (timer) clearInterval(timer); root.remove(); },
  };
}

// The trading session a moment falls in, 09:15–15:30 IST, for a day's panel.
export function sessionOf(t) {
  const d = new Date(new Date(t).getTime() + 5.5 * 3600e3).toISOString().slice(0, 10);
  return { start: `${d}T09:15:00+05:30`, end: `${d}T15:30:00+05:30` };
}
