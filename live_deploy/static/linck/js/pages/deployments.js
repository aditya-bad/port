// Deployments — every deployment, built for 60+: search, filters with
// live counts, sortable columns, bulk actions, a totals row, cards on
// phones, 25 or 50 per page.

import { html, mount, $, actions, on, setText, setTone } from '../core/dom.js';
import { inr, inrS, pct, ago, plural, tone } from '../core/format.js';
import { ui, listen } from '../core/store.js';
import { go, href } from '../core/router.js';
import * as book from '../data/book.js';
import { LAB_TAG } from '../data/config.js';
import { status, check, chips, tagChip, empty, marker, STATUS } from '../ui/components.js';
import { say } from '../ui/toast.js';
import * as A from '../actions.js';
import { cycleText, cycleTone, cycleTip, closedNote } from '../ui/cycle.js';
import { repairChip } from '../ui/repairs.js';

const st = ui('deployments', { q: '', st: 'all', mode: 'all', idx: 'all', tag: 'all', lab: false, sk: 'today', sd: -1, page: 0, dense: false });
const COLS = [
  ['status', 'Status'], ['name', 'Deployment'], [null, 'Tags'], ['mode', 'Mode'], ['idx', 'Index'],
  ['cap', 'Capital', 1], ['today', 'Cycle', 1], ['overall', 'Overall', 1], ['ret', 'Return', 1], ['legs', 'Legs', 1], ['last', 'Last trade', 1], [null, '', 1],
];
const SK = {
  status: r => ['error', 'paused', 'active', 'stopped'].indexOf(r.v.status), name: r => r.v.name, mode: r => r.v.mode, idx: r => r.v.index,
  cap: r => r.v.cap, today: r => (r.x.cycle == null ? -Infinity : r.x.cycle), overall: r => r.x.overall, ret: r => r.x.ret, legs: r => r.v.legs.length,
  last: r => (r.v.lastTradeAt ? r.v.lastTradeAt.getTime() : 0),
};

export default {
  title: 'Deployments',
  render(main, params) {
    const sel = new Set();
    let menuOpen = null;
    const strat = params.strat || 'all';

    function pass(v, s, skip) {
      const q = s.q.trim().toLowerCase();
      return (s.lab || !v.isLab || s.tag === LAB_TAG)
        && (skip === 'st' || s.st === 'all' || v.status === s.st)
        && (skip === 'mode' || s.mode === 'all' || v.modeKey === s.mode)
        && (skip === 'idx' || s.idx === 'all' || v.index === s.idx)
        && (skip === 'tag' || s.tag === 'all' || v.tags.includes(s.tag))
        && (strat === 'all' || v.strategy === strat)
        && (!q || `${v.name} ${v.strategy} ${v.strategyLabel} ${v.tags.join(' ')} ${v.raw.notes || ''}`.toLowerCase().includes(q));
    }
    function group(all, s, key, label, opts) {
      const field = { st: 'status', mode: 'modeKey', idx: 'index' }[key];
      const n = val => all.filter(v => pass(v, s, key) && (val === 'all' || (key === 'tag' ? v.tags.includes(val) : v[field] === val))).length;
      return html`<div class="chip-group"><span class="label">${label}</span>${chips(opts.map(([k, l]) => [k, l, n(k)]), s[key], `f:${key}`)}</div>`;
    }

    function rowMenu(v) {
      return html`<div class="menu" role="menu">${A.menuItems(v).map((m, i) => html`
        <button type="button" role="menuitem" data-act="menuItem" data-id="${v.id}" data-i="${i}" ${m.off ? html`aria-disabled="true" data-reason="${m.h}"` : ''}><b>${m.l}</b><span>${m.h}</span></button>`)}</div>`;
    }

    function paint() {
      if (!book.isLoaded('deployments')) { mount(main, html`<div class="page"><div class="skel" style="height:300px"></div></div>`); return; }
      const s = st.get();
      const all = book.deployments();
      const rows = all.filter(v => pass(v, s)).map(v => ({ v, x: book.live(v) }));
      const f = SK[s.sk] || SK.today;
      rows.sort((a, b) => { const p = f(a), q = f(b); return (typeof p === 'string' ? p.localeCompare(q) : p - q) * s.sd; });
      const PS = s.dense ? 50 : 25, pages = Math.max(1, Math.ceil(rows.length / PS)), page = Math.min(s.page, pages - 1);
      const pr = rows.slice(page * PS, page * PS + PS);
      const idxs = [...new Set(all.map(v => v.index))].filter(x => x !== '—').sort();
      const tagNames = book.tags().map(t => t.name);
      const selected = [...sel].map(id => book.find(id)).filter(Boolean);
      const allOnPage = pr.length && pr.every(r => sel.has(r.v.id));
      const T = rows.reduce((a, r) => { a.cap += r.v.cap; a.today += r.x.cycleSum; a.overall += r.x.overall; a.legs += r.v.legs.length; return a; }, { cap: 0, today: 0, overall: 0, legs: 0 });
      const stratF = strat !== 'all' ? (book.strategy(strat) || { label: strat }).label : null;
      const bulk = [
        ['Pause', 'bulkPause', selected.some(v => v.rawStatus === 'active'), 'Nothing selected is running'],
        ['Resume', 'bulkResume', selected.some(v => v.rawStatus === 'paused'), 'Nothing selected is paused'],
        ['Close positions…', 'bulkClose', selected.some(v => v.legs.length && v.rawStatus !== 'stopped'), 'Nothing selected holds positions'],
        ['Add tag…', 'bulkTag', true, ''],
        ['Compare', 'bulkCompare', selected.length >= 2 && selected.length <= 5, 'Select 2 to 5 deployments to compare'],
      ];
      mount(main, html`
        <div class="page-head">
          <div class="col"><h1 class="t-h1">Deployments</h1>
            <span class="mute">${plural(all.length, 'deployment')} · ${all.filter(v => v.rawStatus === 'active').length} active · ${all.filter(v => v.legs.length).length} holding positions · ${all.filter(v => v.isLab).length} in Strategy Lab</span></div>
          <span class="grow"></span>
          <button type="button" class="btn btn-primary" data-act="new">New deployment</button>
        </div>
        <div class="pad-x filters">
          <input class="input search" type="search" value="${s.q}" placeholder="Search name, strategy, tag or notes" aria-label="Search deployments" data-search>
          <button type="button" class="btn btn-sm only-phone" data-act="showF" aria-expanded="${!!s.showF}">Filters${activeF(s) ? ` · ${activeF(s)}` : ''}</button>
          <div class="filter-groups ${s.showF ? 'open' : ''}">
          ${group(all, s, 'st', 'Status', [['all', 'All'], ['active', 'Active'], ['paused', 'Paused'], ['error', 'Error'], ['stopped', 'Stopped']])}
          ${group(all, s, 'mode', 'Mode', [['all', 'All'], ['intraday', 'Intraday'], ['positional', 'Positional']])}
          ${group(all, s, 'idx', 'Index', [['all', 'All'], ...idxs.map(i => [i, i])])}
          ${group(all, s, 'tag', 'Tag', [['all', 'All'], ...tagNames.map(t => [t, t])])}
          ${check(s.lab, 'Show Strategy Lab', 'lab')}
          </div>
          ${stratF ? html`<button type="button" class="pill" data-act="clearStrat">Strategy: ${stratF} ×</button>` : ''}
          <span class="grow"></span>
          <button type="button" class="tiny b mute hide-phone" data-act="dense">${s.dense ? 'Comfortable rows' : 'Compact rows'}</button>
        </div>
        ${selected.length ? html`
          <div class="selbar" role="toolbar" aria-label="Bulk actions">
            <span class="mono">${selected.length} selected</span>
            ${bulk.map(([l, act, ok, why]) => html`<button type="button" data-act="${act}" ${ok ? '' : html`aria-disabled="true" data-reason="${why}" title="${why}"`}>${l}</button>`)}
            <span class="grow"></span><button type="button" data-act="clearSel">Clear</button>
          </div>` : ''}
        <div class="pad-x" style="padding-bottom:var(--px)">
          <div class="cards only-phone">
            ${pr.map(({ v, x }) => html`
              <a class="card-row ${v.issue ? 'att' : ''}" href="${href(`d/${v.id}`)}">
                <span class="row" style="gap:8px;min-width:0">${marker(v.status)}<b class="ell">${v.name}</b></span>
                <span class="col right" style="gap:1px;align-items:flex-end" data-tip="${cycleTip(v, x)}"><span class="mono ${cycleTone(x)}" data-today="${v.id}">${cycleText(x)}</span>${closedNote(x)}</span>
                <span class="tiny mute" style="padding-left:17px">${STATUS[v.status].label} · ${v.mode} · ${plural(v.legs.length, 'leg')} · ${noteOf(v) || v.strategy}</span>
                <span class="mono right mute" style="font-size:11px">${inrS(x.overall)} all</span>
              </a>`)}
          </div>
          <div class="hide-phone scroll-x">
            <div class="tbl dep-tbl ${s.dense ? 'dense' : ''}">
              <div class="tr th">
                <button type="button" class="box ${allOnPage ? 'on' : ''}" data-act="selPage" aria-label="Select all on this page"></button>
                ${COLS.map(([k, l, r]) => (k
                  ? html`<button type="button" class="${r ? 'right' : ''} ${s.sk === k ? 'on' : ''}" data-act="sort" data-k="${k}">${l}${s.sk === k ? (s.sd > 0 ? ' ↑' : ' ↓') : ''}</button>`
                  : html`<span class="${r ? 'right' : ''}">${l}</span>`))}
              </div>
              ${pr.map(({ v, x }) => {
                const note = noteOf(v);
                const [act] = A.primary(v);
                return html`
                <div class="tr hover ${v.issue ? 'att' : sel.has(v.id) ? 'sel' : ''}" style="position:relative">
                  <button type="button" class="box ${sel.has(v.id) ? 'on' : ''}" data-act="sel" data-id="${v.id}" aria-label="Select ${v.name}"></button>
                  ${status(v.status)}
                  <a class="cell-name" href="${href(`d/${v.id}`)}"><b>${v.name}</b><span class="${note ? 'warn' : ''}">${note || v.strategy}${v.variations ? html` · <span class="var-n" data-var-n="${v.variations}">+${plural(v.variations, 'variation')}</span>` : ''}</span></a>
                  <span class="row wrap" style="gap:4px">${repairChip(v)}${v.tags.map(tagChip)}</span>
                  <span class="small mute">${v.mode}</span>
                  <span class="mono tiny">${v.index}</span>
                  <span class="num tiny">${inrS(v.cap, { sign: false })}</span>
                  <span class="num col" style="gap:1px;align-items:flex-end" data-tip="${cycleTip(v, x)}"><span class="${cycleTone(x)}" data-today="${v.id}">${cycleText(x)}</span>${closedNote(x)}</span>
                  <span class="num ${tone(x.overall)}" data-overall="${v.id}">${inrS(x.overall)}</span>
                  <span class="num tiny ${tone(x.overall)}">${pct(x.ret, 1)}</span>
                  <span class="num tiny">${v.legs.length || '—'}</span>
                  <span class="tiny mute right">${ago(v.lastTradeAt)}</span>
                  <span class="row" style="gap:4px;justify-content:flex-end">
                    <button type="button" class="btn btn-xs" data-act="primary" data-id="${v.id}">${act}</button>
                    <button type="button" class="btn btn-xs btn-icon" data-act="menu" data-id="${v.id}" aria-haspopup="menu" aria-expanded="${menuOpen === v.id}" aria-label="More actions for ${v.name}">···</button>
                  </span>
                  ${menuOpen === v.id ? rowMenu(v) : ''}
                </div>`;
              })}
              ${pr.length ? html`
                <div class="tr tot">
                  <span></span><span>Total</span><span class="small mute">${plural(rows.length, 'deployment')}</span><span></span><span></span><span></span>
                  <span class="num tiny">${inrS(T.cap, { sign: false })}</span>
                  <span class="num ${tone(T.today)}" data-tot-today data-tip="${'Every deployment’s current cycle added up,\nwith any positional cycle that closed today'}">${inr(T.today)}</span>
                  <span class="num ${tone(T.overall)}">${inrS(T.overall)}</span>
                  <span class="num tiny ${tone(T.overall)}">${pct(T.cap ? T.overall / T.cap * 100 : 0, 1)}</span>
                  <span class="num tiny">${T.legs || '—'}</span><span></span><span></span>
                </div>` : ''}
            </div>
          </div>
          ${!rows.length ? empty('No deployments match', 'Try clearing a filter or the search.', html`<button type="button" class="btn" data-act="clearAll">Clear all filters</button>`) : ''}
          <div class="row small" style="padding-top:14px;gap:10px">
            <span class="mute">${rows.length ? `Showing ${page * PS + 1}–${Math.min(rows.length, page * PS + PS)} of ${rows.length}` : ''}</span>
            <span class="grow"></span>
            <button type="button" class="btn btn-sm" data-act="prev" ${page > 0 ? '' : html`aria-disabled="true"`}>‹ Prev</button>
            <button type="button" class="btn btn-sm" data-act="next" ${page < pages - 1 ? '' : html`aria-disabled="true"`}>Next ›</button>
          </div>
        </div>`);
      const inp = $('[data-search]', main);
      if (inp && document.activeElement === document.body && s.q) { /* keep caret where the user left it */ }
    }

    function activeF(s) {
      return ['st', 'mode', 'idx', 'tag'].filter(k => s[k] !== 'all').length + (s.lab ? 1 : 0);
    }
    function noteOf(v) {
      if (v.issue) return v.issue.text;
      if (v.rawStatus === 'paused' && v.legs.length) return `paused holding ${plural(v.legs.length, 'leg')}`;
      if (v.streak >= 3) return `losing streak ${v.streak}`;
      if (v.young) return 'too new to judge';
      return '';
    }

    const set = patch => { st.set({ page: 0, ...patch }); paint(); };
    const offActs = actions(main, {
      new: () => A.newDeployment(),
      lab: () => set({ lab: !st.get().lab }),
      showF: () => set({ showF: !st.get().showF }),
      dense: () => set({ dense: !st.get().dense }),
      clearStrat: () => go('deployments'),
      clearAll: () => { set({ q: '', st: 'all', mode: 'all', idx: 'all', tag: 'all' }); if (strat !== 'all') go('deployments'); },
      sort: d => { const s = st.get(); const k = d.k; set({ sk: k, sd: s.sk === k ? -s.sd : (typeof SK[k]({ v: book.deployments()[0] || {}, x: {} }) === 'string' ? 1 : -1) }); },
      prev: () => { const s = st.get(); if (s.page > 0) { st.set({ page: s.page - 1 }); paint(); } },
      next: () => { st.set({ page: st.get().page + 1 }); paint(); },
      sel: d => { sel.has(d.id) ? sel.delete(d.id) : sel.add(d.id); paint(); },
      selPage: () => {
        const rows = [...main.querySelectorAll('[data-act="sel"]')].map(b => b.dataset.id);
        const allOn = rows.every(id => sel.has(id));
        rows.forEach(id => (allOn ? sel.delete(id) : sel.add(id)));
        paint();
      },
      clearSel: () => { sel.clear(); paint(); },
      primary: d => { const v = book.find(d.id); if (v) A.primary(v)[1](); },
      menu: d => { menuOpen = menuOpen === d.id ? null : d.id; paint(); },
      menuItem: (d, el) => {
        menuOpen = null;
        const v = book.find(d.id);
        const item = v && A.menuItems(v)[+d.i];
        paint();
        if (!item) return;
        if (item.off) return say(item.h);
        item.run();
      },
      bulkPause: (d, el) => (el.dataset.reason ? say(el.dataset.reason) : A.pause(chosen())),
      bulkResume: (d, el) => (el.dataset.reason ? say(el.dataset.reason) : A.resume(chosen())),
      bulkClose: (d, el) => (el.dataset.reason ? say(el.dataset.reason) : A.closeLegs(chosen())),
      bulkTag: () => A.addTag(chosen()),
      bulkCompare: (d, el) => (el.dataset.reason ? say(el.dataset.reason) : go(`compare?ids=${[...sel].join(',')}`)),
    });
    const chosen = () => [...sel].map(id => book.find(id)).filter(Boolean);
    // Filter chips: data-act="f:<key>"
    const offChips = on(main, 'click', '[data-act^="f:"]', (e, el) => set({ [el.dataset.act.slice(2)]: el.dataset.v }));
    const offSearch = on(main, 'input', '[data-search]', (e, el) => {
      st.set({ q: el.value, page: 0 });
      const pos = el.selectionStart;
      paint();
      const n = $('[data-search]', main);
      n.focus(); n.setSelectionRange(pos, pos);
    });
    const offDoc = on(document, 'click', 'body', e => {
      if (menuOpen && !e.target.closest('.menu') && !e.target.closest('[data-act="menu"]')) { menuOpen = null; paint(); }
    });
    paint();
    const offs = [listen('data', paint), listen('ticks', () => {
      book.deployments().forEach(v => {
        if (v.rawStatus === 'stopped') return;
        const x = book.live(v);
        main.querySelectorAll(`[data-today="${v.id}"]`).forEach(el => { setText(el, cycleText(x)); setTone(el, cycleTone(x)); });
      });
    })];
    return () => { offActs(); offChips(); offSearch(); offDoc(); offs.forEach(f => f()); };
  },
};
