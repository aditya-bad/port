// Geek mode: a full-screen, strategy-specific view of one deployment
// (pages/geek/, API app/routers/geek.py). Only strategies with a Geek
// module on the server get the button.

import { go } from '../core/router.js';

export const GEEK = new Set(['strangle_monthly_v2']);
export const supports = v => !!v && GEEK.has(v.strategy);

const reduced = () => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; } };
const store = (k, v) => { try { sessionStorage.setItem(k, v); } catch (e) { /* private mode */ } };
const read = k => { try { return sessionStorage.getItem(k); } catch (e) { return null; } };

// The normal page falls away, then the geek route opens a circle
// from `from` (the button pressed).
export function enter(id, from, view = '') {
  const r = from && from.getBoundingClientRect ? from.getBoundingClientRect() : null;
  store('geekEnter', JSON.stringify(r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : { x: innerWidth - 80, y: 140 }));
  if (!location.hash.includes('/geek')) store('geekBack', location.hash);
  const to = `d/${id}/geek${view ? `/${view}` : ''}`;
  if (reduced()) { go(to); return; }
  document.body.classList.add('geek-leaving');
  setTimeout(() => go(to), 300);
}

// Where the circle opens from, once, right after enter().
export function takeEntry() {
  const v = read('geekEnter');
  try { sessionStorage.removeItem('geekEnter'); } catch (e) { /* ignore */ }
  try { return v ? JSON.parse(v) : null; } catch (e) { return null; }
}

// Back to the page geek mode was opened from (that deployment's Now tab
// otherwise), closing the circle into the Exit button.
export function exit(id, shell, from) {
  const back = read('geekBack');
  const to = back && back.startsWith(`#/d/${id}`) && !back.includes('/geek') ? back.slice(2) : `d/${id}/now`;
  if (!shell || reduced()) { go(to); return; }
  if (from && from.getBoundingClientRect) {
    const r = from.getBoundingClientRect();
    shell.style.setProperty('--ox', `${r.left + r.width / 2}px`);
    shell.style.setProperty('--oy', `${r.top + r.height / 2}px`);
  }
  shell.classList.remove('gk-in');
  shell.classList.add('gk-out');
  setTimeout(() => go(to), 380);
}
