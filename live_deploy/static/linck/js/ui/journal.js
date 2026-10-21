// The trade journal box: write a note and see the notes of one scope (a
// day, a deployment, one trade, or everything). Built once and kept, so
// pages that redraw on a timer re-attach it instead of losing what's
// being typed:  const jb = journalBox({...}); after each paint
// jb.attach(slotElement); on cleanup jb.destroy().

import { api } from '../core/api.js';
import { html, mount, actions, $ } from '../core/dom.js';
import { inr, tone } from '../core/format.js';
import { empty } from './components.js';
import { parseTags, tagsText, addTag, problem, byDay } from '../data/journal.js';

const dayName = d => new Date(`${d}T12:00:00+05:30`).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
const todayIST = () => new Date(Date.now() + 5.5 * 3600e3).toISOString().slice(0, 10);

// opts: { deploymentId, day, positionId, general, deployments: [{id, name}],
//         limit, onChange(entries), compact, filters: () => ({tag, q, ...}) }
export function journalBox(opts = {}) {
  const root = document.createElement('div');
  root.className = 'jr-box';
  let entries = null, suggested = [], editing = null, confirming = null, error = '', saving = false, alive = true;
  // dep: a deployment id, 'day' (the whole day, every deployment) or
  // 'none' (a standalone note, attached to nothing)
  const draft = { body: '', tags: '', day: opts.day || todayIST(), dep: opts.deploymentId || 'none' };

  const fixedDay = !!opts.day, fixedDep = !!opts.deploymentId;
  const scopeQ = () => ({
    deployment_id: opts.deploymentId, position_id: opts.positionId, general: opts.general ? 'true' : '',
    day_from: opts.day, day_to: opts.day, limit: opts.limit || 300, ...(opts.filters ? opts.filters() : {}),
  });

  async function load() {
    try {
      const [j, t] = await Promise.all([api.journal(scopeQ()), suggested.length ? null : api.journalTags()]);
      if (!alive) return;
      entries = j.entries;
      if (t) suggested = [...t.tags.map(x => x.tag), ...t.suggested].slice(0, 14);
      if (opts.onChange) opts.onChange(entries);
    } catch (e) { error = e.message; }
    paint();
  }

  function note(e) {
    if (editing && editing.id === e.id) {
      return html`<div class="jr-note editing" data-note="${e.id}">
        <textarea class="input jr-text" data-edit-body rows="3" aria-label="Edit the note">${editing.body}</textarea>
        <input class="input" data-edit-tags value="${editing.tags}" placeholder="Tags, separated by commas" aria-label="Tags">
        <div class="row" style="gap:8px"><button type="button" class="btn btn-primary btn-sm" data-act="jrSave" data-id="${e.id}">Save</button>
          <button type="button" class="btn btn-sm" data-act="jrCancel">Cancel</button></div></div>`;
    }
    return html`<div class="jr-note" data-note="${e.id}">
      <div class="row wrap" style="gap:8px;align-items:baseline">
        ${e.standalone ? html`<span class="jr-dep jr-free">Not attached</span>`
          : !fixedDep ? html`<span class="jr-dep">${e.deployment_name || 'Whole day'}</span>` : e.deployment_id ? '' : html`<span class="jr-dep">Whole day</span>`}
        ${e.position_symbol ? html`<span class="mono tiny">${e.position_symbol}</span>` : ''}
        ${e.tags.map(t => html`<span class="jr-tag">${t}</span>`)}
        <span class="grow"></span>
        ${e.standalone ? '' : html`<span class="mono tiny ${tone(e.day_pnl)}" data-tip="${`${e.deployment_name || 'Every deployment'} on ${dayName(e.day)}\nBooked that day  ${inr(e.day_pnl)}\nTrades closed  ${e.day_closed}`}">${e.day_closed ? inr(e.day_pnl) : 'no trades closed'}</span>`}
      </div>
      <p class="jr-body">${e.body}</p>
      <div class="row tiny mute" style="gap:10px"><span>${e.author || ''}</span><span class="grow"></span>
        <button type="button" class="linkish" data-act="jrEdit" data-id="${e.id}">Edit</button>
        ${confirming === e.id ? html`<button type="button" class="linkish neg" data-act="jrDelete" data-id="${e.id}">Delete it</button><button type="button" class="linkish" data-act="jrKeep">Keep</button>`
          : html`<button type="button" class="linkish" data-act="jrAsk" data-id="${e.id}">Delete</button>`}</div>
    </div>`;
  }

  function paint() {
    const list = entries === null ? html`<div class="skel" style="height:60px"></div>`
      : entries.length ? (fixedDay ? entries.map(note) : byDay(entries).map(([d, N]) => html`<div class="jr-day"><h4>${dayName(d)}</h4>${N.map(note)}</div>`))
        : empty('', opts.emptyText || 'No notes yet.');
    const chosen = parseTags(draft.tags);
    mount(root, html`
      <form class="jr-new" data-jr-form>
        <textarea class="input jr-text" data-jr-body rows="${opts.compact ? 2 : 3}" maxlength="4000" placeholder="${opts.placeholder || 'What happened, what you saw, what you did by hand…'}" aria-label="New note">${draft.body}</textarea>
        <div class="row wrap" style="gap:8px">
          ${fixedDay ? '' : html`<input class="input" type="date" data-jr-day value="${draft.day}" aria-label="Day" style="width:160px">`}
          ${fixedDep || !opts.deployments ? '' : html`<select class="input" data-jr-dep aria-label="Attach the note to" style="max-width:240px">
            <option value="none" ${draft.dep === 'none' ? 'selected' : ''}>Just a note (not attached)</option>
            <option value="day" ${draft.dep === 'day' ? 'selected' : ''}>Whole day (every deployment)</option>
            ${opts.deployments.map(d => html`<option value="${d.id}" ${draft.dep === d.id ? 'selected' : ''}>${d.name}</option>`)}</select>`}
          <input class="input grow" data-jr-tags value="${draft.tags}" placeholder="Tags, separated by commas" aria-label="Tags" style="min-width:180px">
          <button type="submit" class="btn btn-primary" ${saving ? 'disabled' : ''}>${saving ? 'Saving…' : 'Add note'}</button>
        </div>
        <div class="chips">${suggested.filter(t => !chosen.includes(t)).slice(0, 10).map(t => html`<button type="button" class="chip" data-act="jrTag" data-t="${t}">+ ${t}</button>`)}</div>
        ${error ? html`<p class="small neg" role="alert">${error}</p>` : ''}
      </form>
      <div class="jr-list">${list}</div>`);
  }

  const offActs = actions(root, {
    jrTag: d => { draft.tags = addTag($('[data-jr-tags]', root).value, d.t); draft.body = $('[data-jr-body]', root).value; paint(); },
    jrEdit: d => { const e = entries.find(x => x.id === d.id); editing = { id: e.id, body: e.body, tags: tagsText(e.tags) }; confirming = null; paint(); },
    jrCancel: () => { editing = null; paint(); },
    jrAsk: d => { confirming = d.id; paint(); },
    jrKeep: () => { confirming = null; paint(); },
    jrDelete: async d => {
      try { await api.deleteNote(d.id); confirming = null; error = ''; await load(); } catch (e) { error = e.message; paint(); }
    },
    jrSave: async d => {
      const body = $('[data-edit-body]', root).value, tags = parseTags($('[data-edit-tags]', root).value);
      const bad = problem(body, tags);
      if (bad) { error = bad; paint(); return; }
      try { await api.editNote(d.id, { body, tags }); editing = null; error = ''; await load(); } catch (e) { error = e.message; paint(); }
    },
  });
  const onSubmit = async ev => {
    if (!ev.target.matches('[data-jr-form]')) return;
    ev.preventDefault();
    draft.body = $('[data-jr-body]', root).value;
    draft.tags = $('[data-jr-tags]', root).value;
    const dayEl = $('[data-jr-day]', root), depEl = $('[data-jr-dep]', root);
    if (dayEl) draft.day = dayEl.value || todayIST();
    if (depEl) draft.dep = depEl.value;
    const tags = parseTags(draft.tags), bad = problem(draft.body, tags);
    if (bad) { error = bad; paint(); return; }
    saving = true; error = ''; paint();
    try {
      const dep = opts.deploymentId || (draft.dep === 'none' || draft.dep === 'day' ? null : draft.dep);
      await api.addNote({ body: draft.body, tags, day: draft.day, deployment_id: dep, position_id: opts.positionId || null,
        standalone: !opts.deploymentId && !opts.positionId && draft.dep === 'none' && !!opts.deployments });
      draft.body = ''; draft.tags = '';
      saving = false;
      await load();
    } catch (e) { saving = false; error = e.message; paint(); }
  };
  root.addEventListener('submit', onSubmit);
  // what's typed survives a redraw of the box itself
  const onInput = ev => {
    if (ev.target.matches('[data-jr-body]')) draft.body = ev.target.value;
    if (ev.target.matches('[data-jr-tags]')) draft.tags = ev.target.value;
    if (ev.target.matches('[data-edit-body]') && editing) editing.body = ev.target.value;
    if (ev.target.matches('[data-edit-tags]') && editing) editing.tags = ev.target.value;
  };
  root.addEventListener('input', onInput);
  paint();
  load();
  return {
    root,
    reload: load,
    attach(slot) { if (slot && root.parentNode !== slot) slot.appendChild(root); },
    entries: () => entries || [],
    destroy() { alive = false; offActs(); root.removeEventListener('submit', onSubmit); root.removeEventListener('input', onInput); root.remove(); },
  };
}
