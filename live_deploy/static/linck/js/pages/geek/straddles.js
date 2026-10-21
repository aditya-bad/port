// Straddles (DTT): every straddle it sold, one a day, how each ended,
// and the same by weekday and by days to expiry.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr, price, dFull } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, bars, tone } from './ui.js';
import { mins } from './st.js';
import { exitName } from './dtt.js';

const day = d => dFull(new Date(`${d}T12:00:00+05:30`));
// One straddle, for a tooltip.
export const straddleTip = r => `${day(r.day)} · ${r.strike ? r.strike.toLocaleString('en-IN') : ''} straddle\nCE  ${r.ce ? `${price(r.ce.entry)} → ${r.ce.exit == null ? 'open' : price(r.ce.exit)}` : '—'}\nPE  ${r.pe ? `${price(r.pe.entry)} → ${r.pe.exit == null ? 'open' : price(r.pe.exit)}` : '—'}\nCombined  ${r.decay_pct == null ? '—' : `${r.decay_pct > 0 ? 'fell' : 'rose'} ${Math.abs(r.decay_pct)}%`}\nEnded by  ${exitName(r.reason)}${r.stop_leg ? ` (${r.stop_leg})` : ''}${r.exit_t ? ` at ${r.exit_t.slice(11, 16)}` : ''}\nHeld  ${mins(r.held_min)}\nP&L  ${r.pnl == null ? '—' : inr(r.pnl)}`;
export const GCOLS = [['', 'minmax(110px,1fr)'], ['Straddles', '76px', 'num'], ['Won', '56px', 'num'], ['P&L', '96px', 'num'], ['Avg', '90px', 'num']];
export const grow = (name, g) => [name, String(g.n), `${Math.round(g.win_rate)}%`,
  html`<span class="${tone(g.pnl)}">${inr(g.pnl)}</span>`, html`<span class="${tone(g.avg)}">${inr(g.avg)}</span>`];

export function straddleTable(rows, g) {
  return table([['Day', '150px'], ['Strike', '80px', 'num'], ['CE in → out', '156px', 'num'], ['PE in → out', '156px', 'num'],
    ['Combined', '96px', 'num'], ['Ended by', '150px'], ['Held', '72px', 'num'], ['P&L', '96px', 'num']],
  rows.map(r => [html`<a href="${g.href('straddle')}?s=${r.id}">${day(r.day)}</a>`,
    r.strike ? r.strike.toLocaleString('en-IN') : '—',
    html`<span class="mono small">${r.ce ? `${price(r.ce.entry)} → ${r.ce.exit == null ? '…' : price(r.ce.exit)}` : '—'}</span>`,
    html`<span class="mono small">${r.pe ? `${price(r.pe.entry)} → ${r.pe.exit == null ? '…' : price(r.pe.exit)}` : '—'}</span>`,
    r.decay_pct == null ? '—' : html`<span class="${tone(r.decay_pct)}">${r.decay_pct > 0 ? '−' : '+'}${Math.abs(r.decay_pct)}%</span>`,
    r.open ? html`<span class="oc acted">Open</span>` : html`${exitName(r.reason)}${r.stop_leg ? html` <span class="mute">(${r.stop_leg})</span>` : ''}`,
    mins(r.held_min), html`<span class="${tone(r.pnl)}">${r.pnl == null ? '—' : inr(r.pnl)}</span>`]), { minw: 960 });
}

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Straddles')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Straddles')}${empty('Couldn’t load the straddles', data.message)}`); return; }
      const s = data.summary;
      mount(el, html`
        ${head('Straddles', 'Every straddle it sold, one a day: the strike, each leg in and out, how far the combined premium fell, and what ended it.', `${s.straddles} straddles`)}
        ${s.straddles ? html`
        ${tiles([
          ['Straddles', String(s.straddles), s.open ? `${s.open} open` : `${s.late_starts} started late`],
          ['Won', s.win_rate == null ? '—' : `${Math.round(s.win_rate)}%`, `${s.wins} of ${s.closed}`],
          ['P&L', inr(s.pnl), s.avg == null ? '' : `avg ${inr(s.avg)}`, tone(s.pnl)],
          ['Best / worst', s.best == null ? '—' : inr(s.best), s.worst == null ? '' : `worst ${inr(s.worst)}`, tone(s.best)],
          ['Combined fell', s.median_decay == null ? '—' : `${s.median_decay}%`, 'median, entry to exit'],
          ['Held', mins(s.median_held), 'median'],
        ], 6)}
        <div class="gk-two even">
          <div class="gk-panel"><h3>By what ended it <span class="s">median time it ended</span></h3>
            ${table([...GCOLS, ['At', '64px', 'num']], Object.entries(data.by_exit).sort((a, b) => b[1].n - a[1].n)
              .map(([, x]) => [...grow(x.label, x), x.median_exit || '—']), { minw: 0 })}
            <h3 style="margin-top:16px">Which leg stopped it <span class="s">of the leg stops</span></h3>
            <p class="small">CE <b>${data.stop_leg.CE}</b> · PE <b>${data.stop_leg.PE}</b>${data.stop_leg.both ? html` · both <b>${data.stop_leg.both}</b>` : ''}
              <span class="mute"> · a CE stop means the index rallied, a PE stop that it fell</span></p></div>
          <div class="gk-panel"><h3>By weekday <span class="s">P&amp;L of straddles sold that day</span></h3>
            ${data.by_weekday.length ? bars(data.by_weekday.map(w => [`${w.day} · ${w.n}`, w.pnl, `Sold on a ${w.day}\nStraddles  ${w.n}\nWon  ${w.wins} (${Math.round(w.win_rate)}%)\nP&L  ${inr(w.pnl)}\nAverage  ${inr(w.avg)}`])) : empty('', 'No closed straddles yet.')}
            <h3 style="margin-top:16px">By days to expiry <span class="s">0 is expiry day</span></h3>
            ${data.by_dte.length ? bars(data.by_dte.map(w => [`${w.dte} day${w.dte === 1 ? '' : 's'} · ${w.n}`, w.pnl, `${w.dte === 0 ? 'Expiry day' : `${w.dte} day${w.dte === 1 ? '' : 's'} to expiry`}\nStraddles  ${w.n}\nWon  ${w.wins} (${Math.round(w.win_rate)}%)\nP&L  ${inr(w.pnl)}\nAverage  ${inr(w.avg)}`])) : empty('', 'No closed straddles yet.')}</div>
        </div>
        <div class="gk-panel"><h3>Every straddle <span class="s">newest first · open one end to end</span></h3>${straddleTable(data.straddles, g)}</div>`
        : empty('No straddles yet', 'They appear after its first entry.')}`);
    }
    return poll(() => api.geek(g.id, 'straddles'), 60000, d => { data = d; paint(); });
  },
};
