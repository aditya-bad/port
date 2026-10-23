// The app's single source of data. Loads deployments, open legs, the
// trade book, today's session and the strategy catalogue once, refreshes
// whatever an event touches, and turns rows into one view model per
// deployment that every page reads. Live P&L is recomputed from ticks
// without refetching.

import { api } from '../core/api.js';
import { basis, pick, pnlOf, baseOf } from '../core/basis.js';
import { emit, listen } from '../core/store.js';
import { debounce } from '../core/dom.js';
import { prices, health } from './live.js';
import { dayKey } from '../core/format.js';
import { LAB_TAG, YOUNG_DAYS, QUIET_DAYS } from './config.js';
import { tradeMetrics } from './metrics.js';

const raw = { deployments: [], positions: [], trades: [], daily: [], repairs: [], cycles: {}, session: null, strategies: [], tags: [], health: {}, spike: null, exposure: null };
const loaded = { deployments: false, positions: false, book: false, session: false, strategies: false, tags: false, health: false, spike: false, exposure: false };
const errors = {};
let version = 0;

const LOADERS = {
  deployments: async () => { raw.deployments = await api.deployments(); },
  positions: async () => { raw.positions = await api.openPositions(); },
  book: async () => { const b = await api.book(); raw.trades = b.trades; raw.daily = b.daily; raw.repairs = b.repairs || []; raw.cycles = b.cycles || {}; },
  session: async () => { raw.session = await api.session(); },
  strategies: async () => { raw.strategies = await api.strategies(); },
  tags: async () => { raw.tags = await api.tags(); },
  // strategy health findings (app/strategy_health.py): {deployment id: [findings]}
  health: async () => { raw.health = (await api.strategyHealth()).deployments || {}; },
  // a VIX spike now (app/vix.py): {spiking, vix, prev_close, change_pct, exposed}
  spike: async () => { raw.spike = await api.vixSpike(); },
  // what open legs gain or lose if VIX rises a point (app/vix.exposure)
  exposure: async () => { raw.exposure = await api.vixExposure(); },
};

export async function load(kinds = Object.keys(LOADERS)) {
  await Promise.all(kinds.map(async k => {
    try { await LOADERS[k](); loaded[k] = true; delete errors[k]; } catch (e) { errors[k] = e.message; }
  }));
  version++;
  cache.clear();
  emit('data', kinds);
}
export const isLoaded = (...kinds) => kinds.every(k => loaded[k]);
export const loadError = () => Object.values(errors)[0] || null;

// Events from /sse/events decide what to refetch.
const pending = new Set();
const flush = debounce(() => { const k = [...pending]; pending.clear(); load(k); }, 400);
export function refresh(...kinds) { kinds.forEach(k => pending.add(k)); flush(); }
listen('event', ev => {
  const t = ev.event_type || '';
  if (/fill|execution|flatten|removed|close/.test(t)) refresh('deployments', 'positions', 'book', 'session', 'health');
  else refresh('deployments', 'session');
});
setInterval(() => refresh('deployments', 'session'), 60000);   // snapshots land every 5 min
setInterval(() => refresh('positions'), 30000);                 // server-side marks when no ticks flow
setInterval(() => refresh('health'), 600000);                   // the server checks every 10 minutes
setInterval(() => refresh('spike', 'exposure'), 60000);         // and VIX every minute

// ── Live leg P&L ──────────────────────────────────────────────────
export function legPrice(p) {
  const t = prices.get(p.instrument_token);
  return t ? t.price : p.current_price != null ? Number(p.current_price) : null;
}
// Gross from the latest tick; charges projected with the classic
// Zerodha formula (static/js/calculators.js); slippage carries the
// server's last projection as a fixed offset (it needs the order book).
export function legPnl(p, m = basis()) {
  const px = legPrice(p);
  if (px == null) return null;
  const qty = Number(p.qty), avg = Number(p.avg_entry_price);
  const gross = p.side === 'long' ? (px - avg) * qty : (avg - px) * qty;
  if (m === 'gross') return gross;
  let charges = 0;
  try {
    const buy = p.side === 'long' ? avg : px, sell = p.side === 'long' ? px : avg;
    // eslint-disable-next-line no-undef
    charges = Calculators.brokerage({ segment: 'options', buyPrice: buy, sellPrice: sell, quantity: qty }).totalCharges;
  } catch (e) { charges = 0; }
  const slip = m === 'all' && p.unrealized_pnl_net != null && p.unrealized_pnl_net_slip != null
    ? Number(p.unrealized_pnl_net) - Number(p.unrealized_pnl_net_slip) : 0;
  return gross - charges - slip;
}

// ── View models ───────────────────────────────────────────────────
const cache = new Map();
const memo = (key, fn) => { if (!cache.has(key)) cache.set(key, fn()); return cache.get(key); };

export const strategies = () => raw.strategies;
export const strategy = name => raw.strategies.find(s => s.name === name) || null;
export const tags = () => raw.tags;
export const session = () => raw.session;

function indexOf(d) {
  const c = d.config || {};
  const v = c.instrument || c.options_underlying || c.symbol || '';
  const s = String(v).toUpperCase().replace(' 50', '');
  return s === 'NIFTY BANK' ? 'BANKNIFTY' : s || '—';
}
function tradesBy() {
  return memo(`tradesBy:${version}`, () => {
    const m = new Map();
    for (const t of raw.trades) {
      if (!m.has(t.deployment_id)) m.set(t.deployment_id, []);
      m.get(t.deployment_id).push(t);
    }
    return m;
  });
}
function dailyBy() {
  return memo(`dailyBy:${version}`, () => {
    const m = new Map();
    for (const r of raw.daily) {
      if (!m.has(r.deployment_id)) m.set(r.deployment_id, []);
      m.get(r.deployment_id).push(r);
    }
    return m;
  });
}
// {done: [{day repaired, cycle: {from, to, current}}], open: [days flagged
// by the day check, not repaired or dismissed: {day, status, headline}]}
// by deployment, oldest first
function repairsBy() {
  return memo(`repairsBy:${version}`, () => {
    const m = new Map();
    for (const r of raw.repairs) {
      if (!m.has(r.deployment_id)) m.set(r.deployment_id, { done: [], open: [] });
      const e = m.get(r.deployment_id);
      if (r.state === 'repaired') e.done.push({ day: r.day, cycle: r.cycle || null }); else e.open.push(r);
    }
    return m;
  });
}
function legsBy() {
  return memo(`legsBy:${version}`, () => {
    const m = new Map();
    for (const p of raw.positions) {
      if (!m.has(p.deployment_id)) m.set(p.deployment_id, []);
      m.get(p.deployment_id).push(p);
    }
    return m;
  });
}
// The most recent error today with nothing filled after it.
function issueOf(id) {
  const lane = raw.session && raw.session.lanes[id];
  if (!lane) return null;
  let issue = null;
  for (const e of lane.events) {
    if (e.kind === 'error') issue = { t: e.t, text: e.reason || 'Strategy error' };
    else if (issue && (e.kind === 'fill' || e.kind === 'adjust')) issue = null;
  }
  return issue;
}

function base(d) {
  const id = d.id;
  const strat = strategy(d.strategy_name);
  const trades = tradesBy().get(id) || [];
  const created = new Date(d.created_at);
  const ageDays = Math.floor((Date.now() - created) / 864e5);
  const last = trades.length ? new Date(trades[trades.length - 1].closed_at) : null;
  const down = d.status === 'active' ? health.notRunning.find(x => x.id === id) : null;
  const issue = down ? { text: 'Not running', reason: down.reason, down: true } : d.status === 'active' ? issueOf(id) : null;
  return {
    id, raw: d, name: d.deployment_name, strategy: d.strategy_name,
    strategyLabel: strat ? strat.label : d.strategy_name,
    mode: d.mode === 'intraday' ? 'Intraday' : 'Positional', modeKey: d.mode,
    status: down ? 'down' : issue ? 'error' : d.status, rawStatus: d.status,
    cap: Number(d.initial_capital), tags: d.tags || [], isLab: (d.tags || []).includes(LAB_TAG),
    index: indexOf(d), created, ageDays, young: ageDays < YOUNG_DAYS,
    inReports: d.include_in_reports !== false,
    legs: legsBy().get(id) || [], trades, daily: dailyBy().get(id) || [],
    repairs: repairsBy().get(id) || { done: [], open: [] },
    lastTradeAt: last, quietDays: last ? Math.floor((Date.now() - last) / 864e5) : ageDays,
    issue, statusFields: d.status_fields || [],
    equityOpen: raw.session && raw.session.lanes[id] ? raw.session.lanes[id].equity_open : null,
    realizedOpen: raw.session && raw.session.lanes[id] ? raw.session.lanes[id].realized_open : null,
  };
}

// Today's P&L: P&L now minus P&L at the session open. When none of the
// open legs has had a price since the open (a holiday, Kite not
// connected, before 09:15) they haven't moved today, so only what closed
// today counts; the stored last prices would otherwise show the legs'
// whole unrealized P&L as today's move. Pure, for tests.
export function dayPnl({ realized, overall, legs, equityOpen, realizedOpen, openAt, stopped }, priceAt) {
  if (stopped) return { today: 0, stale: false, fresh: false };
  const fresh = legs.some(p => { const at = priceAt(p.instrument_token); return at != null && at >= openAt; });
  if (legs.length && !fresh && realizedOpen) return { today: realized - baseOf(realizedOpen), stale: true, fresh };
  return { today: overall - (equityOpen ? baseOf(equityOpen) : realized), stale: false, fresh };
}

// Cycle P&L: what every page follows, instead of the day. A cycle is one
// entry to its exit: an intraday deployment's cycle is the day (dayPnl);
// a positional deployment's is one episode, entry through adjustments
// and rolls to flat (app/v2/book.py), so its P&L is what the cycle's
// closed legs realized plus its open legs now. On the day a cycle closes
// it's shown beside the new one; from the next day only the new one.
// open: the server's open cycle ({realized, start}) or null;
// closedToday: [{value, from, to}] for cycles that closed today. Pure, for tests.
export function cyclePnl({ positional, today, unreal, hasLegs, open, closedToday = [], stopped }) {
  if (stopped) return { cycle: null, parts: [], sum: 0, since: null };
  if (!positional) return { cycle: today, parts: [{ kind: 'day', value: today, open: hasLegs }], sum: today, since: null };
  const parts = closedToday.map(c => ({ kind: 'closed', value: c.value, from: c.from, to: c.to, open: false }));
  let cur = null;
  if (open) cur = { kind: 'open', value: open.realized + unreal, from: open.start, open: true };
  else if (hasLegs) cur = { kind: 'open', value: unreal, from: null, open: true };   // before the server's answer
  if (cur) parts.push(cur);
  const sum = parts.reduce((a, q) => a + q.value, 0);
  return { cycle: cur ? cur.value : parts.length ? parts[parts.length - 1].value : null, parts, sum, since: cur ? cur.from : null };
}

// Live figures for one deployment in the current basis.
export function live(v) {
  const realized = pick(v.raw, 'realized_pnl') || 0;
  let unreal = 0, known = false;
  for (const p of v.legs) {
    const x = legPnl(p);
    if (x != null) { unreal += x; known = true; }
  }
  if (!known && v.legs.length) unreal = pick(v.raw, 'unrealized_pnl') || 0;
  const overall = realized + unreal;
  const { today, stale, fresh } = dayPnl({
    realized, overall, legs: v.legs, equityOpen: v.equityOpen, realizedOpen: v.realizedOpen,
    openAt: raw.session ? raw.session.open_at : Infinity, stopped: v.rawStatus === 'stopped',
  }, tok => { const t = prices.get(tok); return t ? t.at : null; });
  const positional = v.modeKey === 'positional', now = dayKey(new Date());
  const oc = positional ? raw.cycles[v.id] : null;
  const c = cyclePnl({
    positional, today, unreal, hasLegs: v.legs.length > 0, stopped: v.rawStatus === 'stopped',
    open: oc ? { realized: pick(oc, 'pnl') || 0, start: oc.start } : null,
    closedToday: positional ? v.trades.filter(t => dayKey(new Date(t.closed_at)) === now).map(t => ({ value: pnlOf(t), from: t.opened_at, to: t.closed_at })) : [],
  });
  return {
    realized, unreal, overall, today, stale, fresh, ret: v.cap ? overall / v.cap * 100 : 0,
    cycle: c.cycle, cycleParts: c.parts, cycleSum: c.sum, cycleSince: c.since, positional,
    lastCycle: positional && v.trades.length ? v.trades[v.trades.length - 1] : null,
  };
}

export function deployments() {
  return memo(`deps:${version}:${basis()}:${health.sig}`, () => raw.deployments.map(d => {
    const v = base(d);
    const m = tradeMetrics(v.trades.map(pnlOf));
    v.streak = m.streak;
    v.quiet = v.rawStatus === 'active' && v.quietDays >= QUIET_DAYS ? v.quietDays : 0;
    return v;
  }));
}
export const find = id => deployments().find(v => v.id === id) || null;
export const rawDeployment = id => raw.deployments.find(d => d.id === id) || null;
export const eventsToday = v => (raw.session && raw.session.lanes[v.id] ? raw.session.lanes[v.id].events : []);
export function totals(list) {
  return list.reduce((a, v) => {
    const x = live(v);
    a.today += x.today; a.cycle += x.cycleSum; a.overall += x.overall; a.unreal += x.unreal; a.realized += x.realized; a.legs += v.legs.length;
    return a;
  }, { today: 0, cycle: 0, overall: 0, unreal: 0, realized: 0, legs: 0 });
}
export const healthOf = id => raw.health[id] || [];
export const vixExposure = () => raw.exposure;
export const vixSpike = () => (raw.spike && raw.spike.spiking ? raw.spike : null);
export const allTrades = () => raw.trades;
export const allDaily = () => raw.daily;
export const dataVersion = () => version;
