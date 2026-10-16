// Toasts: one at a time, 5 seconds, optional Undo (reversible actions
// skip the dialog and offer Undo instead).

import { html, mount } from '../core/dom.js';

let el = null, timer = null;

export function say(text, undo, ms = 5000) {
  if (!el) {
    el = document.createElement('div');
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    document.body.appendChild(el);
  }
  clearTimeout(timer);
  el.className = 'toast';
  mount(el, html`<b>${text}</b>${undo ? html`<span class="undo" role="button" tabindex="0" data-undo>Undo</span>` : ''}<span class="x" role="button" tabindex="0" aria-label="Dismiss" data-x>×</span>`);
  el.hidden = false;
  el.onclick = e => {
    if (e.target.closest('[data-undo]')) { hide(); undo(); }
    else if (e.target.closest('[data-x]')) hide();
  };
  timer = setTimeout(hide, ms);
}
export function hide() {
  clearTimeout(timer);
  if (el) el.hidden = true;
}
export const fail = (e, what) => say(`${what}: ${e.message || e}`);
