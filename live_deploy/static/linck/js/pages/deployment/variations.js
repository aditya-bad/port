// Variations: this deployment and the ones run beside it with other
// settings (app/variations.py). Side by side, their P&L over each other,
// what differs, Make primary, and adding one: its label and settings, its
// own capital, and whether it starts fresh or continues the current cycle.

import { api } from '../../core/api.js';
import { html, mount, actions, $ } from '../../core/dom.js';
import { inr, inrFit, pct, dShort, plural, tone } from '../../core/format.js';
import { pnlOf } from '../../core/basis.js';
import { listen } from '../../core/store.js';
import { href } from '../../core/router.js';
import * as book from '../../data/book.js';
import { rows, curves, differences, show, styleOf, MAX } from '../../data/variations.js';
import { fieldsFor, renderFields, bindFields, validate, toConfig } from '../../ui/fields.js';
import { line } from '../../ui/charts.js';
import { registerScrub } from '../../ui/tooltip.js';
import { status, seg, empty } from '../../ui/components.js';
import * as A from '../../actions.js';
import { say } from '../../ui/toast.js';

export default {
  render(el, v0) {
    let adding = false, draft = null, form = { label: '', capital: '', start: 'fresh', run: 'paused' }, saving = false, err = '';
    const fam = () => book.family(v0.id);

    function table(R) {
      return html`<div class="tbl var-tbl" role="table" data-var="table">
        <div class="tr th" role="row"><span>Deployment</span><span>Status</span><span class="num">Capital</span><span class="num">Cycle</span>
          <span class="num">Overall</span><span class="num">Return</span><span class="num">Trades</span><span class="num">Won</span><span class="num">Max drawdown</span><span></span></div>
        ${R.map(r => html`<div class="tr ${r.v.id === v0.id ? 'sel' : ''}" role="row" data-var-row="${r.v.id}">
          <span class="cell-name"><i class="key" style="background:${styleOf(r.i).color}" aria-hidden="true"></i>
            <a href="${href(`d/${r.v.id}/variations`)}"><b>${r.label}</b></a><span class="tiny mute ell">${r.primary ? r.v.name : r.v.name.split(' · ').slice(-1)[0] === r.label ? '' : r.v.name}</span></span>
          <span data-l="Status">${status(r.v.status)}</span>
          <span class="num" data-l="Capital">${inr(r.v.cap, { sign: false })}</span>
          <span class="num ${tone(r.cycle)}" data-l="Cycle">${r.cycle == null ? '—' : inrFit(r.cycle)}</span>
          <span class="num ${tone(r.overall)}" data-l="Overall">${inrFit(r.overall)}</span>
          <span class="num ${tone(r.ret)}" data-l="Return">${pct(r.ret)}</span>
          <span class="num" data-l="Trades">${r.trades}</span>
          <span class="num" data-l="Won">${r.win == null ? '—' : `${Math.round(r.win)}%`}</span>
          <span class="num" data-l="Max drawdown">${r.dd < 0 ? pct(r.dd) : '—'}</span>
          <span class="row" style="gap:6px;justify-content:flex-end">${r.primary ? html`<span class="tag">primary</span>`
            : html`<button type="button" class="btn btn-xs" data-act="promote" data-id="${r.v.id}">Make primary</button>`}</span>
        </div>`)}
      </div>`;
    }

    function chart(F) {
      const c = curves(F, pnlOf);
      if (c.days.length < 2) return html`<p class="small mute" data-var="nochart">The P&L lines appear once at least two days have closed trades.</p>`;
      registerScrub('var-curves', f => {
        const i = Math.round(f * (c.days.length - 1));
        return { t: `${dShort(new Date(c.days[i]))}\n${F.map((v, j) => `${j ? v.variationLabel || v.name : 'Primary'}  ${inr(c.series[j][i])}`).join('\n')}`,
          ys: [], cs: [] };
      });
      return html`<div class="lens-legend small">${F.map((v, j) => html`<span><i class="key" style="background:${styleOf(j).color}"></i>${j ? v.variationLabel || v.name : 'Primary'}</span>`)}</div>
        ${line({ series: F.map((v, j) => ({ values: c.series[j], color: styleOf(j).color, dash: styleOf(j).dash, width: j ? 1.6 : 2.4, label: v.name })), h: 220, key: 'var-curves', zero: true })}
        <div class="lens-axis tiny mute mono"><span>${dShort(new Date(c.days[0]))}</span><span>${dShort(new Date(c.days[c.days.length - 1]))}</span></div>`;
    }

    function diffs(F) {
      const st = book.strategy(F[0].strategy);
      const D = differences(F, st ? st.default_config : {});
      if (F.length < 2) return '';
      if (!D.length) return html`<p class="small mute">Every variation uses the primary’s settings.</p>`;
      return html`<div class="tbl var-diff" role="table" data-var="diff" style="--n:${F.length}">
        <div class="tr th" role="row"><span>Setting</span><span>Primary</span>${F.slice(1).map(v => html`<span>${v.variationLabel || v.name}</span>`)}</div>
        ${D.map(d => html`<div class="tr" role="row"><span class="mono small">${d.key}</span><span class="mono small">${show(d.primary)}</span>
          ${d.values.map(x => html`<span class="mono small ${JSON.stringify(x) !== JSON.stringify(d.primary) ? 'warn b' : 'mute'}">${show(x)}</span>`)}</div>`)}
      </div>`;
    }

    function adder(F) {
      const p = F[0], strat = book.strategy(p.strategy);
      const fields = fieldsFor(strat, p.raw.config || {});
      if (!draft) draft = { ...(strat ? strat.default_config : {}), ...(p.raw.config || {}) };
      const E = validate(fields, draft);
      const live = F.slice(1).filter(v => v.rawStatus !== 'stopped').length;
      const label = form.label.trim();
      const cap = form.capital === '' ? p.cap : Number(form.capital);
      const why = !label ? 'Give it a label: what it tries.' : !(cap > 0) ? 'Capital must be more than zero.'
        : Object.keys(E).length ? 'Fix the highlighted settings.' : live >= MAX ? `${MAX} variations already: stop one first.`
        : form.start === 'continue' && !p.legs.length ? `${p.name} holds nothing now: there’s no cycle to continue.` : '';
      return html`<div class="var-add" data-var="add">
        <div class="sect ruled"><h3 class="t-h3">Add a variation of ${p.name}</h3><span class="mute small">its own deployment, beside the primary</span></div>
        <div class="grid-side" style="gap:32px">
          <div class="stack" style="gap:14px">
            <label class="col" style="gap:6px"><b class="small">Label</b><input class="input" data-form="label" value="${form.label}" maxlength="40" placeholder="checkpoint 0.75%" aria-label="Label"></label>
            <label class="col" style="gap:6px"><b class="small">Capital</b><input class="input" data-form="capital" value="${form.capital}" inputmode="decimal" placeholder="${p.cap}" aria-label="Capital">
              <span class="tiny mute">Its own paper capital; the primary’s unless you change it.</span></label>
            <div class="col" style="gap:6px"><b class="small">Start</b>${seg([['fresh', 'Start fresh'], ['continue', 'Continue the current cycle']], form.start, 'start')}
              <span class="tiny mute">${form.start === 'continue' ? `A copy of ${p.name}’s open legs, the legs closed in this cycle and its saved state; it manages them from now under these settings.` : 'It enters by its own rules, at its next entry time.'}</span></div>
            <div class="col" style="gap:6px"><b class="small">Then</b>${seg([['paused', 'Leave paused'], ['now', 'Run now']], form.run, 'run')}</div>
            <p class="tiny mute" style="margin:0">A variation’s alerts are the critical ones only (not running, errors, lost legs); its entries and exits go to its log. Totals count the primary. Its day is checked when you ask.</p>
            ${err ? html`<div class="err" role="alert">${err}</div>` : ''}
            <div class="row" style="gap:8px"><button type="button" class="btn btn-primary" data-act="create" ${why || saving ? html`aria-disabled="true" data-reason="${why || 'Working…'}"` : ''}>${saving ? 'Adding…' : 'Add variation'}</button>
              <button type="button" class="btn btn-quiet" data-act="cancelAdd">Cancel</button></div>
          </div>
          <div data-fields>${renderFields(fields, draft, { editable: true, errors: E })}</div>
        </div>
      </div>`;
    }

    function paint() {
      const F = fam();
      if (!F.length) { mount(el, empty('Gone', 'This deployment no longer exists.')); return; }
      const R = rows(F, pnlOf, book.live);
      const p = F[0];
      const live = F.slice(1).filter(v => v.rawStatus !== 'stopped').length;
      mount(el, html`<div class="pad-x tab-body stack" style="gap:28px" data-var="tab">
        <div class="row wrap" style="gap:12px;align-items:baseline">
          <p class="small mute pretty" style="margin:0;max-width:760px">${F.length > 1
            ? `${p.name} and ${plural(F.length - 1, 'variation')}: the same strategy with other settings, each a deployment of its own on the same market. Totals count the primary.`
            : `No variations yet. A variation runs ${p.name}’s strategy with other settings beside it, on the same market, so you can see which settings do better.`}</p>
          <span class="grow"></span>
          ${p.rawStatus === 'stopped' ? '' : html`<button type="button" class="btn ${F.length > 1 ? '' : 'btn-primary'}" data-act="add" ${live >= MAX ? html`aria-disabled="true" data-reason="${MAX} variations already: stop one first."` : ''}>Add variation…</button>`}
        </div>
        ${adding ? adder(F) : ''}
        ${F.length > 1 ? html`
          ${table(R)}
          <div class="row" style="gap:8px;margin-top:-14px"><a class="btn btn-sm" href="${href(`compare?ids=${F.slice(0, 5).map(x => x.id).join(',')}`)}" data-var-compare>Compare in detail</a>
            <span class="tiny mute">every metric, whether they move together${F.length > 5 ? ' · the first 5' : ''}</span></div>
          <div><div class="sect"><h3 class="t-h3">P&L, over each other</h3><span class="mute small">cumulative, from closed trades</span></div>${chart(F)}</div>
          <div><div class="sect ruled"><h3 class="t-h3">What differs</h3><span class="mute small">only the settings a variation changed</span></div>${diffs(F)}</div>` : ''}
      </div>`);
    }

    const offFields = bindFields(el, (k, val, rerender) => {
      draft = { ...draft, [k]: val };
      if (rerender) paint();
    });
    const onInput = e => {
      const f = e.target.closest('[data-form]');
      if (!f) return;
      form = { ...form, [f.dataset.form]: f.value };
      // the button's state follows after a pause in typing, keeping the cursor where it was
      clearTimeout(onInput.t);
      onInput.t = setTimeout(() => {
        const key = f.dataset.form, pos = f.selectionStart;
        paint();
        const n = $(`[data-form="${key}"]`, el);
        if (n) { n.focus(); try { n.setSelectionRange(pos, pos); } catch (x) { /* number inputs */ } }
      }, 400);
    };
    el.addEventListener('input', onInput);
    const off = actions(el, {
      add: (d, b) => { if (b.dataset.reason) return say(b.dataset.reason); adding = true; err = ''; paint(); },
      cancelAdd: () => { adding = false; draft = null; form = { label: '', capital: '', start: 'fresh', run: 'paused' }; paint(); },
      start: d => { form = { ...form, start: d.v }; paint(); },
      run: d => { form = { ...form, run: d.v }; paint(); },
      create: async (d, b) => {
        if (b.dataset.reason) return say(b.dataset.reason);
        const p = fam()[0];
        const fields = fieldsFor(book.strategy(p.strategy), p.raw.config || {});
        saving = true; err = ''; paint();
        try {
          const body = { label: form.label.trim(), config: toConfig(fields, draft), start: form.start, run: form.run };
          if (form.capital !== '') body.initial_capital = Number(form.capital);
          const r = await api.addVariation(p.id, body);
          await book.load(['deployments', 'positions', 'book']);
          say(`Added ${r.name}${form.run === 'now' ? ', running' : ', paused'}.`);
          adding = false; draft = null; form = { label: '', capital: '', start: 'fresh', run: 'paused' };
        } catch (e) { err = e.message; }
        saving = false; paint();
      },
      promote: d => A.makePrimary(book.find(d.id)),
    });
    paint();
    const offData = listen('data', () => { if (!adding) paint(); });   // not while a variation is being written
    return () => { off(); offFields(); offData(); el.removeEventListener('input', onInput); clearTimeout(onInput.t); };
  },
};
