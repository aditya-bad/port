// Entries: why each contract and strike was picked — the expiry rule
// that applied, the premium it aimed for, what it got.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { dStamp } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, table, poll } from './ui.js';

const TRIG = t => (t === 'initial_entry' ? 'First entry' : t === 'checkpoint_target' ? 'After checkpoint' : t.startsWith('reentry_after_') ? `After ${t.slice(14).replace(/_/g, ' ')}` : t);

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Entries')}${loading(5)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Entries')}${empty('Couldn’t load entries', data.message)}`); return; }
      if (!data.rows.length) { mount(el, html`${head('Entries')}${empty('No entries yet', `The first one is at ${(v.raw.config || {}).entry_time || 'entry time'}.`)}`); return; }
      mount(el, html`
        ${head('Entries', 'Why each contract and strike was picked: the expiry rule that applied, the premium it aimed for, and what it got.', 'fill metadata')}
        ${table([['Entered', '150px'], ['Why', '130px'], ['Expiry rule', 'minmax(200px,1.3fr)'], ['Target', '76px', 'num'], ['Got', 'minmax(220px,1.2fr)'], ['Slippage, pts', '150px', 'num']],
          data.rows.map(r => [html`<span class="mono tiny">${dStamp(r.t)}</span><br><span class="tiny mute">cycle ${r.cycle_id ?? '—'}</span>`, TRIG(r.trigger),
            html`<span class="small">Day ${r.day_of_month ?? '—'} → ${r.rotation || '—'} ${r.contract_expiry || ''}<br><span class="mute">${r.days_to_expiry != null ? `${r.days_to_expiry} days out · ` : ''}${r.capital_pct != null ? `${r.long_days ? 'long-days ' : ''}${(r.capital_pct * 100).toFixed(1).replace(/\.0$/, '')}% of capital` : ''}</span></span>`,
            r.target != null ? r.target.toFixed(1) : '—',
            html`<span class="mono tiny">${r.legs.map(l => `${l.opt} ${l.strike ?? ''} @${l.price.toFixed(1)}`).join(' · ')}${r.hedges.length ? html`<br><span class="mute">hedges ${r.hedges.map(h => h.symbol).join(', ')}</span>` : ''}</span>`,
            html`<span class="mono tiny">${r.legs.map(l => (l.slip_pts != null ? `${l.opt} ${l.slip_pts.toFixed(2)}` : `${l.opt} —`)).join(' · ')}</span>`]), { minw: 940 })}
        <p class="gk-note">Target per leg = capital × the % in use ÷ lot size ÷ 2. Long-days applies to a fresh entry whose contract is more than 29 days out and in a later month. Slippage is the fill against the order book at that moment, in points.</p>`);
    }
    const stop = poll(() => api.geek(g.id, 'entries'), 120000, d => { data = d; paint(); });
    return stop;
  },
};
