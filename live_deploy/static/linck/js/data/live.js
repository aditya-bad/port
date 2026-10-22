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
    if (/^kite_|^health_/.test(ev.event_type)) pollStatus();
    emit('event', ev);
  };
}

// ── Kite ───────────────────────────────────────────────────────────
// state: 'connected' | 'stalled' (connected, but no ticks for a while in
// market hours: reconnecting by itself) | 'connecting' | 'expired'
export const kite = { state: 'expired', lastTickAt: null, error: null };
// Deployments active in the database with nothing running them, and the
// health monitor's open issues (/health; app/ops_health.py).
export const health = { notRunning: [], issues: [], sig: '' };
// The daily option-history backfill while it runs (/health), else null.
export const backfill = { running: null };
let fastPoll = null;
let connecting = false;
export async function pollStatus() {
  try {
    const h = await api.health();
    kite.state = h.kite_connected && !h.needs_login ? (h.ticks_stalled_s ? 'stalled' : 'connected') : connecting ? 'connecting' : 'expired';
    kite.stalledFor = h.ticks_stalled_s || null;
    kite.lastTickAt = h.last_tick_at || null;
    kite.error = h.last_error || null;
    const nr = h.not_running || [];
    const iss = h.open_issues || [];
    const sig = `${nr.map(x => `${x.id}:${x.open_positions}`).join(',')}|${iss.map(x => `${x.id}:${x.snoozed_until || ''}`).join(',')}`;
    health.notRunning = nr;
    health.issues = iss;
    if (sig !== health.sig) { health.sig = sig; emit('data'); }
    backfill.running = h.candle_backfill || null;
    // follow a running backfill every few seconds, then back to the normal pace
    if (backfill.running && !fastPoll) fastPoll = setTimeout(() => { fastPoll = null; pollStatus(); }, 4000);
  } catch (e) {
    kite.state = connecting ? 'connecting' : 'expired';
    kite.error = e.message;
  }
  emit('status', kite);
  return kite;
}
// Another view (Account) saw a backfill start before the next /health:
// show it in the header now and follow it from here.
export function noticeBackfill(running) {
  if (!running || backfill.running) return;
  backfill.running = running;
  if (!fastPoll) fastPoll = setTimeout(() => { fastPoll = null; pollStatus(); }, 4000);
  emit('status', kite);
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
