// Per-viewer UI state that should survive a reload: filters, sort
// orders, chosen periods. One namespaced object per page.

const KEY = 'linck-ui';
let all = {};
try { all = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { all = {}; }

function save() {
  try { localStorage.setItem(KEY, JSON.stringify(all)); } catch (e) { /* private mode */ }
}

// ui('deployments', { q: '', st: 'all' }) → live object; ui.set merges and saves.
export function ui(ns, defaults) {
  all[ns] = { ...defaults, ...(all[ns] || {}) };
  return {
    get: () => all[ns],
    set(patch) { all[ns] = { ...all[ns], ...patch }; save(); return all[ns]; },
  };
}

// Tiny pub/sub for app-wide data changes ('data', 'ticks', 'status').
const bus = new EventTarget();
export function emit(type, detail) { bus.dispatchEvent(new CustomEvent(type, { detail })); }
export function listen(type, fn) {
  const h = e => fn(e.detail);
  bus.addEventListener(type, h);
  return () => bus.removeEventListener(type, h);
}
