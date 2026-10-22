// Safe templating and small DOM helpers.
//
// html`...` escapes every interpolated value unless it is itself the
// result of html`` / raw(). Arrays are joined; null/undefined/false
// render as nothing, so `${cond && html`...`}` works. Inside an ARIA
// state (aria-pressed="${x}" and the like) false is written "false":
// an empty value there means "not a toggle", not "off".

class Safe {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}
export const raw = s => new Safe(String(s));
export function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function part(v) {
  if (v == null || v === false) return '';
  if (v instanceof Safe) return v.s;
  if (Array.isArray(v)) return v.map(part).join('');
  return esc(v);
}
const ARIA_STATE = /aria-(?:pressed|checked|selected|expanded)="$/;
export function html(strings, ...vals) {
  let out = strings[0];
  for (let i = 0; i < vals.length; i++) {
    const v = vals[i] === false && ARIA_STATE.test(strings[i]) ? 'false' : vals[i];
    out += part(v) + strings[i + 1];
  }
  return new Safe(out);
}
export function mount(el, content) {
  el.innerHTML = String(content);
  return el;
}
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// Delegated events: on(root, 'click', '[data-act]', (e, el) => ...).
// Returns an unsubscribe function.
export function on(root, type, sel, fn, opts) {
  const h = e => {
    const el = e.target.closest && e.target.closest(sel);
    if (el && root.contains(el)) fn(e, el);
  };
  root.addEventListener(type, h, opts);
  return () => root.removeEventListener(type, h, opts);
}

// One handler table per page: data-act="name" (+ data-id etc.) → fn.
export function actions(root, table) {
  return on(root, 'click', '[data-act]', (e, el) => {
    const fn = table[el.dataset.act];
    if (!fn) return;
    const link = e.target.closest('a[href]');
    if (link && link !== el && el.contains(link)) return;   // a link inside a clickable row goes where it says
    if (el.getAttribute('aria-disabled') === 'true' && !el.dataset.reason) return;
    e.preventDefault();
    fn(el.dataset, el, e);
  });
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// Swap a node's text only when it changed (live ticks hit this often).
export function setText(el, text) {
  if (el && el.textContent !== text) el.textContent = text;
}
export function setTone(el, cls) {
  if (!el) return;
  el.classList.remove('pos', 'neg', 'flat');
  el.classList.add(cls);
}

export function download(name, text, type = 'text/csv') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
