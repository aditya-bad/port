// LinckLens's data (app/lens.py), shared by its pages so moving between
// them doesn't ask Kite again: the holdings (kept a minute here too, as
// on the server), and the year of prices, followed while the server
// fetches it in the background.

import { api } from '../core/api.js';

const STALE_MS = 60000;
const st = { h: null, hErr: null, hAt: 0, busy: false, y: null, yErr: null, following: false };
const subs = new Set();

export const lens = st;
export function onLens(fn) { subs.add(fn); return () => subs.delete(fn); }
function emit() { subs.forEach(f => { try { f(st); } catch (e) { console.error(e); } }); }

export async function loadHoldings(fresh = false) {
  if (st.busy) return;
  if (!fresh && st.h && Date.now() - st.hAt < STALE_MS) { emit(); followHistory(); return; }
  st.busy = true; emit();
  try {
    st.h = await api.lensHoldings(fresh); st.hErr = null; st.hAt = Date.now();
  } catch (e) { st.hErr = e; }
  st.busy = false; emit();
  if (st.h && !st.hErr) followHistory(fresh);
}

// The year: asked until it's in (the server fetches a share at a time).
async function followHistory(again = false) {
  if (st.following || (st.y && st.y.state === 'ready' && !again)) return;
  st.following = true;
  try {
    for (let i = 0; i < 400; i += 1) {
      try { st.y = await api.lensHistory(); st.yErr = null; } catch (e) { st.yErr = e; break; }
      emit();
      if (st.y.state !== 'loading') break;
      await new Promise(r => setTimeout(r, 1500));
    }
  } finally { st.following = false; emit(); }
}

// The holdings' year, by symbol, when it's in.
export const yearOf = sym => (st.y && st.y.state === 'ready' && st.y.insights.shares[sym]) || null;
