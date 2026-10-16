// Appearance preferences: one registry drives the attributes on <html>
// (which css/tokens.css reads), the Account › Appearance controls, and
// what is saved per user on the server (/v2/preferences).
//
// Add a theme: a block in tokens.css + an entry in THEMES.
// Add a preference: attribute rules in tokens.css + an entry in PREFS.
// Nothing else needs to change: Account renders whatever is listed here.

import { api } from './api.js';

export const THEMES = [
  { id: 'paper', label: 'Paper', hint: 'Warm off-white, the default', swatch: { paper: 'oklch(0.968 0.006 90)', ink: 'oklch(0.2 0.01 90)', y: 'oklch(0.88 0.13 95)', line: 'oklch(0.86 0.01 90)' } },
  { id: 'slate', label: 'Slate', hint: 'Cool grey-blue daylight', swatch: { paper: 'oklch(0.972 0.006 240)', ink: 'oklch(0.22 0.03 255)', y: 'oklch(0.86 0.12 85)', line: 'oklch(0.87 0.012 240)' } },
  { id: 'night', label: 'Night', hint: 'Warm charcoal for evenings', swatch: { paper: 'oklch(0.19 0.008 75)', ink: 'oklch(0.93 0.008 85)', y: 'oklch(0.45 0.1 85)', line: 'oklch(0.32 0.01 75)' } },
];

// key, html attribute, label, hint, options [value, label, swatch?], default
export const PREFS = [
  { key: 'pos', attr: 'data-pos', label: 'Profit colour in charts', hint: 'Bars and fills for gains', def: 'green',
    options: [['green', 'Green', 'var(--g)'], ['ink', 'Ink', 'var(--ink)']] },
  { key: 'hl', attr: 'data-hl', label: 'Highlight colour', hint: 'Things that need you, selected rows, verdicts', def: 'yellow',
    options: [['yellow', 'Yellow', 'oklch(0.88 0.13 95)'], ['mint', 'Mint', 'oklch(0.86 0.12 165)'], ['lilac', 'Lilac', 'oklch(0.84 0.09 300)']] },
  { key: 'ct', attr: 'data-ct', label: 'Contrast', hint: 'High darkens secondary text and dividers', def: 'std',
    options: [['std', 'Standard'], ['high', 'High']] },
  { key: 'size', attr: 'data-size', label: 'Text size', hint: 'Scales the whole interface', def: 'std',
    options: [['sm', 'Compact'], ['std', 'Standard'], ['lg', 'Large']] },
  { key: 'font', attr: 'data-font', label: 'Typeface', hint: 'Numbers always stay monospaced', def: 'linck',
    options: [['linck', 'Linck'], ['plain', 'Plain']] },
];

const KEY = 'linck-prefs';
const listeners = new Set();

function systemTheme() {
  try { return matchMedia('(prefers-color-scheme: dark)').matches ? 'night' : 'paper'; } catch (e) { return 'paper'; }
}
function load() {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { return {}; }
}
let state = load();

export function get(key) {
  if (key === 'theme') return THEMES.some(t => t.id === state.theme) ? state.theme : systemTheme();
  const p = PREFS.find(x => x.key === key);
  if (!p) return state[key];
  return p.options.some(o => o[0] === state[key]) ? state[key] : p.def;
}
export function all() {
  const out = { theme: get('theme') };
  PREFS.forEach(p => { out[p.key] = get(p.key); });
  return out;
}

export function apply() {
  const root = document.documentElement;
  root.setAttribute('data-th', get('theme'));
  PREFS.forEach(p => root.setAttribute(p.attr, get(p.key)));
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', getComputedStyle(root).getPropertyValue('--paper').trim() || '#f6f5f0');
}

let saveTimer;
const edited = {};   // keys changed during this visit beat the server copy
export function set(key, value) {
  edited[key] = value;
  state = { ...state, [key]: value };
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* private mode: still applies for this visit */ }
  apply();
  listeners.forEach(fn => fn(key, value));
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => api.savePrefs({ [key]: value }).catch(() => {}), 400);
}
export function cycleTheme() {
  const i = THEMES.findIndex(t => t.id === get('theme'));
  set('theme', THEMES[(i + 1) % THEMES.length].id);
}
export const themeLabel = () => (THEMES.find(t => t.id === get('theme')) || THEMES[0]).label;
export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }

// The server copy wins (the same prefs follow the user to every
// device), except for anything changed during this visit before it loaded.
export async function sync() {
  try {
    const remote = await api.prefs();
    if (remote && typeof remote === 'object') {
      state = { ...state, ...remote, ...edited };
      try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
      apply();
      listeners.forEach(fn => fn('*'));
    }
  } catch (e) { /* offline or API-key session: local prefs only */ }
}
