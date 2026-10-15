// Interaction primitives: toasts, modals/confirm, popover menus, the
// shared chart tooltip, and the ⌘K command palette.
import { html, raw, mount, esc, $, ICONS } from './util.js';

// ── Toasts ─────────────────────────────────────────────────────────
export function toast(title, message = '', kind = 'ok', ms = 6000) {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast';
  el.setAttribute('role', kind === 'bad' ? 'alert' : 'status');
  mount(el, html`<span class="dot ${kind}"></span>
    <div><div class="toast-title">${title}</div>${message ? html`<div class="toast-msg">${message}</div>` : ''}</div>
    <button class="btn ghost sm icon" aria-label="Dismiss">${raw(ICONS.close)}</button>`);
  el.querySelector('button').onclick = () => el.remove();
  box.appendChild(el);
  if (ms) setTimeout(() => el.remove(), ms);
}
export function toastError(e, title = 'Something went wrong') {
  toast(title, e && e.message ? e.message : String(e), 'bad', 9000);
}

// ── Modal ──────────────────────────────────────────────────────────
// modal({ title, body (html), actions: [{label, value, kind}], wide, onMount })
// resolves with the clicked action's value (or null when dismissed).
let openModals = 0;
export function modal({ title, description, body = '', actions = [{ label: 'Close', value: null }], wide = false, onMount }) {
  return new Promise(resolve => {
    const scrim = document.createElement('div');
    scrim.className = 'scrim';
    mount(scrim, html`<div class="modal ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${title}">
      <div class="modal-head"><h2>${title}</h2>${description ? html`<p>${description}</p>` : ''}</div>
      ${body ? html`<div class="modal-body">${body}</div>` : ''}
      <div class="modal-foot">${actions.map((a, i) => html`<button class="btn ${a.kind || ''}" data-i="${i}">${a.label}</button>`)}</div>
    </div>`);
    const prevFocus = document.activeElement;
    const close = v => {
      scrim.remove();
      openModals--;
      document.removeEventListener('keydown', onKey);
      if (prevFocus && prevFocus.focus) prevFocus.focus();
      resolve(v);
    };
    const onKey = e => { if (e.key === 'Escape') close(null); };
    scrim.addEventListener('mousedown', e => { if (e.target === scrim) close(null); });
    scrim.querySelectorAll('.modal-foot button').forEach(b => {
      b.onclick = async () => {
        const a = actions[+b.dataset.i];
        if (a.handler) {
          b.disabled = true;
          try {
            const r = await a.handler(scrim);
            if (r === false) { b.disabled = false; return; }
            close(r === undefined ? a.value : r);
          } catch (e) { b.disabled = false; toastError(e); }
          return;
        }
        close(a.value);
      };
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(scrim);
    openModals++;
    if (onMount) onMount(scrim);
    const first = scrim.querySelector('input, select, textarea') || scrim.querySelector('.modal-foot .btn.primary, .modal-foot .btn.danger') || scrim.querySelector('.modal-foot .btn');
    if (first) first.focus();
  });
}
export const anyModalOpen = () => openModals > 0;

export function confirm({ title, description, confirmLabel = 'Confirm', danger = false }) {
  return modal({
    title, description,
    actions: [{ label: 'Cancel', value: false }, { label: confirmLabel, value: true, kind: danger ? 'danger' : 'primary' }],
  });
}

// ── Popover menu ───────────────────────────────────────────────────
// items: [{label, onClick, danger, href, sep, heading}]
let openMenu = null;
export function closeMenu() {
  if (openMenu) { openMenu.remove(); openMenu = null; }
}
export function menu(anchor, items) {
  closeMenu();
  const el = document.createElement('div');
  el.className = 'menu';
  el.setAttribute('role', 'menu');
  mount(el, html`${items.filter(Boolean).map((it, i) => it.sep ? html`<hr>` : it.heading
    ? html`<div class="menu-label">${it.heading}</div>`
    : it.href
      ? html`<a role="menuitem" href="${it.href}" class="${it.danger ? 'danger' : ''}">${it.label}</a>`
      : html`<button role="menuitem" data-i="${i}" class="${it.danger ? 'danger' : ''}">${it.label}</button>`)}`);
  const list = items.filter(Boolean);
  el.querySelectorAll('button[data-i]').forEach(b => {
    b.onclick = () => { closeMenu(); list[+b.dataset.i].onClick(); };
  });
  el.querySelectorAll('a').forEach(a => { a.onclick = () => closeMenu(); });
  document.body.appendChild(el);
  const r = anchor.getBoundingClientRect();
  const w = el.offsetWidth, h = el.offsetHeight;
  let left = Math.min(r.right - w, window.innerWidth - w - 8);
  left = Math.max(8, left);
  let top = r.bottom + 6;
  if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
  openMenu = el;
  const first = el.querySelector('button, a');
  if (first) first.focus();
}
document.addEventListener('mousedown', e => {
  if (openMenu && !openMenu.contains(e.target)) closeMenu();
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });
window.addEventListener('hashchange', closeMenu);

// ── Tooltip ────────────────────────────────────────────────────────
export const tip = {
  show(x, y, content) {
    const el = document.getElementById('tip');
    el.innerHTML = String(content);
    el.style.display = 'block';
    const w = el.offsetWidth, h = el.offsetHeight, pad = 10;
    let left = x + 14, top = y + 14;
    if (left + w > window.innerWidth - pad) left = x - w - 14;
    if (top + h > window.innerHeight - pad) top = y - h - 14;
    el.style.left = `${Math.max(pad, left)}px`;
    el.style.top = `${Math.max(pad, top)}px`;
  },
  hide() { document.getElementById('tip').style.display = 'none'; },
};

// ── Command palette ────────────────────────────────────────────────
// sources(): [{group, label, hint, run}]
export function openPalette(sources) {
  if (document.querySelector('.palette')) return;
  const items = sources();
  const scrim = document.createElement('div');
  scrim.className = 'scrim';
  mount(scrim, html`<div class="palette" role="dialog" aria-label="Command palette">
    <input placeholder="Jump to a strategy, page or action…" aria-label="Search" autocomplete="off">
    <div class="palette-list" role="listbox"></div></div>`);
  const input = $('input', scrim), list = $('.palette-list', scrim);
  let sel = 0, shown = [];
  const close = () => { scrim.remove(); document.removeEventListener('keydown', onKey, true); };
  const render = () => {
    const q = input.value.trim().toLowerCase();
    shown = items.filter(it => !q || `${it.label} ${it.hint || ''} ${it.group}`.toLowerCase().includes(q)).slice(0, 40);
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    let lastGroup = null;
    list.innerHTML = shown.length ? shown.map((it, i) => {
      const g = it.group !== lastGroup ? `<div class="palette-group">${esc(it.group)}</div>` : '';
      lastGroup = it.group;
      return `${g}<div class="palette-item ${i === sel ? 'on' : ''}" role="option" data-i="${i}">
        <div class="grow">${esc(it.label)}</div>${it.hint ? `<span class="kind">${esc(it.hint)}</span>` : ''}</div>`;
    }).join('') : '<div class="empty">No matches</div>';
    const on = list.querySelector('.palette-item.on');
    if (on) on.scrollIntoView({ block: 'nearest' });
  };
  const run = i => { const it = shown[i]; if (!it) return; close(); it.run(); };
  const onKey = e => {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(shown.length - 1, sel + 1); render(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); render(); }
    else if (e.key === 'Enter') { e.preventDefault(); run(sel); }
  };
  input.addEventListener('input', () => { sel = 0; render(); });
  list.addEventListener('click', e => { const it = e.target.closest('.palette-item'); if (it) run(+it.dataset.i); });
  scrim.addEventListener('mousedown', e => { if (e.target === scrim) close(); });
  document.addEventListener('keydown', onKey, true);
  document.body.appendChild(scrim);
  render();
  input.focus();
}
