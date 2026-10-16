// Dialogs. The kicker says how serious it is (CONFIRM, EMERGENCY,
// IRREVERSIBLE …); order-placing actions list the exact orders;
// destructive ones arm only after typed confirmation.
//
// openDialog({
//   kicker, hot, tone: 'warn'|'good', title, lines: [text],
//   orders: [{ t, v, tone }], check: { label, value }, chips: { options, value },
//   input: { label, placeholder, value, mono }, menu: [{ l, h, run }], spin,
//   okLabel, armed: state => bool, onOk: async state => (true to close),
//   cancelLabel, alt: { label, run }, render: (state) => extra html
// })

import { html, mount, esc } from '../core/dom.js';

let current = null;

export function closeDialog() {
  if (!current) return;
  current.scrim.remove();
  document.removeEventListener('keydown', current.onKey);
  const back = current.returnFocus;
  current = null;
  if (back && back.focus) back.focus();
}

export function openDialog(spec) {
  closeDialog();
  const state = {
    text: spec.input ? spec.input.value || '' : '',
    checked: spec.check ? !!spec.check.value : false,
    pick: spec.chips ? spec.chips.value || null : null,
    busy: false, error: '',
  };
  const scrim = document.createElement('div');
  scrim.className = 'scrim';
  document.body.appendChild(scrim);
  const onKey = e => { if (e.key === 'Escape' && !state.busy) closeDialog(); };
  document.addEventListener('keydown', onKey);
  current = { scrim, onKey, returnFocus: document.activeElement };

  const armed = () => !state.busy && (spec.armed ? spec.armed(state) : true);
  function paint(keepFocus) {
    const ok = armed();
    mount(scrim, html`
      <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dlgTitle">
        <header class="${spec.tone || ''}">
          ${spec.kicker && html`<div class="kicker ${spec.hot ? 'hot' : ''}">${spec.kicker}</div>`}
          <h2 id="dlgTitle">${spec.title}</h2>
        </header>
        <div class="body">
          ${(spec.lines || []).filter(Boolean).map(l => html`<p>${l}</p>`)}
          ${spec.menu && html`<div class="menu-list">${spec.menu.map((m, i) => html`
            <button type="button" data-menu="${i}"><span><b>${m.l}</b><small>${m.h || ''}</small></span><span class="mute">›</span></button>`)}</div>`}
          ${spec.spin && html`<div class="row" style="padding:10px 0"><span class="spinner"></span><b>${spec.spin}</b></div>`}
          ${spec.orders && spec.orders.length ? html`<div class="orders">${spec.orders.map(o => html`<div><span>${o.t}</span><span class="${o.tone || ''}">${o.v}</span></div>`)}</div>` : ''}
          ${spec.render && spec.render(state)}
          ${spec.check && html`<button type="button" class="check" role="checkbox" aria-checked="${state.checked}" data-check><i></i>${spec.check.label}</button>`}
          ${spec.chips && html`<div class="chips">${spec.chips.options.map(o => html`
            <button type="button" class="chip strong ${state.pick === o ? 'on' : ''}" data-pick="${o}">${o}</button>`)}</div>`}
          ${spec.input && html`<label class="col" style="gap:6px"><b class="small">${spec.input.label}</b>
            <input class="input" data-input style="height:44px;${spec.input.mono === false ? '' : 'font-family:var(--f-mono);font-size:15px'}"
              placeholder="${spec.input.placeholder || ''}" value="${state.text}" autocomplete="off" ${spec.input.type ? html`type="${spec.input.type}"` : ''}></label>`}
          ${state.error && html`<div class="err" role="alert">${state.error}</div>`}
        </div>
        <footer>
          ${spec.alt && html`<button type="button" class="btn" style="margin-right:auto" data-alt>${spec.alt.label}</button>`}
          <button type="button" class="btn btn-quiet" data-cancel>${spec.cancelLabel || 'Cancel'}</button>
          ${spec.onOk && html`<button type="button" class="btn btn-primary" data-ok aria-disabled="${!ok}">${state.busy ? 'Working…' : spec.okLabel || 'Confirm'}</button>`}
        </footer>
      </div>`);
    const inp = scrim.querySelector('[data-input]');
    if (inp) {
      inp.addEventListener('input', () => {
        state.text = inp.value;
        if (spec.chips && state.text) state.pick = null;
        const btn = scrim.querySelector('[data-ok]');
        if (btn) btn.setAttribute('aria-disabled', String(!armed()));
        scrim.querySelectorAll('[data-pick]').forEach(b => b.classList.toggle('on', b.dataset.pick === state.pick));
      });
      inp.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
      if (keepFocus !== false) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
    } else if (keepFocus !== false) {
      const f = scrim.querySelector('[data-ok]:not([aria-disabled="true"])') || scrim.querySelector('[data-cancel]');
      if (f) f.focus();
    }
  }
  async function submit() {
    if (!spec.onOk || !armed()) return;
    state.busy = true; state.error = ''; paint(false);
    try {
      const done = await spec.onOk(state);
      if (done !== false) { closeDialog(); return; }
      state.busy = false; paint(false);
    } catch (e) {
      state.busy = false; state.error = e.message || String(e); paint(false);
    }
  }
  scrim.addEventListener('click', e => {
    if (e.target === scrim && !state.busy) return closeDialog();
    const t = e.target.closest('button');
    if (!t) return;
    if (t.hasAttribute('data-cancel')) return closeDialog();
    if (t.hasAttribute('data-ok')) return submit();
    if (t.hasAttribute('data-alt')) { closeDialog(); return spec.alt.run(); }
    if (t.hasAttribute('data-check')) { state.checked = !state.checked; return paint(false); }
    if (t.dataset.pick) { state.pick = t.dataset.pick; state.text = ''; return paint(false); }
    if (t.dataset.menu != null) { closeDialog(); return spec.menu[+t.dataset.menu].run(); }
  });
  paint();
  return { close: closeDialog, repaint: () => paint(false), state };
}
export const isOpen = () => !!current;
export { esc };
