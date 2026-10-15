// Live layer: the shared /sse/ticks and /sse/events streams, Kite/health
// status, and live P&L for open positions. One connection each, app-wide.
import { api, pnlMode } from './api.js';
import { toast } from './ui.js';

export const bus = new EventTarget();
const emit = (type, detail) => bus.dispatchEvent(new CustomEvent(type, { detail }));

// ── Ticks ──────────────────────────────────────────────────────────
export const prices = new Map();   // token -> { price, prevClose, at }
let tickStream = null;
let gotLiveTick = false;
export function startTicks() {
  if (tickStream) return;
  tickStream = new EventSource('/sse/ticks');
  tickStream.onmessage = ev => {
    let ticks;
    try { ticks = JSON.parse(ev.data); } catch (e) { return; }
    if (!Array.isArray(ticks)) return;
    const changed = [];
    for (const t of ticks) {
      if (t.instrument_token == null || t.last_price == null) continue;
      const prev = prices.get(t.instrument_token);
      prices.set(t.instrument_token, {
        price: t.last_price,
        prevClose: (t.ohlc && t.ohlc.close) || (prev && prev.prevClose) || null,
        at: Date.now(),
      });
      changed.push(t.instrument_token);
    }
    if (changed.length) { gotLiveTick = true; emit('ticks', changed); }
  };
}
// REST fallback for the index tape when no live ticks are flowing
// (market closed, or Kite not logged in yet).
export async function primeQuotes(tokens) {
  if (gotLiveTick) return;
  try {
    const data = await api.quotes(tokens);
    const changed = [];
    for (const tok of tokens) {
      const q = data[String(tok)];
      if (!q || q.last_price == null) continue;
      prices.set(tok, { price: q.last_price, prevClose: q.prev_close, at: null });
      changed.push(tok);
    }
    if (changed.length) emit('ticks', changed);
  } catch (e) { /* no Kite session: tape just shows dashes */ }
}

// ── Events (fills, pauses, errors) ─────────────────────────────────
const EVENT_LABEL = {
  execution_entry: 'Entry', execution_exit: 'Exit', paused: 'Paused', resumed: 'Resumed',
  stopped: 'Stopped', flattened: 'Positions closed', created: 'Deployed', strategy_error: 'Strategy error',
  kite_disconnected: 'Kite disconnected', kite_reconnected: 'Kite reconnected',
};
// Individual fills are summarised by the execution_* event that follows.
const QUIET = new Set(['fill_buy', 'fill_sell']);
let eventStream = null;
export function startEvents() {
  if (eventStream) return;
  eventStream = new EventSource('/sse/events');
  eventStream.onmessage = msg => {
    let ev;
    try { ev = JSON.parse(msg.data); } catch (e) { return; }
    if (!ev || !ev.event_type) return;
    if (!QUIET.has(ev.event_type)) {
      const bad = /error|fail|disconnected/i.test(ev.event_type);
      toast(`${ev.deployment_name || 'Deployment'} · ${EVENT_LABEL[ev.event_type] || ev.event_type.replace(/_/g, ' ')}`,
        ev.message || '', bad ? 'bad' : 'ok', bad ? 0 : 6000);
    }
    if (/^kite_/.test(ev.event_type)) pollHealth();
    emit('changed', ev);
  };
}

// ── Health / Kite ──────────────────────────────────────────────────
export const status = { kite: null, needsLogin: false, lastTickAt: null, error: null, db: true };
export async function pollHealth() {
  try {
    const h = await api.health();
    status.kite = !!h.kite_connected;
    status.needsLogin = !!h.needs_login;
    status.lastTickAt = h.last_tick_at;
    status.error = h.last_error;
    status.db = h.database_connected !== false;
  } catch (e) {
    status.kite = false;
    status.error = e.message;
  }
  emit('status', status);
  return status;
}
export async function loginWithKite() {
  const { login_url } = await api.kiteLoginUrl();
  const popup = window.open(login_url, 'kite_login', 'width=520,height=720');
  const iv = setInterval(async () => {
    await pollHealth();
    if (!popup || popup.closed || status.kite) {
      clearInterval(iv);
      if (status.kite) toast('Kite connected', 'Live prices and strategy execution are back on.', 'ok');
    }
  }, 1500);
}

// ── Live P&L for open positions ────────────────────────────────────
// Recomputes an open position's P&L from the latest tick. In the
// charges/slippage bases, charges are projected client-side with the
// same Zerodha formula the server uses (Calculators.brokerage), and
// slippage — which needs the order book only the server sees — carries
// the server's last projected amount as a fixed offset.
export function livePnl(p) {
  const tick = prices.get(p.instrument_token);
  const price = tick ? tick.price : p.current_price;
  if (price == null) return null;
  const qty = Number(p.qty), avg = Number(p.avg_entry_price);
  const gross = p.side === 'long' ? (price - avg) * qty : (avg - price) * qty;
  const mode = pnlMode();
  if (mode === 'gross') return gross;
  let charges = 0;
  try {
    const buyPrice = p.side === 'long' ? avg : price, sellPrice = p.side === 'long' ? price : avg;
    // calculators.js declares a top-level const (not a window property).
    // eslint-disable-next-line no-undef
    charges = Calculators.brokerage({ segment: 'options', buyPrice, sellPrice, quantity: qty }).totalCharges;
  } catch (e) { charges = 0; }
  let slip = 0;
  if (mode === 'all' && p.unrealized_pnl_net != null && p.unrealized_pnl_net_slip != null) {
    slip = p.unrealized_pnl_net - p.unrealized_pnl_net_slip;
  }
  return gross - charges - slip;
}
export const livePrice = p => {
  const t = prices.get(p.instrument_token);
  return t ? t.price : p.current_price;
};
