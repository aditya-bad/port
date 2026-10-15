// Deploy — pick a strategy, set it up, start it.
import { html, raw, mount, $, on, humanize, ICONS } from '../util.js';
import { api } from '../api.js';
import { modal, toast, toastError } from '../ui.js';
import { empty, skeleton } from '../components.js';
import { configForm } from '../configform.js';

// Strategies that hold positions overnight by design.
const POSITIONAL = new Set(['strangle_monthly_v2', 'calendar_btst', 'weekly_ema_st_spread', 'trend_credit_spread']);
const ui = { q: '', selected: null };
let catalog = null, deps = [], form = null, presets = [];

function suggestName(name) {
  const base = humanize(name);
  let n = 1, candidate = base;
  const taken = new Set(deps.map(d => d.deployment_name.toLowerCase()));
  while (taken.has(candidate.toLowerCase())) candidate = `${base} ${++n}`;
  return candidate;
}

function paint(main) {
  const q = ui.q.trim().toLowerCase();
  const list = catalog.filter(s => s.enabled !== false).filter(s => !q || `${s.name} ${s.description}`.toLowerCase().includes(q));
  const sel = catalog.find(s => s.name === ui.selected);
  const running = name => deps.filter(d => d.strategy_name === name && d.status !== 'stopped').length;
  mount(main, html`
    <div class="page-head">
      <div><h1>Deploy a strategy</h1><div class="sub">Every deployment paper-trades on live prices with its own capital, positions and history.</div></div>
    </div>
    ${sel ? '' : html`<label class="search" style="max-width:360px;margin-bottom:14px;display:block"><span class="sr-only">Search strategies</span>${raw(ICONS.search)}<input class="input" data-act="q" placeholder="Search strategies" value="${ui.q}"></label>`}
    ${sel ? html`
      <div class="crumb"><a href="#/deploy" data-act="back">${raw(ICONS.back)} All strategies</a></div>
      <div class="grid two" style="align-items:start">
        <section class="card">
          <div class="card-head"><div><h2>${humanize(sel.name)}</h2><div class="sub mono small">${sel.name}</div></div>
            ${presets.length ? html`<select class="input" style="width:auto" data-act="preset" aria-label="Load a preset"><option value="">Load a preset…</option>${presets.map(p => html`<option value="${p.id}">${p.preset_name}</option>`)}</select>` : ''}</div>
          <div class="card-body stack">
            <div class="form-grid">
              <label class="field"><span>Deployment name</span><input class="input" id="dName" value="${suggestName(sel.name)}" required></label>
              <label class="field"><span>Mode</span><select class="input" id="dMode">
                <option value="intraday" ${POSITIONAL.has(sel.name) ? '' : 'selected'}>Intraday — flat by close</option>
                <option value="positional" ${POSITIONAL.has(sel.name) ? 'selected' : ''}>Positional — holds overnight</option></select></label>
              <label class="field"><span>Capital (₹)</span><input class="input" id="dCap" type="number" min="1" step="1000" value="100000"></label>
            </div>
            <label class="field"><span>Notes <span class="muted">(optional)</span></span><input class="input" id="dNotes" placeholder="What are you testing with this one?"></label>
            <div><div class="label" style="margin:4px 0 10px">Configuration</div><div id="dCfg"></div></div>
          </div>
          <div class="modal-foot" style="border-top:1px solid var(--line)">
            <button class="btn" data-act="save-preset">Save as preset</button>
            <button class="btn primary" data-act="deploy">Deploy</button>
          </div>
        </section>
        <section class="card card-pad">
          <div class="label" style="margin-bottom:8px">About this strategy</div>
          <p class="sub" style="margin:0;white-space:pre-line">${sel.description}</p>
          ${running(sel.name) ? html`<p class="small" style="margin:14px 0 0">You already run <b>${running(sel.name)}</b> deployment${running(sel.name) === 1 ? '' : 's'} of this strategy.</p>` : ''}
        </section>
      </div>`
    : list.length ? html`<div class="cards">${list.map(s => html`<button class="scard" data-pick="${s.name}">
        <div class="row"><div class="cell-title">${humanize(s.name)}</div>${running(s.name) ? html`<span class="badge active">${running(s.name)} running</span>` : ''}</div>
        <div class="desc">${s.description}</div>
        <div class="row"><span class="badge outline">${POSITIONAL.has(s.name) ? 'Positional' : 'Intraday'}</span><span class="small muted">${Object.keys(s.default_config || {}).length} settings</span></div></button>`)}</div>`
      : html`<div class="card">${empty('No strategies match', '')}</div>`}`);
  if (sel) form = configForm($('#dCfg', main), { defaults: sel.default_config || {}, values: {} });
}

async function pick(main, name) {
  ui.selected = name;
  presets = await api.presets(name).catch(() => []);
  paint(main);
  window.scrollTo(0, 0);
}

export default {
  title: 'Deploy',
  async render(main, params) {
    mount(main, skeleton(3));
    [catalog, deps] = await Promise.all([api.strategies(), api.deployments()]);
    ui.selected = null;
    if (params.tab && catalog.some(s => s.name === params.tab)) await pick(main, params.tab);
    else paint(main);
    const offs = [
      on(main, 'click', '[data-pick]', (e, b) => { location.hash = `#/deploy/${b.dataset.pick}`; }),
      on(main, 'input', '[data-act="q"]', (e, i) => { ui.q = i.value; const p = i.selectionStart; paint(main); const n = $('[data-act="q"]', main); n.focus(); n.setSelectionRange(p, p); }),
      on(main, 'change', '[data-act="preset"]', (e, s) => {
        const p = presets.find(x => x.id === s.value);
        if (p) { form.setValues(p.config); toast(`Loaded preset “${p.preset_name}”`); }
      }),
      on(main, 'click', '[data-act="save-preset"]', async () => {
        let cfg;
        try { cfg = form.read(); } catch (e) { toastError(e, 'Check the configuration'); return; }
        await modal({
          title: 'Save as preset', description: 'Reuse this configuration the next time you deploy this strategy.',
          body: html`<label class="field"><span>Preset name</span><input class="input" id="pName" placeholder="e.g. Wider bands, 2 lots"></label>`,
          actions: [{ label: 'Cancel', value: null }, {
            label: 'Save preset', kind: 'primary', handler: async scrim => {
              const name = $('#pName', scrim).value.trim();
              if (!name) { toastError(new Error('Give the preset a name'), 'Not saved'); return false; }
              await api.savePreset(ui.selected, name, cfg);
              presets = await api.presets(ui.selected).catch(() => presets);
              toast('Preset saved');
              return true;
            },
          }],
        });
      }),
      on(main, 'click', '[data-act="deploy"]', async (e, b) => {
        const name = $('#dName', main).value.trim();
        const cap = Number($('#dCap', main).value);
        if (!name) { toastError(new Error('Give the deployment a name'), 'Not deployed'); return; }
        if (!(cap > 0)) { toastError(new Error('Capital must be more than zero'), 'Not deployed'); return; }
        let cfg;
        try { cfg = form.read(); } catch (err) { toastError(err, 'Check the configuration'); return; }
        b.disabled = true;
        b.textContent = 'Deploying…';
        try {
          const d = await api.createDeployment({
            deployment_name: name, strategy_name: ui.selected, mode: $('#dMode', main).value,
            initial_capital: cap, config: cfg, notes: $('#dNotes', main).value.trim() || null,
          });
          toast(`${name} deployed`, 'It starts trading on its next signal.');
          location.hash = `#/s/${d.id}`;
        } catch (err) {
          toastError(err, 'Not deployed');
          b.disabled = false;
          b.textContent = 'Deploy';
        }
      }),
    ];
    return () => offs.forEach(f => f());
  },
};
