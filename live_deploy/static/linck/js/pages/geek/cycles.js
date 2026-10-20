// Cycles: one row per checkpoint cycle — how it was born, lived and ended,
// the worst dip and best peak it went through (from the 5-minute price
// marks), its P&L — and how the months compare with the monthly target.
// Charts: every cycle's P&L journey on one axis, each cycle's range and
// result, and what ends them. A row (or a line) opens that cycle end to end.

import { api } from '../../core/api.js';
import { html, mount, on } from '../../core/dom.js';
import { go } from '../../core/router.js';
import { inr, dShort } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, svg, path, gapped, steps, money, tone, LABEL, yLabels, wide } from './ui.js';
import { band, dayTable, hoursOf, smooth } from './paths.js';

const endTag = r => html`<span class="oc ${r.ended_by === 'checkpoint_target' ? '' : r.ended_by === 'open' ? 'no_action' : 'blocked'}">${LABEL[r.ended_by] || r.ended_by.replace(/_/g, ' ')}</span>`;

const END_C = { checkpoint_target: 'var(--ink)', open: 'var(--b)', contract_expiry_backstop: 'var(--wr)', convergence_stop: 'var(--r)' };
const endColour = k => END_C[k] || 'var(--mute)';

// Every cycle's P&L against days since it opened: do they climb steadily
// to the checkpoint, or dip first? The open one is blue. With `one`, the
// cycle page's own copy: that cycle alone, at full weight, not a link.
export function journeys(rows, target, one = false) {
  const R = rows.filter(r => r.path && r.path.length > 1).slice(0, 24);
  if (!R.length) return empty('', 'Appears once a cycle has prices or has booked a leg.');
  const W = 1200, H = 280, all = R.flatMap(r => r.path);
  const dx = Math.max(1, ...all.map(p => p[0] / 24)), hi = Math.max(target, ...all.map(p => p[1])), lo = Math.min(0, ...all.map(p => p[1]));
  const pad = (hi - lo || 1) * 0.06, top = hi + pad, bot = lo - pad;
  const x = d => 64 + d / dx * (W - 140), y = v => 12 + (top - v) / (top - bot) * (H - 46);
  let s = '';
  for (let d = 0; d <= dx; d += Math.max(1, Math.round(dx / 10))) s += `<line x1="${x(d)}" x2="${x(d)}" y1="12" y2="${H - 34}" stroke="var(--line)"/><text x="${x(d)}" y="${H - 18}" text-anchor="middle">day ${d}</text>`;
  s += yLabels([0, target, bot, top], y, 58);
  s += `<line x1="64" x2="${W - 76}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)"/>`;
  s += `<line x1="64" x2="${W - 76}" y1="${y(target)}" y2="${y(target)}" stroke="var(--g)" stroke-dasharray="5 3"/><text class="lab" x="${W - 76}" y="${y(target) - 5}" text-anchor="end" style="fill:var(--g)">checkpoint</text>`;
  [...R].reverse().forEach(r => {
    const open = r.status === 'open', c = endColour(r.ended_by);
    const pts = r.path.map(p => [x(p[0] / 24), y(p[1])]), L = pts[pts.length - 1];
    const w = open || one ? 2.4 : 1.3, op = open || one ? 1 : 0.55;
    s += one ? `<g class="jr-one" data-kind="${r.path_kind || 'marks'}">` : `<g class="jr" data-c="${r.cycle_id}" data-kind="${r.path_kind || 'marks'}">`;
    if (r.path_kind === 'booked') {
      s += `<path d="${path(steps(pts))}" fill="none" stroke="${c}" stroke-width="${w}" stroke-dasharray="6 4" opacity="${op}"/>`;
    } else {
      const g = gapped(pts, r.path_gaps);
      s += `<path d="${g.solid}" fill="none" stroke="${c}" stroke-width="${w}" opacity="${op}"/>`;
      if (g.gaps) s += `<path class="gap" d="${g.gaps}" fill="none" stroke="${c}" stroke-width="${w}" stroke-dasharray="1 4" stroke-linecap="round" opacity="${op}"/>`;
    }
    s += `<circle cx="${L[0]}" cy="${L[1]}" r="${open || one ? 4.5 : 3}" fill="${c}"/><text x="${L[0] + 6}" y="${L[1] + 3}" style="fill:${c}">#${r.cycle_id}</text></g>`;
  });
  const ends = [['checkpoint_target', 'reached the checkpoint'], ['contract_expiry_backstop', 'expiry'], ['convergence_stop', 'convergence stop'], ['open', 'running']]
    .filter(([k]) => !one || R[0].ended_by === k);
  return html`${wide(svg(W, H, s, one ? `Cycle ${R[0].cycle_id}’s P&L against days since it opened` : 'Every cycle’s P&L against days since it opened'))}
    <div class="row wrap tiny" style="gap:16px">${ends
      .map(([k, l]) => html`<span class="row" style="gap:6px"><i style="width:14px;height:3px;background:${endColour(k)}"></i>${l}</span>`)}
      ${R.some(r => r.path_kind === 'booked') ? html`<span class="row" style="gap:6px"><i style="width:14px;border-top:2px dashed var(--ink)"></i>no prices stored: steps at what each leg booked</span>` : ''}
      ${R.some(r => r.path_gaps && r.path_gaps.length) ? html`<span class="row" style="gap:6px"><i style="width:14px;border-top:2px dotted var(--ink)"></i>market open, no prices (Kite offline)</span>` : ''}</div>`;
}

// Spotlight: the same journeys, calmed. Past cycles faint and smoothed to
// one point every 2 hours, the typical (median) past cycle bold, the
// running one blue; hovering a line lights it up and names it below.
const SPOT = [['all', 'All'], ['last5', 'Last 5'], ['checkpoint', 'Reached checkpoint'], ['stopped', 'Stopped / expired']];
export function spotPast(rows, filter) {
  const past = rows.filter(r => r.status !== 'open' && r.path && r.path.length > 1);   // newest first
  if (filter === 'last5') return past.slice(0, 5);
  if (filter === 'checkpoint') return past.filter(r => r.ended_by === 'checkpoint_target');
  if (filter === 'stopped') return past.filter(r => r.ended_by !== 'checkpoint_target');
  return past;
}
export const spotText = r => `#${r.cycle_id} · ${(LABEL[r.ended_by] || r.ended_by.replace(/_/g, ' ')).toLowerCase()} · ${inr(r.realized_pnl)} after ${(hoursOf(r) / 24).toFixed(1)} days · ${r.rolls} rolls · click to open`;

function spotlight(rows, target, filter) {
  const past = spotPast(rows, filter), run = rows.find(r => r.status === 'open' && r.path && r.path.length > 1);
  const R = [...past, ...(run ? [run] : [])];
  const chips = html`<div class="sp-chips" role="group" aria-label="Which past cycles">${SPOT.map(([k, l]) => html`<button type="button" data-spot="${k}" class="${k === filter ? 'on' : ''}" aria-pressed="${k === filter}">${l}</button>`)}</div>`;
  if (!R.length) return html`${chips}${empty('', 'No cycles match this filter.')}`;
  const W = 1200, H = 280, all = R.flatMap(r => r.path.map(p => p[1]));
  const dx = Math.max(1, ...R.map(r => hoursOf(r) / 24)), hi = Math.max(target, ...all), lo = Math.min(0, ...all);
  const pad = (hi - lo || 1) * 0.06, top = hi + pad, bot = lo - pad;
  const x = d => 64 + d / dx * (W - 150), y = v => 12 + (top - v) / (top - bot) * (H - 46);
  const pts = r => smooth(r.path).map(p => [x(p[0] / 24), y(p[1])]);
  let s = '';
  for (let d = 0; d <= dx; d += Math.max(1, Math.round(dx / 10))) s += `<line x1="${x(d)}" x2="${x(d)}" y1="12" y2="${H - 34}" stroke="var(--line)"/><text x="${x(d)}" y="${H - 18}" text-anchor="middle">day ${d}</text>`;
  s += yLabels([0, target, bot, top], y, 58);
  s += `<line x1="64" x2="${W - 86}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)"/>`;
  s += `<line x1="64" x2="${W - 86}" y1="${y(target)}" y2="${y(target)}" stroke="var(--g)" stroke-dasharray="5 3"/><text class="lab" x="${W - 86}" y="${y(target) - 5}" text-anchor="end" style="fill:var(--g)">checkpoint</text>`;
  past.forEach(r => {
    const d = path(r.path_kind === 'booked' ? steps(pts(r)) : pts(r));
    s += `<g class="sp" data-c="${r.cycle_id}" data-info="${spotText(r).replace(/"/g, '&quot;')}" style="--c:${endColour(r.ended_by)}"><path class="v" d="${d}"/><path class="hit" d="${d}"/></g>`;
  });
  const typ = band(past);
  if (typ.length > 1) {
    const tp = typ.map(b => [x(b.h / 24), y(b.med)]), L = tp[tp.length - 1];
    s += `<path class="sp-typ" d="${path(tp)}" fill="none" stroke="var(--ink)" stroke-width="2.4"/><text class="lab" x="${L[0] + 6}" y="${L[1] + 4}" style="fill:var(--ink)">typical</text>`;
  }
  if (run) {
    const rp = pts(run), L = rp[rp.length - 1];
    s += `<g class="sp-run" data-c="${run.cycle_id}"><path d="${path(rp)}" fill="none" stroke="var(--b)" stroke-width="2.8"/><circle cx="${L[0]}" cy="${L[1]}" r="4.5" fill="var(--b)"/><text class="lab" x="${L[0] + 7}" y="${L[1] + 4}" style="fill:var(--b)">#${run.cycle_id} now</text></g>`;
  }
  return html`${chips}${wide(svg(W, H, s, 'Past cycles faint, the typical cycle bold, the running cycle blue'))}
    <div class="row wrap tiny" style="gap:16px"><span class="row" style="gap:6px"><i style="width:14px;height:3px;background:var(--ink);opacity:.25"></i>past cycles (${past.length})</span>
      <span class="row" style="gap:6px"><i style="width:14px;height:3px;background:var(--ink)"></i>typical: the median past cycle, while at least 3 were still open</span>
      ${run ? html`<span class="row" style="gap:6px"><i style="width:14px;height:3px;background:var(--b)"></i>running</span>` : ''}</div>
    <p class="sp-info small" aria-live="polite">Hover a line to see which cycle it is; click it to open that cycle.</p>`;
}

// Is the running cycle normal? Where past cycles were, day by day (middle
// half dark, best to worst light, the median as a line), and the running
// cycle drawn over it.
function normalBand(rows, target) {
  const B = band(rows), run = rows.find(r => r.status === 'open' && r.path && r.path.length > 1);
  if (B.length < 2) return empty('', 'Appears once at least 3 cycles have closed.');
  const all = [...B.flatMap(b => [b.lo, b.hi]), ...(run ? run.path.map(p => p[1]) : [])];
  const W = 1200, H = 280, dx = Math.max(1, B[B.length - 1].h / 24, run ? hoursOf(run) / 24 : 0);
  const hi = Math.max(target, ...all), lo = Math.min(0, ...all), pad = (hi - lo || 1) * 0.06, top = hi + pad, bot = lo - pad;
  const x = d => 64 + d / dx * (W - 170), y = v => 12 + (top - v) / (top - bot) * (H - 46);
  const area = (a, b) => `${path([...B.map(z => [x(z.h / 24), y(z[a])]), ...[...B].reverse().map(z => [x(z.h / 24), y(z[b])])])}Z`;
  let s = '';
  for (let d = 0; d <= dx; d += Math.max(1, Math.round(dx / 10))) s += `<line x1="${x(d)}" x2="${x(d)}" y1="12" y2="${H - 34}" stroke="var(--line)"/><text x="${x(d)}" y="${H - 18}" text-anchor="middle">day ${d}</text>`;
  s += `<path class="nb-range" d="${area('hi', 'lo')}" fill="var(--ink)" opacity=".07"/><path class="nb-mid" d="${area('q3', 'q1')}" fill="var(--ink)" opacity=".15"/>`;
  s += yLabels([0, target, bot, top], y, 58);
  s += `<line x1="64" x2="${W - 106}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)"/>`;
  s += `<line x1="64" x2="${W - 106}" y1="${y(target)}" y2="${y(target)}" stroke="var(--g)" stroke-dasharray="5 3"/>`;
  s += `<path d="${path(B.map(z => [x(z.h / 24), y(z.med)]))}" fill="none" stroke="var(--ink)" stroke-width="2"/>`;
  const E = B[B.length - 1], ex = x(E.h / 24) + 6;
  s += `<text class="lab" x="${ex}" y="${y(E.med) + 4}" style="fill:var(--ink)">typical</text>`;
  if (Math.abs(y(E.hi) - y(E.med)) > 14) s += `<text x="${ex}" y="${y(E.hi) + 4}">best past</text>`;
  if (Math.abs(y(E.lo) - y(E.med)) > 14) s += `<text x="${ex}" y="${y(E.lo) + 4}">worst past</text>`;
  if (run) {
    const rp = smooth(run.path).map(p => [x(p[0] / 24), y(p[1])]), L = rp[rp.length - 1];
    s += `<g class="nb-run"><path d="${path(rp)}" fill="none" stroke="var(--b)" stroke-width="2.8"/><circle cx="${L[0]}" cy="${L[1]}" r="4.5" fill="var(--b)"/><text class="lab" x="${L[0] + 7}" y="${L[1] - 8}" style="fill:var(--b)">#${run.cycle_id} now ${inr(run.pnl ?? L[1])}</text></g>`;
  }
  return html`${wide(svg(W, H, s, 'Where past cycles were each day, and the running cycle'))}
    <div class="row wrap tiny" style="gap:16px"><span class="row" style="gap:6px"><i style="width:14px;height:10px;background:var(--ink);opacity:.2"></i>middle half of past cycles</span>
      <span class="row" style="gap:6px"><i style="width:14px;height:10px;background:var(--ink);opacity:.08"></i>best to worst</span>
      <span class="row" style="gap:6px"><i style="width:14px;height:3px;background:var(--ink)"></i>typical</span>
      ${run ? html`<span class="row" style="gap:6px"><i style="width:14px;height:3px;background:var(--b)"></i>running</span>` : ''}</div>
    <p class="gk-note">The band ends where fewer than 3 past cycles were still open: cycles that reach the checkpoint early drop out, so later days rest on fewer cycles.</p>`;
}

// The consolidated table under both: past cycles day by day, and the
// running cycle against them.
function dayRows(rows) {
  const T = dayTable(rows);
  if (!T.length) return '';
  const run = rows.some(r => r.status === 'open' && r.path && r.path.length > 1);
  const PLACE = { 'above every past cycle': 'pos', 'top quarter': 'pos', 'middle half': '', 'bottom quarter': 'neg', 'below every past cycle': 'neg' };
  return table([['Day', '128px'], ['Past cycles open', '120px', 'num'], ['Typical', '100px', 'num'], ['Middle half', 'minmax(150px,1fr)', 'num'],
    ['Best / worst', 'minmax(150px,1fr)', 'num'], ['Reached checkpoint by then', '170px', 'num'], ...(run ? [['Running cycle', '110px', 'num'], ['Where it stands', '170px']] : [])],
    T.map(r => [html`<b class="${r.now ? 'mono' : ''}">${r.label}</b>`, `${r.open} of ${r.closed}`,
      r.med == null ? html`<span class="mute">too few</span>` : html`<span class="${tone(r.med)}">${inr(r.med)}</span>`,
      r.q1 == null ? '—' : `${inr(r.q1)} to ${inr(r.q3)}`,
      r.hi == null ? '—' : html`<span class="pos">${inr(r.hi)}</span> / <span class="${tone(r.lo)}">${inr(r.lo)}</span>`,
      r.closed ? `${r.reached} · ${Math.round(r.reached / r.closed * 100)}%` : '—',
      ...(run ? [r.run == null ? '—' : html`<b class="${tone(r.run)}">${inr(r.run)}</b>`, r.place ? html`<span class="${PLACE[r.place]}">${r.place}</span>` : '—'] : [])]),
    { minw: run ? 1100 : 820, cls: 'day-tbl', rowCls: i => (T[i].now ? 'att' : '') });
}

// Each cycle as a range from its worst dip to its best peak, and where it
// ended: a long red tail under a green result means it was rescued.
function ranges(rows, target) {
  const R = [...rows].reverse().slice(-30);
  if (!R.length) return empty('', 'Appears after the first cycle.');
  const W = 1200, H = 230, vals = R.flatMap(r => [r.mae ?? 0, r.mfe ?? 0, r.pnl ?? 0]);
  const hi = Math.max(target, ...vals), lo = Math.min(0, ...vals), pad = (hi - lo || 1) * 0.06, top = hi + pad, bot = lo - pad;
  const bw = Math.min(34, (W - 90) / R.length - 8), x = i => 70 + i * ((W - 90) / R.length), y = v => 10 + (top - v) / (top - bot) * (H - 40);
  let s = `<line x1="64" x2="${W - 10}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)"/>`;
  s += `<line x1="64" x2="${W - 10}" y1="${y(target)}" y2="${y(target)}" stroke="var(--g)" stroke-dasharray="5 3"/>`;
  s += yLabels([0, target, bot, top], y, 58);
  R.forEach((r, i) => {
    const cx = x(i) + bw / 2, p = r.pnl ?? 0;
    if (r.mae != null && r.mfe != null) {
      s += `<rect x="${cx - 2}" y="${y(Math.max(0, r.mfe))}" width="4" height="${y(0) - y(Math.max(0, r.mfe))}" fill="var(--gs)"/>`;
      s += `<rect x="${cx - 2}" y="${y(0)}" width="4" height="${y(Math.min(0, r.mae)) - y(0)}" fill="var(--rs)"/>`;
    }
    s += `<g class="jr" data-c="${r.cycle_id}"><rect x="${x(i)}" y="${Math.min(y(0), y(p))}" width="${bw}" height="${Math.max(1.5, Math.abs(y(p) - y(0)))}" fill="${r.status === 'open' ? 'var(--b)' : p >= 0 ? 'var(--pos)' : 'var(--r)'}" opacity="${r.status === 'open' ? 0.7 : 1}"/>`;
    s += `<text x="${cx}" y="${H - 14}" text-anchor="middle">#${r.cycle_id}</text></g>`;
  });
  return wide(svg(W, H, s, 'Each cycle’s worst dip, best peak and result'));
}

// What ends cycles: one bar, split by how each closed cycle ended.
function endings(s) {
  const E = Object.entries(s.ended_by).sort((a, b) => b[1] - a[1]), n = E.reduce((a, [, k]) => a + k, 0);
  if (!n) return '';
  return html`<div class="col" style="gap:8px">
    <div style="display:flex;height:22px;border:1px solid var(--ink)">${E.map(([k, c]) => html`<i style="width:${c / n * 100}%;background:${endColour(k)};opacity:${k === 'checkpoint_target' ? 1 : 0.8}" title="${LABEL[k] || k}: ${c}"></i>`)}</div>
    <div class="row wrap tiny" style="gap:16px">${E.map(([k, c]) => html`<span class="row" style="gap:6px"><i style="width:10px;height:10px;background:${endColour(k)}"></i>${LABEL[k] || k.replace(/_/g, ' ')} <b>${c}</b> · ${Math.round(c / n * 100)}%</span>`)}</div>
  </div>`;
}

function daysChart(rows) {
  const d = rows.filter(r => r.ended_by === 'checkpoint_target').map(r => r.days);
  if (!d.length) return empty('', 'Appears once a cycle reaches its checkpoint.');
  const W = 520, mx = Math.max(6, Math.ceil(Math.max(...d) + 1)), x = v => 30 + v / mx * (W - 50);
  let s = `<line x1="30" x2="${W - 20}" y1="120" y2="120" stroke="var(--ink)"/>`;
  for (let v = 0; v <= mx; v += Math.max(1, Math.round(mx / 6))) s += `<text x="${x(v)}" y="136" text-anchor="middle">${v}d</text>`;
  const stack = {};
  d.forEach(v => { const b = Math.round(v); stack[b] = (stack[b] || 0) + 1; s += `<rect x="${x(b) - 8}" y="${120 - stack[b] * 20}" width="16" height="16" fill="var(--ink)"/>`; });
  const med = [...d].sort((a, b) => a - b)[Math.floor(d.length / 2)];
  s += `<line x1="${x(med)}" x2="${x(med)}" y1="16" y2="120" stroke="var(--g)" stroke-dasharray="4 3"/><text class="lab" x="${x(med) + 6}" y="26" style="fill:var(--g)">median ${med}d</text>`;
  return svg(W, 144, s, 'Days each cycle took to reach its checkpoint');
}

function monthChart(m) {
  if (!m.months.length) return empty('', 'Appears after the first closed trade.');
  const W = 520, top = Math.max(m.target * 1.1, ...m.months.map(x => x.pnl_net)), low = Math.min(0, ...m.months.map(x => x.pnl_net));
  const y = v => 14 + (top - v) / ((top - low) || 1) * 106, bw = Math.min(90, (W - 60) / m.months.length - 16);
  let s = `<line x1="30" x2="${W - 10}" y1="${y(m.target)}" y2="${y(m.target)}" stroke="var(--g)" stroke-dasharray="5 3"/><text class="lab" x="${W - 10}" y="${y(m.target) - 5}" text-anchor="end" style="fill:var(--g)">target ${inr(m.target, { sign: false })}</text>`;
  s += `<line x1="30" x2="${W - 10}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)"/>`;
  m.months.forEach((x, i) => {
    const cx = 50 + i * (bw + 16), v = x.pnl_net;
    s += `<rect x="${cx}" y="${Math.min(y(0), y(v))}" width="${bw}" height="${Math.abs(y(v) - y(0))}" fill="${v < 0 ? 'var(--r)' : 'var(--ink)'}"/>`;
    s += `<text class="lab" x="${cx + bw / 2}" y="${Math.min(y(0), y(v)) - 5}" text-anchor="middle">${inr(v)}</text>`;
    const [yy, mm] = x.month.split('-');
    s += `<text x="${cx + bw / 2}" y="138" text-anchor="middle">${new Date(+yy, +mm - 1, 1).toLocaleString('en-IN', { month: 'short' })} · ${Math.round(v / m.target * 100)}%</text>`;
  });
  return svg(W, 146, s, 'Net P&L per month against the monthly target');
}

export default {
  render(el, v, g) {
    let data = null, spotFilter = 'all';
    function paint() {
      if (data === null) { mount(el, html`${head('Cycles')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Cycles')}${empty('Couldn’t load cycles', data.message)}`); return; }
      const s = data.summary;
      if (!data.rows.length) { mount(el, html`${head('Cycles')}${empty('No cycles yet', 'A cycle starts at the first entry.')}`); return; }
      const ck = s.ended_by.checkpoint_target || 0;
      const spot = spotFilter;
      const others = Object.entries(s.ended_by).filter(([k]) => k !== 'checkpoint_target').map(([k, n]) => `${(LABEL[k] || k).toLowerCase()} ${n}`).join(' · ');
      mount(el, html`
        ${head('Cycles', 'How each cycle was born, lived and ended: is the checkpoint doing the work, or are expiries and stops?', 'fills · 5-minute price marks')}
        ${tiles([
          ['Cycles', s.cycles, `${s.closed} closed · ${s.open} open`],
          ['Ended by checkpoint', s.closed ? `${ck} of ${s.closed}` : '—', others || (s.closed ? 'every closed cycle' : '')],
          ['Median days to checkpoint', s.median_days_to_checkpoint ?? '—', s.fastest_checkpoint_days != null ? `fastest ${s.fastest_checkpoint_days} · slowest ${s.slowest_checkpoint_days}` : ''],
          ['Avg worst dip / best peak', s.avg_mae != null ? html`<span class="neg">${inr(s.avg_mae)}</span> / <span class="pos">${inr(s.avg_mfe)}</span>` : '—', s.worst_mae != null ? `worst dip ${inr(s.worst_mae)}` : 'from price marks'],
        ])}
        <div class="gk-panel"><h3>Every cycle’s journey <span class="s">P&L against days since it opened · click a line or a row to open that cycle</span></h3>${journeys(data.rows, data.checkpoint_target)}</div>
        <div class="gk-panel" id="spot"><h3>Spotlight <span class="s">the same journeys, calmer: past cycles faint and smoothed · bold black: the typical cycle · blue: running · hover a line to light it up</span></h3>${spotlight(data.rows, data.checkpoint_target, spot)}</div>
        <div class="gk-panel"><h3>Is this cycle normal? <span class="s">the running cycle against where past cycles were on the same day</span></h3>${normalBand(data.rows, data.checkpoint_target)}</div>
        <div class="gk-panel"><h3>Day by day <span class="s">past cycles’ P&L at the end of each day since opening, and the running cycle against them</span></h3>${dayRows(data.rows)}</div>
        <div class="gk-panel"><h3>Range and result <span class="s">shaded: worst dip and best peak · bar: where it ended · dashed: checkpoint</span></h3>${ranges(data.rows, data.checkpoint_target)}</div>
        <div class="gk-panel"><h3>What ends cycles <span class="s">${s.closed} closed</span></h3>${endings(s)}</div>
        ${table([['#', '44px'], ['Opened → closed', 'minmax(150px,1.2fr)'], ['Expiry', '96px'], ['Days', '56px', 'num'], ['Ended by', '130px'],
          ['Rolls', '60px', 'num'], ['EOD · no-op', '90px', 'num'], ['Worst dip', '96px', 'num'], ['Best peak', '96px', 'num'], ['P&L', '110px', 'num']],
          data.rows.map(r => [html`<a class="mono b" href="${g.href(`cycle?c=${r.cycle_id}`)}">${r.cycle_id}</a>`, `${dShort(r.start)} → ${r.end ? dShort(r.end) : 'now'}`, r.contract_expiry || '—', r.days,
            endTag(r), r.rolls, `${r.rolls_by_kind.eod + r.rolls_by_kind.rollout} · ${r.eod_checks_no_action}`,
            html`<span class="${tone(r.mae)}">${money(r.mae)}</span>`, html`<span class="${tone(r.mfe)}">${money(r.mfe)}</span>`,
            html`<span class="${tone(r.pnl)} b">${money(r.pnl)}</span>${r.status === 'open' ? html` <span class="tiny mute">open</span>` : ''}`]),
          { minw: 960, rowCls: i => `click ${data.rows[i].ended_by === 'convergence_stop' ? 'att' : ''}`, rowAttrs: i => html`data-c="${data.rows[i].cycle_id}" role="link" tabindex="0"` })}
        <p class="gk-note">P&L is gross (before charges) for closed cycles and the live cycle P&L for the open one. Worst dip and best peak come from the 5-minute price marks (15-minute candle closes for cycles older than the marks), so a spike between two prices isn’t seen; a cycle with no stored prices shows none.</p>
        <div class="gk-two even">
          <div class="gk-panel"><h3>Days to checkpoint <span class="s">each cycle that reached it</span></h3>${daysChart(data.rows)}</div>
          <div class="gk-panel"><h3>Monthly target <span class="s">net P&L per month · informational</span></h3>${monthChart(data.monthly)}</div>
        </div>`);
    }
    const stop = poll(() => api.geek(g.id, 'cycles'), 60000, d => { data = d; paint(); });
    const open = (e, t) => { if (!e.target.closest('a')) go(`d/${g.id}/geek/cycle?c=${t.dataset.c}`); };
    const info = el => el.querySelector('.sp-info');
    const offs = [on(el, 'click', '[data-c]', open), on(el, 'keydown', '.tr[data-c]', (e, t) => { if (e.key === 'Enter') open(e, t); }),
      on(el, 'click', '[data-spot]', (e, t) => { spotFilter = t.dataset.spot; paint(); }),
      // the lit line goes on top of the rest; a class, as moving it drops :hover
      on(el, 'mouseover', '.sp', (e, t) => {
        if (t.classList.contains('lit')) return;
        el.querySelectorAll('.sp.lit').forEach(x => x.classList.remove('lit'));
        t.classList.add('lit');
        if (t.nextElementSibling) t.parentNode.appendChild(t);
        const i = info(el); if (i) { i.textContent = t.dataset.info; i.classList.add('on'); }
      }),
      on(el, 'mouseout', '.sp', (e, t) => { if (!t.contains(e.relatedTarget)) t.classList.remove('lit'); })];
    return () => { stop(); offs.forEach(f => f()); };
  },
};
