// New deployment — four steps: strategy (or copy an existing one), name
// and capital, settings with defaults and help, review with "Create
// paused" or "Start now". A full-screen overlay; Escape cancels.

import { api } from '../core/api.js';
import { html, mount, $ } from '../core/dom.js';
import { inr, inrS, plural } from '../core/format.js';
import { go } from '../core/router.js';
import * as book from '../data/book.js';
import { isLive } from '../data/live.js';
import { INDICES, indexByName } from '../data/config.js';
import { fieldsFor, renderFields, bindFields, validate, toConfig } from '../ui/fields.js';
import { say } from '../ui/toast.js';

const STEPS = ['Strategy', 'Basics', 'Settings', 'Review'];
const INDEX_KEYS = ['instrument', 'options_underlying'];

// Point a config at an index: the index name keys, the signal token and
// symbol, but only keys the strategy actually has.
function withIndex(cfg, ix) {
  const i = indexByName(ix);
  if (!i) return cfg;
  const out = { ...cfg };
  INDEX_KEYS.forEach(k => { if (k in out) out[k] = ix; });
  if ('instrument_tokens' in out) out.instrument_tokens = [i.token];
  if ('symbol' in out) out.symbol = i.symbol;
  return out;
}
const supportsIndex = s => s && INDEX_KEYS.some(k => k in s.default_config);

export function openWizard({ strategy, copy } = {}) {
  const el = document.createElement('div');
  el.className = 'wiz';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.setAttribute('aria-label', 'New deployment');
  document.body.appendChild(el);
  document.body.style.overflow = 'hidden';

  const w = { step: 0, strat: null, name: '', mode: 'positional', idx: 'NIFTY', cap: '500000', cfg: {}, start: 'paused', copy: null, busy: false, error: '',
    notes: '', presets: null, presetsFor: null, psave: null, pdel: null, perr: '' };
  const pick = (name, cfg) => {
    const s = book.strategy(name);
    w.strat = name;
    w.mode = s ? s.mode : 'positional';
    w.idx = s && s.index && indexByName(s.index) ? s.index : 'NIFTY';
    w.cfg = { ...(s ? s.default_config : {}), ...(cfg || {}) };
  };
  if (copy) {
    pick(copy.strategy, copy.raw.config);
    Object.assign(w, { step: 1, name: `${copy.name} copy`, mode: copy.modeKey, idx: indexByName(copy.index) ? copy.index : w.idx, cap: String(copy.cap), copy: copy.name });
  } else if (strategy) { pick(strategy); w.step = 1; }

  const close = () => {
    off(); el.remove(); document.body.style.overflow = '';
    document.removeEventListener('keydown', onKey); window.removeEventListener('hashchange', close);
  };
  const onKey = e => { if (e.key === 'Escape' && !w.busy) close(); };
  document.addEventListener('keydown', onKey);
  window.addEventListener('hashchange', close);   // Back / any navigation closes it

  function errs() {
    const all = book.deployments();
    const s = book.strategy(w.strat);
    // The index picked in Basics sets these; don't ask twice.
    const fields = fieldsFor(s, w.cfg).filter(f => !(supportsIndex(s) && [...INDEX_KEYS, 'instrument_tokens', 'symbol'].includes(f.key)));
    const nameErr = !w.name.trim() ? 'Give it a name you’ll recognise on the Desk.' : all.some(v => v.name.toLowerCase() === w.name.trim().toLowerCase()) ? 'Another deployment already has this name.' : '';
    const capErr = !(+w.cap >= 10000) ? 'At least ₹10,000.' : +w.cap > 5e8 ? 'Above ₹50 Cr. Check the amount.' : '';
    const fieldErr = validate(fields, w.cfg);
    return { s, fields, nameErr, capErr, fieldErr };
  }
  function canNext() {
    const e = errs();
    return [!!w.strat, !e.nameErr && !e.capErr, !Object.keys(e.fieldErr).length, true][w.step];
  }

  // Saved presets for this strategy (same store as classic's Deploy modal).
  function loadPresets() {
    if (!w.strat || w.presetsFor === w.strat) return;
    const name = w.strat;
    w.presetsFor = name; w.presets = null;
    api.presets(name).then(r => { if (w.strat === name) { w.presets = r || []; paint(); } })
      .catch(() => { if (w.strat === name) { w.presets = []; paint(); } });
  }
  function currentConfig() {
    const e = errs();
    return supportsIndex(e.s) ? withIndex({ ...w.cfg, ...toConfig(e.fields, w.cfg) }, w.idx) : toConfig(e.fields, w.cfg);
  }
  function presetBar() {
    const P = w.presets;
    return html`<div class="preset-bar" style="max-width:760px">
      <div class="row wrap" style="gap:8px">
        <b class="small">Presets</b>
        ${P === null ? html`<span class="tiny mute">Loading…</span>` : !P.length ? html`<span class="tiny mute">None saved for this strategy yet.</span>` : P.map(x => w.pdel === x.id
          ? html`<span class="chip on">Delete “${x.preset_name}”? <button type="button" class="b" data-pdel-yes="${x.id}">Delete</button> · <button type="button" data-pdel-no>Keep</button></span>`
          : html`<span class="chip preset-chip"><button type="button" data-preset="${x.id}" title="Load these settings">${x.preset_name}</button><button type="button" class="mute" data-pdel="${x.id}" aria-label="Delete preset ${x.preset_name}">×</button></span>`)}
        <span class="grow"></span>
        ${w.psave === null ? html`<button type="button" class="btn btn-sm" data-psave>Save as preset…</button>` : ''}
      </div>
      ${w.psave !== null ? html`<div class="row wrap" style="gap:8px;padding-top:10px">
        <input class="input" data-w="psave" value="${w.psave}" placeholder="Preset name, e.g. Wide strikes" autocomplete="off" style="flex:1;min-width:180px">
        <button type="button" class="btn btn-sm btn-primary" data-psave-go>Save</button><button type="button" class="btn btn-sm btn-quiet" data-psave-no>Cancel</button></div>` : ''}
      ${w.perr ? html`<div class="err" role="alert" style="margin-top:8px">${w.perr}</div>` : ''}
    </div>`;
  }

  // Half a step for being on it, the whole step once it can move on.
  const pctDone = () => Math.round((w.step + (canNext() ? 1 : 0.5)) / STEPS.length * 100);

  function paint() {
    if (w.step === 2) loadPresets();
    const e = errs(), s = e.s;
    const ok = canNext();
    const titles = ['Pick a strategy', 'Name and capital', 'Settings', 'Review and deploy'];
    const subs = [
      w.copy ? `Copying ${w.copy}.` : 'Each strategy trades a fixed rule set. You choose the index, capital and limits.',
      'The name shows on the Desk, in alerts and in push notifications.',
      'Defaults come from the strategy. Every field explains what it controls.',
      'Nothing trades until the strategy’s own entry rules fire, and only while Kite is connected.',
    ];
    const block = ok ? '' : ['Pick a strategy to continue.', e.nameErr || e.capErr, 'Fix the highlighted fields.', ''][w.step];
    const review = [
      ['Strategy', s ? `${s.label} · ${s.name}` : w.strat, 0, false], ['Name', w.name, 1, false], ['Mode', w.mode === 'intraday' ? 'Intraday' : 'Positional', 1, false],
      ...(w.notes.trim() ? [['Notes', w.notes.trim(), 1, false]] : []),
      ...(supportsIndex(s) ? [['Index', w.idx, 1, true]] : []), ['Capital', inr(+w.cap || 0, { sign: false }), 1, true],
      ...e.fields.filter(f => f.type !== 'json').slice(0, 8).map(f => [f.label, f.type === 'bool' ? (w.cfg[f.key] ? 'On' : 'Off') : `${f.prefix || ''}${w.cfg[f.key] ?? '—'}${f.suffix ? ` ${f.suffix}` : ''}`, 2, true]),
    ];
    mount(el, html`
      <aside class="wiz-rail hide-phone">
        <div class="wordmark" style="padding-bottom:20px">New deployment</div>
        <div class="wiz-steps">${STEPS.map((l, i) => {
          const state = i < w.step ? 'done' : i === w.step ? 'on' : 'next';
          const fill = i < w.step ? 100 : i === w.step && ok ? 50 : 0;
          const lots = w.cfg.lots_per_trade;
          const hint = [s ? s.label : 'Not picked yet', w.name || (i <= w.step ? 'Name, index, capital' : ''),
            w.step > 2 ? `${e.fields.length} settings${lots ? ` · ${plural(+lots, 'lot')}` : ''}` : i <= w.step ? 'Entry, exits, limits' : '',
            i === w.step ? 'Check and deploy' : ''][i];
          return html`<button type="button" class="wiz-step ${state}" data-step="${i}" ${i < w.step ? '' : 'disabled'} ${i === w.step ? html`aria-current="step"` : ''}>
            <span class="wiz-dot"><span class="mono">${i < w.step ? '✓' : i + 1}</span>${i < STEPS.length - 1 ? html`<span class="wiz-line"><i style="height:${fill}%"></i></span>` : ''}</span>
            <span class="col wiz-lab" style="gap:0"><b>${l}</b><span class="tiny mute ell">${hint}</span></span></button>`;
        })}</div>
        <span class="grow"></span>
        <button type="button" class="b mute" data-close>Cancel and discard</button>
      </aside>
      <div class="wiz-main">
        <div class="wiz-prog" role="progressbar" aria-label="Progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pctDone()}"><i data-prog style="width:${pctDone()}%"></i>${STEPS.map(() => html`<span></span>`)}</div>
        <div class="wiz-body">
          <div class="wiz-stepline"><span>STEP ${w.step + 1} OF 4<span class="only-phone-in"> · ${STEPS[w.step].toUpperCase()}</span></span><i></i><span data-pct>${pctDone()}% done</span>
            <span class="grow"></span><button type="button" class="b mute only-phone-in" data-close style="letter-spacing:0;font-family:var(--f-body)">Cancel</button></div>
          <h1 class="t-h1" style="font-size:36px;padding-bottom:6px">${titles[w.step]}</h1>
          <p class="mute" style="margin:0 0 24px;max-width:720px">${subs[w.step]}</p>
          ${w.step === 0 ? html`
            <div class="str-grid">${book.strategies().filter(x => x.enabled !== false).map(x => html`
              <button type="button" class="wiz-str ${w.strat === x.name ? 'on' : ''}" data-strat="${x.name}">
                <span class="row base"><b class="t-h4">${x.label}</b><span class="tiny b mute">${x.mode === 'intraday' ? 'Intraday' : 'Positional'}</span></span>
                <span class="small mute pretty">${x.description}</span></button>`)}</div>
            <h2 class="t-h4" style="padding-top:28px">Or start from an existing deployment</h2>
            <div class="row wrap" style="gap:6px;padding-top:10px">${book.deployments().slice(0, 14).map(v => html`<button type="button" class="btn btn-sm" data-copy="${v.id}">${v.name}</button>`)}</div>` : ''}
          ${w.step === 1 ? html`
            <div class="stack" style="gap:22px;max-width:640px">
              <label class="col" style="gap:6px"><b>Name</b><input class="input ${e.nameErr && w.name ? 'bad' : ''}" style="height:44px;font-size:16px" data-w="name" value="${w.name}" placeholder="e.g. Nifty Strangle D" autocomplete="off">
                <span class="hint ${e.nameErr && w.name ? 'bad' : ''}">${e.nameErr || 'Looks good.'}</span></label>
              <label class="col" style="gap:6px"><b>Notes <span class="mute" style="font-weight:500">· optional</span></b>
                <textarea class="input" rows="2" style="height:auto;padding:10px 12px;font-size:15px;resize:vertical" data-w="notes" placeholder="Why it exists, what you’re testing">${w.notes}</textarea></label>
              <div class="col" style="gap:6px"><b>Mode</b><span class="mute">${s ? s.label : 'This strategy'} runs ${w.mode === 'intraday' ? 'intraday: it squares off every day.' : 'positionally: it holds across days until its exit rules fire.'}</span></div>
              ${supportsIndex(s) ? html`<div class="col" style="gap:6px"><b>Index</b><div class="seg lg" role="group">${INDICES.map(i => html`
                <button type="button" class="${w.idx === i.n ? 'on' : ''}" data-idx="${i.n}">${i.n}</button>`)}</div>
                <span class="hint">Sets the strategy’s index and signal instrument. Check the strategy supports it.</span></div>` : ''}
              <div class="col" style="gap:6px"><b>Capital</b>
                <div class="row wrap" style="gap:8px"><div class="input-group lg ${e.capErr ? 'bad' : ''}" style="width:240px"><span>₹</span><input data-w="cap" value="${w.cap}" inputmode="numeric" aria-label="Capital"></div>
                  ${[2e5, 5e5, 10e5, 20e5].map(c => html`<button type="button" class="chip" data-cap="${c}">${inrS(c, { sign: false })}</button>`)}</div>
                <span class="hint ${e.capErr ? 'bad' : ''}">${e.capErr || `${inrS(+w.cap, { sign: false })}. Returns, drawdowns and the leaderboard are measured against this.`}</span></div>
            </div>` : ''}
          ${w.step === 2 ? presetBar() : ''}
          ${w.step === 2 ? html`<div style="max-width:760px" data-fields>${renderFields(e.fields, w.cfg, { editable: true, errors: e.fieldErr })}</div>` : ''}
          ${w.step === 3 ? html`
            <div class="grid-side" style="gap:48px;max-width:1100px">
              <div class="rule">${review.map(([l, v, st, mono]) => html`<div class="review-row"><span class="mute">${l}</span><b class="${mono ? 'mono' : ''}" style="font-weight:600">${v}</b>
                <button type="button" class="small b right" data-step="${st}">Edit</button></div>`)}</div>
              <div class="stack" style="gap:10px">
                <b style="font-size:16px">When should it start?</b>
                ${[['paused', 'Create paused', 'Nothing runs until you press Resume. Good for checking settings first.'],
                  ['now', 'Start now', isLive() ? 'Runs from its next entry window.' : 'Starts running now; it can only trade once Kite is connected.']].map(([k, l, h]) => html`
                  <button type="button" class="start-opt ${w.start === k ? 'on' : ''}" data-start="${k}" aria-pressed="${w.start === k}"><b>${l}</b><span class="small mute">${h}</span></button>`)}
                ${w.error ? html`<div class="err" role="alert">${w.error}</div>` : ''}
              </div>
            </div>` : ''}
        </div>
        <footer class="wiz-foot">
          <button type="button" class="btn" data-back ${w.step > 0 ? '' : 'disabled'} style="${w.step > 0 ? '' : 'opacity:.3'}">‹ Back</button>
          <span class="grow"></span>
          <span class="err hide-phone">${block}</span>
          <button type="button" class="btn ${ok ? 'btn-primary' : ''}" data-next ${ok ? '' : html`aria-disabled="true"`}>${w.busy ? 'Creating…' : w.step === 3 ? (w.start === 'now' ? 'Deploy and start' : 'Create deployment') : 'Continue'}</button>
        </footer>
      </div>`);
  }

  async function deploy() {
    const e = errs();
    w.busy = true; w.error = ''; paint();
    try {
      const cfg = currentConfig();
      const r = await api.create({ deployment_name: w.name.trim(), strategy_name: w.strat, mode: w.mode, initial_capital: +w.cap, config: cfg, start: w.start, ...(w.notes.trim() ? { notes: w.notes.trim() } : {}) });
      await book.load(['deployments', 'session']);
      close();
      go(`d/${r.id}`);
      say(`${w.name.trim()} created${r.status === 'active' ? ' and running.' : ', paused. Resume it when ready.'}`);
    } catch (err) {
      w.busy = false; w.error = err.message; paint();
    }
  }

  const offFields = bindFields(el, (k, v, rerender) => { w.cfg = { ...w.cfg, [k]: v }; if (rerender) paint(); else refreshFooter(); });
  function refreshFooter() {
    const ok = canNext(), btn = $('[data-next]', el);
    if (btn) { btn.classList.toggle('btn-primary', ok); btn.setAttribute('aria-disabled', String(!ok)); }
    const bar = $('[data-prog]', el), lab = $('[data-pct]', el), line = $('.wiz-step.on .wiz-line > i', el);
    if (bar) bar.style.width = `${pctDone()}%`;
    if (lab) lab.textContent = `${pctDone()}% done`;
    if (line) line.style.height = ok ? '50%' : '0%';
  }
  const onInput = ev => {
    const t = ev.target.closest('[data-w]');
    if (!t) return;
    w[t.dataset.w] = t.dataset.w === 'cap' ? t.value.replace(/[^\d]/g, '') : t.value;
    if (t.dataset.w === 'psave' || t.dataset.w === 'notes') return;   // no repaint while typing free text
    const pos = t.selectionStart, k = t.dataset.w;
    paint();
    const n = $(`[data-w="${k}"]`, el);
    if (n) { n.focus(); try { n.setSelectionRange(pos, pos); } catch (e) {} }
  };
  const onClick = ev => {
    const t = ev.target.closest('button');
    if (!t || w.busy) return;
    if (t.hasAttribute('data-close')) return close();
    if (t.dataset.strat) { pick(t.dataset.strat); w.step = 1; return paint(); }
    if (t.dataset.copy) {
      const v = book.find(t.dataset.copy);
      pick(v.strategy, v.raw.config);
      Object.assign(w, { step: 1, name: `${v.name} copy`, mode: v.modeKey, idx: indexByName(v.index) ? v.index : w.idx, cap: String(v.cap), copy: v.name });
      return paint();
    }
    if (t.dataset.preset) {
      const x = (w.presets || []).find(q => q.id === t.dataset.preset);
      if (x) { const st = book.strategy(w.strat); w.cfg = { ...(st ? st.default_config : {}), ...x.config }; w.perr = ''; say(`Loaded preset “${x.preset_name}”.`); }
      return paint();
    }
    if (t.dataset.pdel) { w.pdel = t.dataset.pdel; return paint(); }
    if (t.hasAttribute('data-pdel-no')) { w.pdel = null; return paint(); }
    if (t.dataset.pdelYes) {
      const id = t.dataset.pdelYes, name = w.strat;
      api.deletePreset(name, id).then(() => { w.presets = (w.presets || []).filter(q => q.id !== id); w.pdel = null; paint(); say('Preset deleted.'); })
        .catch(err => { w.perr = err.message; w.pdel = null; paint(); });
      return;
    }
    if (t.hasAttribute('data-psave')) { w.psave = ''; w.perr = ''; paint(); const n = $('[data-w=psave]', el); if (n) n.focus(); return; }
    if (t.hasAttribute('data-psave-no')) { w.psave = null; w.perr = ''; return paint(); }
    if (t.hasAttribute('data-psave-go')) {
      const nm = (w.psave || '').trim();
      if (!nm) { w.perr = 'Name the preset.'; return paint(); }
      if (Object.keys(errs().fieldErr).length) { w.perr = 'Fix the highlighted fields before saving.'; return paint(); }
      api.savePreset(w.strat, nm, currentConfig()).then(r => { w.presets = [...(w.presets || []), r]; w.psave = null; w.perr = ''; paint(); say(`Saved preset “${nm}”.`); })
        .catch(err => { w.perr = err.message; paint(); });
      return;
    }
    if (t.dataset.idx) { w.idx = t.dataset.idx; return paint(); }
    if (t.dataset.cap) { w.cap = t.dataset.cap; return paint(); }
    if (t.dataset.start) { w.start = t.dataset.start; return paint(); }
    if (t.dataset.step != null && !t.disabled) { w.step = +t.dataset.step; return paint(); }
    if (t.hasAttribute('data-back') && w.step > 0) { w.step--; return paint(); }
    if (t.hasAttribute('data-next')) {
      if (!canNext()) return paint();
      if (w.step === 3) return deploy();
      w.step++;
      return paint();
    }
  };
  el.addEventListener('input', onInput);
  el.addEventListener('click', onClick);
  const off = () => { offFields(); el.removeEventListener('input', onInput); el.removeEventListener('click', onClick); };
  paint();
  const first = $('input, button', el);
  if (first) first.focus();
}
