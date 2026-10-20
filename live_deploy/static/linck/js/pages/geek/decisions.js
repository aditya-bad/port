// Decisions: every check the strategy made, newest first. Trades explain
// themselves (what each fill's metadata says it saw); checks that didn't
// trade come from the decision log (EOD found nothing to do, a trigger
// refuted by live quotes, management held for stale prices).

import { api } from '../../core/api.js';
import { html, mount, actions } from '../../core/dom.js';
import { dStamp } from '../../core/format.js';
import { ui } from '../../core/store.js';
import { empty, loading } from '../../ui/components.js';
import { head, table, poll } from './ui.js';

const st = ui('geekDecisions', { outcome: 'all', scope: 'cycle' });
const dayOf = t => new Date(t).toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
const OUT = { acted: 'Acted', no_action: 'No action', blocked: 'Blocked' };

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Decisions')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Decisions')}${empty('Couldn’t load decisions', data.message)}`); return; }
      const s = st.get();
      const eng = g.engine(), cycle = eng && eng.state && eng.state.cycle_id != null ? eng.state.cycle_id : null;
      // A strategy without cycles scopes to today while it runs.
      const daily = cycle == null && !!(eng && eng.running);
      const today = dayOf(new Date());
      const inScope = data.rows.filter(r => s.scope === 'all' || (cycle != null ? r.cycle_id === cycle : !daily || dayOf(r.t) === today));
      const rows = inScope.filter(r => s.outcome === 'all' || r.outcome === s.outcome);
      const n = k => inScope.filter(r => k === 'all' || r.outcome === k).length;
      mount(el, html`
        ${head('Decisions', 'Every check the strategy made and why it acted or didn’t, with the numbers it saw at that moment.', 'fills · decision log')}
        <div class="row wrap" style="gap:10px">
          <div class="chips">${[['all', 'All'], ['acted', 'Acted'], ['no_action', 'Checked, no action'], ['blocked', 'Blocked by a guard']].map(([k, l]) => html`
            <button type="button" class="chip ${s.outcome === k ? 'on' : ''}" data-act="outcome" data-k="${k}">${l} <span class="n">${n(k)}</span></button>`)}</div>
          <span class="grow"></span>
          ${cycle != null || daily ? html`<div class="seg">${(cycle != null ? [['cycle', `Cycle ${cycle}`], ['all', 'All cycles']] : [['cycle', 'Today'], ['all', 'All days']]).map(([k, l]) => html`<button type="button" class="${s.scope === k ? 'on' : ''}" data-act="scope" data-k="${k}">${l}</button>`)}</div>` : ''}
        </div>
        ${rows.length ? table([['When', '120px'], ['Rule', '150px'], ['Outcome', '110px'], ['What it did · what it saw', 'minmax(0,1fr)']],
          rows.map(r => [html`<span class="mono tiny">${dStamp(r.t)}</span>`, html`<b>${r.label}</b>`,
            html`<span class="oc ${r.outcome}">${OUT[r.outcome] || r.outcome}</span>`,
            html`<span class="col" style="gap:2px"><span>${r.summary}</span>${r.why ? html`<span class="mono tiny mute">${r.why}</span>` : ''}</span>`]), { minw: 640 })
          : empty('', s.outcome === 'all' ? 'Nothing yet in this scope.' : 'None of this kind in this scope.')}
        <p class="gk-note">Checks that didn’t trade are recorded from the day this version was deployed; trades go back to the first one.</p>`);
    }
    const offActs = actions(el, {
      outcome: d => { st.set({ outcome: d.k }); paint(); },
      scope: d => { st.set({ scope: d.k }); paint(); },
    });
    let shownCycle = null;
    const off = g.onEngine(e => { const c = `${e && e.state ? e.state.cycle_id : ''}|${!!(e && e.running)}`; if (c !== shownCycle) { shownCycle = c; paint(); } });
    const stop = poll(() => api.geek(g.id, 'decisions'), 30000, d => { data = d; paint(); });
    return () => { offActs(); off(); stop(); };
  },
};
