// Journal (every deployment): this deployment's notes and the whole-day
// ones, next to how each day went, with its tags and their days' P&L.

import { api } from '../../core/api.js';
import { html, mount, $ } from '../../core/dom.js';
import { inr } from '../../core/format.js';
import { journalBox } from '../../ui/journal.js';
import { head, table, tone } from './ui.js';

export default {
  render(el, v, g) {
    let tags = null;
    const jb = journalBox({ deploymentId: v.id, general: true, emptyText: 'No notes on this deployment yet.',
      onChange: () => api.journalTags(v.id).then(t => { tags = t.tags; paint(); }).catch(() => {}) });
    function paint() {
      mount(el, html`
        ${head('Journal', 'Your notes on this deployment and on whole days, next to what it booked that day. Tags add up the days they mark.', 'your notes')}
        <div class="gk-two">
          <div class="gk-panel"><h3>Notes</h3><div data-journal-slot></div></div>
          <div class="gk-panel"><h3>Tags <span class="s">this deployment’s notes</span></h3>
            ${tags === null ? html`<div class="skel" style="height:80px"></div>` : tags.length
              ? table([['Tag', 'minmax(0,1fr)'], ['Days', '50px', 'num'], ['Avg day', '90px', 'num']],
                tags.map(t => [t.tag, String(t.days), html`<span class="${tone(t.avg)}">${t.avg == null ? '—' : inr(t.avg)}</span>`]),
                { minw: 0, rowAttrs: i => html`data-tip="${`${tags[i].tag}\nNotes  ${tags[i].notes}\nDays  ${tags[i].days}\nP&L of those days  ${inr(tags[i].pnl)}`}"` })
              : html`<p class="small mute">No tags yet.</p>`}</div>
        </div>`);
      jb.attach($('[data-journal-slot]', el));
    }
    paint();
    return () => jb.destroy();
  },
};
