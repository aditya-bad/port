// Live layer: /sse/ticks (prices), /sse/events (fills, pauses, errors),
// Kite connection status, and Kite login. One connection each.

import { api } from '../core/api.js';
import { emit } from '../core/store.js';

export const prices = new Map();   // token -> { price, prevClose, at }
let gotLiveTick = false;
let ticksSrc = null;

export function startTicks() {
  if (ticksSrc) return;
  ticksSrc = new EventSource('/sse/ticks');
  ticksSrc.onmessage = ev => {
    let ticks;
    try { ticks = JSON.parse(ev.data); } catch (e) { return; }
    if (!Array.isArray(ticks)) return;
    let n = 0;
    for (const t of ticks) {
      if (t.instrument_token == null || t.last_price == null) continue;
      const prev = prices.get(t.instrument_token);
      prices.set(t.instrument_token, {
        price: t.last_price,
        prevClose: (t.ohlc && t.ohlc.close) || (prev && prev.prevClose) || null,
        at: Date.now(),
      });
      n++;
    }
    if (n) { gotLiveTick = true; emit('ticks'); }
  };
}

// Index strip fallback when nothing is streaming (market closed, Kite
// not logged in): last known quotes over REST.
export async function primeQuotes(tokens) {
  if (gotLiveTick) return;
  try {
    const data = await api.quotes(tokens);
    for (const tok of tokens) {
      const q = data && data[String(tok)];
      if (q && q.last_price != null) prices.set(tok, { price: q.last_price, prevClose: q.prev_close, at: null });
    }
    emit('ticks');
  } catch (e) { /* no Kite session: strip shows dashes */ }
}

let eventsSrc = null;
export function startEvents() {
  if (eventsSrc) return;
  eventsSrc = new EventSource('/sse/events');
  eventsSrc.onmessage = msg => {
    let ev;
    try { ev = JSON.parse(msg.data); } catch (e) { return; }
    if (!ev || !ev.event_type) return;
    if (/^kite_/.test(ev.event_type)) pollStatus();
    emit('event', ev);
  };
}

// ── Kite ───────────────────────────────────────────────────────────
// state: 'connected' | 'connecting' | 'expired'
export const kite = { state: 'expired', lastTickAt: null, error: null };
let connecting = false;
export async function pollStatus() {
  try {
    const h = await api.health();
    kite.state = h.kite_connected && !h.needs_login ? 'connected' : connecting ? 'connecting' : 'expired';
    kite.lastTickAt = h.last_tick_at || null;
    kite.error = h.last_error || null;
  } catch (e) {
    kite.state = connecting ? 'connecting' : 'expired';
    kite.error = e.message;
  }
  emit('status', kite);
  return kite;
}
// Opens Zerodha's login in a new tab and watches for the session.
export async function loginWithKite(onDone) {
  const { login_url } = await api.kiteLoginUrl();
  const win = window.open(login_url, 'kite_login', 'width=520,height=720');
  connecting = true;
  kite.state = 'connecting';
  emit('status', kite);
  const iv = setInterval(async () => {
    await pollStatus();
    if (kite.state === 'connected' || !win || win.closed) {
      clearInterval(iv);
      connecting = false;
      await pollStatus();
      onDone && onDone(kite.state === 'connected');
    }
  }, 1500);
  return () => { clearInterval(iv); connecting = false; pollStatus(); };
}
export const isLive = () => kite.state === 'connected';
