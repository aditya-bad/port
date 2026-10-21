// Sessions (DTT adjusted / advanced): every day it traded, the straddle,
// how many legs it added, unwound or rolled, what ended the day, and
// whether the extra legs earned their keep.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr, price } from '../../core/format.js';
import { empty, loading, fixTag } from '../../ui/components.js';
import { head, tiles, table, poll, bars, tone, svg, hit, yLabels, wide } from './ui.js';
import { mins } from './st.js';
import { exitName, sessionTip, dayName } from './adj.js';

const GCOLS = [['', 'minmax(110px,1fr)'], ['Days', '60px', 'num'], ['Won', '56px', 'num'], ['P&L', '96px', 'num'], ['Avg', '90px', 'num']];
const grow = (name, g) => [name, String(g.n), `${Math.round(g.win_rate)}%`, html`<span class="${tone(g.pnl)}">${inr(g.pnl)}</span>`, html`<span class="${tone(g.avg)}">${inr(g.avg)}</span>`];
const gtip = (title, g) => `${title}\nDays  ${g.n}\nWon  ${g.wins} (${Math.round(g.win_rate)}%)\nP&L  ${inr(g.pnl)}\nAverage  ${inr(g.avg)}`;

// Each day's P&L split into the original straddle and the extra legs.
function split(rows) {
  const R = rows.filter(r => !r.open).slice(0, 40).reverse();
  if (!R.length) return '';
  const W = 1200, H = 240, vals = R.flatMap(r => [r.pnl_original || 0, r.pnl_extra || 0, r.pnl || 0, (r.pnl_original || 0) + Math.min(0, r.pnl_extra || 0), Math.max(0, r.pnl_original || 0) + Math.max(0, r.pnl_extra || 0)]);
  const hi = Math.max(1, ...vals), lo = Math.min(0, ...vals), pad = (hi - lo) * 0.08;
  const y = v => 12 + (hi + pad - v) / (hi - lo + 2 * pad) * (H - 44), bw = Math.min(26, (W - 90) / R.length - 6), x = i => 76 + i * ((W - 90) / R.length);
  let s = `<line x1="70" x2="${W - 10}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)"/>` + yLabels([0, lo, hi], y, 64);
  R.forEach((r, i) => {
    const a = r.pnl_original || 0, e = r.pnl_extra || 0, X = x(i);
    s += `<rect x="${X}" y="${Math.min(y(0), y(a))}" width="${bw}" height="${Math.max(1, Math.abs(y(a) - y(0)))}" fill="${a >= 0 ? 'var(--ink)' : 'var(--r)'}"/>`;
    if (e) s += `<rect x="${X + bw * 0.25}" y="${Math.min(y(0), y(e))}" width="${bw * 0.5}" height="${Math.max(1, Math.abs(y(e) - y(0)))}" fill="${e >= 0 ? 'var(--g)' : 'var(--wr)'}" opacity=".9"/>`;
    s += `<circle cx="${X + bw / 2}" cy="${y(r.pnl)}" r="3" fill="var(--b)"/>`;
    if (i % Math.ceil(R.length / 12) === 0) s += `<text x="${X + bw / 2}" y="${H - 8}" text-anchor="middle">${r.day.slice(5)}</text>`;
    s += hit(X - 3, 6, bw + 6, H - 30, sessionTip(r));
  });
  return wide(svg(W, H, s, 'Each day: the original straddle’s P&L, the extra legs’, and the day’s'));
}

export function sessionTable(rows, g) {
  return table([['Day', '150px'], ['Strike', '76px', 'num'], ['Sold', '80px', 'num'], ['Moves', '120px'], ['Ended by', '130px'],
    ['Held', '70px', 'num'], ['Original', '90px', 'num'], ['Extra legs', '90px', 'num'], ['P&L', '90px', 'num']],
  rows.map(r => [html`<a href="${g.href('session')}?d=${r.day}">${dayName(r.day)}</a> ${fixTag(r.repaired)}`, r.strike ? r.strike.toLocaleString('en-IN') : '—',
    r.combined_entry == null ? '—' : price(r.combined_entry),
    html`<span class="small">${r.adjustments ? `${r.adjustments} adj` : ''}${r.rolls ? ` ${r.rolls} roll` : ''}${r.unwinds ? ` ${r.unwinds} unwind` : ''}${!r.adjustments && !r.rolls && !r.unwinds ? 'none' : ''}${r.adjusted_side ? html` <span class="mute">(${r.adjusted_side})</span>` : ''}</span>`,
    r.open ? html`<span class="oc acted">Open</span>` : exitName(r.reason), mins(r.held_min),
    html`<span class="${tone(r.pnl_original)}">${r.pnl_original == null ? '—' : inr(r.pnl_original)}</span>`,
    html`<span class="${tone(r.pnl_extra)}">${r.pnl_extra == null ? '—' : inr(r.pnl_extra)}</span>`,
    html`<span class="${tone(r.pnl)}">${r.pnl == null ? '—' : inr(r.pnl)}</span>`]), { minw: 1000, rowAttrs: i => html`data-tip="${sessionTip(rows[i])}"` });
}

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Sessions')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Sessions')}${empty('Couldn’t load the sessions', data.message)}`); return; }
      const s = data.summary;
      mount(el, html`
        ${head('Sessions', `Every day it traded: the straddle, the legs it added${s.rolling ? ', rolled' : ''} or unwound, what ended the day, and whether the extra legs earned their keep.`, `${s.sessions} days`)}
        ${s.sessions ? html`
        ${tiles([
          ['Days', String(s.sessions), s.open ? `${s.open} open` : `${s.days_adjusted} adjusted`],
          ['Won', s.win_rate == null ? '—' : `${Math.round(s.win_rate)}%`, `${s.wins} of ${s.closed}`],
          ['P&L', inr(s.pnl), s.avg == null ? '' : `avg ${inr(s.avg)}`, tone(s.pnl)],
          ['Original straddles', s.original_pnl == null ? '—' : inr(s.original_pnl), 'the two legs sold at entry', tone(s.original_pnl)],
          ['Extra legs', s.extra_pnl == null ? '—' : inr(s.extra_pnl), `${s.adjustments} adjustment${s.adjustments === 1 ? '' : 's'}${s.rolling ? ` · ${s.rolls} roll${s.rolls === 1 ? '' : 's'}` : ''} · ${s.unwinds} unwind${s.unwinds === 1 ? '' : 's'}`, tone(s.extra_pnl)],
          ['Break-even hits', String(s.breakeven_hits), `held ${mins(s.median_held)} (median)`, s.breakeven_hits ? 'neg' : ''],
        ], 6)}
        <div class="gk-panel"><h3>Did the extra legs help? <span class="s">each day: the original straddle (wide bar), the extra legs (narrow: green earned, amber cost) and the day (blue dot)</span></h3>${split(data.sessions)}</div>
        <div class="gk-two even">
          <div class="gk-panel"><h3>By what ended the day <span class="s">median time it ended</span></h3>
            ${table([...GCOLS, ['At', '60px', 'num']], Object.entries(data.by_exit).sort((a, b) => b[1].n - a[1].n).map(([, x]) => [...grow(x.label, x), x.median_exit || '—']),
              { minw: 0, rowAttrs: i => html`data-tip="${(([, x]) => gtip(x.label, x))(Object.entries(data.by_exit).sort((a, b) => b[1].n - a[1].n)[i])}"` })}
            <h3 style="margin-top:16px">By how many legs it added${s.rolling ? ' or rolled' : ''}</h3>
            ${bars(data.by_moves.map(m => [`${m.moves} · ${m.n} day${m.n === 1 ? '' : 's'}`, m.pnl, gtip(`${m.moves} move${m.moves === '1' ? '' : 's'} that day`, m)]))}
            ${Object.keys(data.by_side).length ? html`<h3 style="margin-top:16px">By the side it adjusted <span class="s">CE: the index fell · PE: it rose</span></h3>
              ${bars(Object.entries(data.by_side).map(([k, x]) => [`${k} · ${x.n}`, x.pnl, gtip(`${k} side adjusted`, x)]))}` : ''}</div>
          <div class="gk-panel"><h3>By weekday</h3>
            ${data.by_weekday.length ? bars(data.by_weekday.map(w => [`${w.day} · ${w.n}`, w.pnl, gtip(`Sold on a ${w.day}`, w)])) : empty('', 'No closed days yet.')}
            <h3 style="margin-top:16px">By days to expiry <span class="s">0 is expiry day</span></h3>
            ${data.by_dte.length ? bars(data.by_dte.map(w => [`${w.dte} day${w.dte === 1 ? '' : 's'} · ${w.n}`, w.pnl, gtip(w.dte === 0 ? 'Expiry day' : `${w.dte} day${w.dte === 1 ? '' : 's'} to expiry`, w)])) : empty('', 'No closed days yet.')}</div>
        </div>
        <div class="gk-panel"><h3>Every day <span class="s">newest first · open one end to end</span></h3>${sessionTable(data.sessions, g)}</div>`
        : empty('No sessions yet', 'They appear after its first entry.')}`);
    }
    return poll(() => api.geek(g.id, 'sessions'), 60000, d => { data = d; paint(); });
  },
};
