// Journal: notes on days, deployments and trades, in your own words —
// what happened, what you saw, what you did by hand. Each note sits next
// to how that day went, and tags add up the days they mark ("event day",
// "manual intervention"…) so you can see which kinds of days pay.

import { api } from '../core/api.js';
import { html, mount, actions, $, on, debounce } from '../core/dom.js';
import { inr, tone } from '../core/format.js';
import { ui, listen } from '../core/store.js';
import * as book from '../data/book.js';
import { empty } from '../ui/components.js';
import { journalBox } from '../ui/journal.js';

const st = ui('journal', { dep: '', tag: '', period: 'all' });
const PERIODS = [['all', 'All time'], ['90', '90 days'], ['30', '30 days'], ['7', '7 days']];
const since = p => (p === 'all' ? '' : new Date(Date.now() + 5.5 * 3600e3 - +p * 864e5).toISOString().slice(0, 10));

// Each tag's days and how they went, as bars.
function tagTable(tags) {
  if (!tags.length) return empty('', 'Tag your notes to compare the days they mark.');
  const mx = Math.max(1, ...tags.map(t => Math.abs(t.avg || 0)));
  return html`<div class="col" style="gap:8px">${tags.map(t => html`
    <button type="button" class="jr-tagrow ${st.get().tag === t.tag ? 'on' : ''}" data-act="pickTag" data-t="${t.tag}"
      data-tip="${`${t.tag}\nNotes  ${t.notes}\nDays  ${t.days}\nP&L of those days  ${inr(t.pnl)}\nAverage day  ${t.avg == null ? '—' : inr(t.avg)}`}">
      <b>${t.tag}</b><span class="mono tiny mute">${t.days ? `${t.days} day${t.days === 1 ? '' : 's'}` : `${t.notes} note${t.notes === 1 ? '' : 's'}`}</span>
      <span class="bar"><i style="${(t.avg || 0) >= 0 ? 'left:50%' : 'right:50%'};width:${(Math.abs(t.avg || 0) / mx * 50).toFixed(1)}%;background:${(t.avg || 0) >= 0 ? 'var(--g)' : 'var(--r)'}"></i><u></u></span>
      <span class="mono tiny right ${tone(t.avg)}">${t.avg == null ? '—' : inr(t.avg)}</span></button>`)}
    <p class="small mute">Average P&amp;L of the days each tag marks: the note’s deployment that day, or every deployment for a whole-day note. Notes attached to nothing count as notes, not days. Pick a tag to see its notes.</p></div>`;
}

export default {
  title: () => 'Journal',
  render(main) {
    let tags = null, jb = null, q = '';
    const deps = () => book.isLoaded('deployments') ? book.everything().map(v => ({ id: v.id, name: v.name })) : [];
    function filters() {
      const s = st.get();
      return { deployment_id: s.dep && s.dep !== 'none' ? s.dep : '', standalone: s.dep === 'none' ? 'true' : '', tag: s.tag, q, day_from: since(s.period) };
    }
    async function loadTags() {
      try { tags = (await api.journalTags(st.get().dep && st.get().dep !== 'none' ? st.get().dep : '')).tags; } catch (e) { tags = []; }
      paint();
    }
    function paint() {
      const s = st.get(), D = deps();
      mount(main, html`<div class="page-head">
          <div class="col"><h1 class="t-h1">Journal</h1><span class="mute">Notes on days, deployments and trades, next to how each day went.</span></div>
        </div>
        <div class="page" style="padding-top:4px">
        <div class="row wrap" style="gap:10px;margin-bottom:16px">
          <select class="input" data-f="dep" aria-label="Deployment" style="max-width:260px">
            <option value="" ${s.dep ? '' : 'selected'}>Every note</option>
            <option value="none" ${s.dep === 'none' ? 'selected' : ''}>Not attached to anything</option>
            ${D.map(d => html`<option value="${d.id}" ${s.dep === d.id ? 'selected' : ''}>${d.name}</option>`)}</select>
          <div class="seg" role="group">${PERIODS.map(([k, l]) => html`<button type="button" class="${s.period === k ? 'on' : ''}" data-act="period" data-k="${k}">${l}</button>`)}</div>
          <input class="input search grow" style="min-width:200px" type="search" data-f="q" value="${q}" placeholder="Search the notes" aria-label="Search the notes">
          ${s.tag ? html`<button type="button" class="chip on" data-act="pickTag" data-t="${s.tag}">${s.tag} ✕</button>` : ''}
        </div>
        <div class="jr-page">
          <section class="jr-main" aria-label="Notes"><div data-journal-slot></div></section>
          <aside class="jr-side"><h3 class="t-h3" style="margin:0 0 10px">Tags</h3>${tags === null ? html`<div class="skel" style="height:120px"></div>` : tagTable(tags)}</aside>
        </div></div>`);
      if (!jb) jb = journalBox({ deployments: D, filters, limit: 500, onChange: () => loadTags(), emptyText: s.tag || q ? 'No notes match.' : 'No notes yet. Write the first one above.' });
      jb.attach($('[data-journal-slot]', main));
    }
    const offActs = actions(main, {
      period: d => { st.set({ period: d.k }); jb.reload(); paint(); },
      pickTag: d => { st.set({ tag: st.get().tag === d.t ? '' : d.t }); jb.reload(); paint(); },
    });
    const search = debounce(() => jb.reload(), 250);
    const offIn = on(main, 'input', '[data-f="q"]', (e, el) => { q = el.value; search(); });
    const offCh = on(main, 'change', '[data-f="dep"]', (e, el) => { st.set({ dep: el.value }); jb.reload(); loadTags(); });
    const off = listen('data', () => { if (!jb) paint(); });
    paint();
    return () => { offActs(); offIn(); offCh(); off(); if (jb) jb.destroy(); };
  },
};
