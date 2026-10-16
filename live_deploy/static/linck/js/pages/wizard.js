// New deployment — four steps: strategy (or copy an existing one), name
// and capital, settings with defaults and help, review with "Create
// paused" or "Start now". A full-screen overlay; Escape cancels.

import { api } from '../core/api.js';
import { html, mount, $ } from '../core/dom.js';
import { inr, inrS } from '../core/format.js';
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

  const w = { step: 0, strat: null, name: '', mode: 'positional', idx: 'NIFTY', cap: '500000', cfg: {}, start: 'paused', copy: null, busy: false, error: '' };
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

  function paint() {
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
      ...(supportsIndex(s) ? [['Index', w.idx, 1, true]] : []), ['Capital', inr(+w.cap || 0, { sign: false }), 1, true],
      ...e.fields.filter(f => f.type !== 'json').slice(0, 8).map(f => [f.label, f.type === 'bool' ? (w.cfg[f.key] ? 'On' : 'Off') : `${f.prefix || ''}${w.cfg[f.key] ?? '—'}${f.suffix ? ` ${f.suffix}` : ''}`, 2, true]),
    ];
    mount(el, html`
      <aside class="wiz-rail hide-phone">
        <div class="wordmark" style="padding-bottom:20px">New deployment</div>
        ${STEPS.map((l, i) => html`<button type="button" class="wiz-step ${i === w.step ? 'on' : ''}" data-step="${i}" ${i < w.step ? '' : 'disabled'}>
          <span class="mono">${i < w.step ? '✓' : i + 1}</span><span class="col" style="gap:0"><b>${l}</b><span class="tiny mute">${[s ? s.label : '', w.name, w.step > 2 ? 'set' : '', ''][i]}</span></span></button>`)}
        <span class="grow"></span>
        <button type="button" class="b mute" data-close>Cancel and discard</button>
      </aside>
      <div class="wiz-main">
        <div class="wiz-body">
          <div class="row only-phone" style="justify-content:space-between;padding-bottom:12px"><span class="tiny mute b">Step ${w.step + 1} of 4 · ${STEPS[w.step]}</span><button type="button" class="b mute" data-close>Cancel</button></div>
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
              <div class="col" style="gap:6px"><b>Mode</b><span class="mute">${s ? s.label : 'This strategy'} runs ${w.mode === 'intraday' ? 'intraday: it squares off every day.' : 'positionally: it holds across days until its exit rules fire.'}</span></div>
              ${supportsIndex(s) ? html`<div class="col" style="gap:6px"><b>Index</b><div class="seg lg" role="group">${INDICES.map(i => html`
                <button type="button" class="${w.idx === i.n ? 'on' : ''}" data-idx="${i.n}">${i.n}</button>`)}</div>
                <span class="hint">Sets the strategy’s index and signal instrument. Check the strategy supports it.</span></div>` : ''}
              <div class="col" style="gap:6px"><b>Capital</b>
                <div class="row wrap" style="gap:8px"><div class="input-group lg ${e.capErr ? 'bad' : ''}" style="width:240px"><span>₹</span><input data-w="cap" value="${w.cap}" inputmode="numeric" aria-label="Capital"></div>
                  ${[2e5, 5e5, 10e5, 20e5].map(c => html`<button type="button" class="chip" data-cap="${c}">${inrS(c, { sign: false })}</button>`)}</div>
                <span class="hint ${e.capErr ? 'bad' : ''}">${e.capErr || `${inrS(+w.cap, { sign: false })}. Returns, drawdowns and the leaderboard are measured against this.`}</span></div>
            </div>` : ''}
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
      const cfg = supportsIndex(e.s) ? withIndex({ ...w.cfg, ...toConfig(e.fields, w.cfg) }, w.idx) : toConfig(e.fields, w.cfg);
      const r = await api.create({ deployment_name: w.name.trim(), strategy_name: w.strat, mode: w.mode, initial_capital: +w.cap, config: cfg, start: w.start });
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
  }
  const onInput = ev => {
    const t = ev.target.closest('[data-w]');
    if (!t) return;
    w[t.dataset.w] = t.dataset.w === 'cap' ? t.value.replace(/[^\d]/g, '') : t.value;
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
