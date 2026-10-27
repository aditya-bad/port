// LinckLens's data (app/lens.py), shared by its pages: the holdings (the
// server reads Kite once a day and keeps it; asked again here after a
// minute, which costs Kite nothing), the year of prices (from the server's
// database, followed while it reads what's missing), and live prices from
// /sse/lens (LinckLens's own Kite websocket) applied as they come.

import { api } from '../core/api.js';
import { applyPrices } from './lensprice.js';

const STALE_MS = 60000;
const st = { h: null, hErr: null, hAt: 0, busy: false, y: null, yErr: null, following: false, live: false, liveAt: null };
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

// ── Live prices ────────────────────────────────────────────────────────
// One stream while any LinckLens page is open (closed a few seconds after
// the last one goes, so moving between them keeps it); the pages are
// redrawn at most every two seconds.
const REDRAW_MS = 2000;
let es = null, users = 0, closeTimer = null, redrawTimer = null;

function redrawSoon() {
  if (redrawTimer) return;
  redrawTimer = setTimeout(() => { redrawTimer = null; emit(); }, REDRAW_MS);
}

export function startLive() {
  users += 1;
  clearTimeout(closeTimer);
  if (es || typeof EventSource === 'undefined') return;
  es = new EventSource('/sse/lens');
  es.onopen = () => { st.live = true; emit(); };
  es.onerror = () => { st.live = false; };          // EventSource tries again by itself
  es.onmessage = e => {
    let m;
    try { m = JSON.parse(e.data); } catch { return; }
    if (!m || !m.p) return;
    st.live = true;
    st.liveAt = m.at || new Date().toISOString();
    if (st.h) st.h = applyPrices(st.h, m.p);
    redrawSoon();
  };
}

export function stopLive() {
  users = Math.max(0, users - 1);
  if (users) return;
  clearTimeout(closeTimer);
  closeTimer = setTimeout(() => { if (!users && es) { es.close(); es = null; st.live = false; } }, 5000);
}
