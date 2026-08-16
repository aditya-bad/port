// live_deploy — thin fetch wrappers for every backend endpoint, plus
// formatting/badge helpers shared by every view. Nothing in here
// touches the DOM — dashboard.js/catalog.js/deployments.js/detail.js
// own their own rendering, this file is the one place that knows the
// actual API shapes.

const Api = {
  // ── Strategies ──────────────────────────────────────────────────
  async listStrategies() {
    const r = await fetch('/strategies');
    return r.json();
  },
  async setStrategyEnabled(name, enabled) {
    const r = await fetch(`/strategies/${encodeURIComponent(name)}/enabled`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
    });
    const data = await r.json();
    return { ok: r.ok, data };
  },
  async listPresets(strategyName) {
    const r = await fetch(`/strategies/${encodeURIComponent(strategyName)}/presets`);
    if (!r.ok) throw new Error(`Could not load presets (${r.status})`);
    return r.json();
  },
  async createPreset(strategyName, presetName, config) {
    const r = await fetch(`/strategies/${encodeURIComponent(strategyName)}/presets`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset_name: presetName, config }),
    });
    const data = await r.json();
    return { ok: r.ok, data };
  },
  async deletePreset(strategyName, presetId) {
    const r = await fetch(`/strategies/${encodeURIComponent(strategyName)}/presets/${presetId}`, { method: 'DELETE' });
    const data = await r.json();
    return { ok: r.ok, data };
  },

  // ── Deployments (CRUD/lifecycle) ───────────────────────────────
  async listDeployments() {
    const r = await fetch('/deployments');
    return r.json();
  },
  async getDeployment(id) {
    const r = await fetch(`/deployments/${id}`);
    if (!r.ok) throw new Error(`No such deployment (${r.status})`);
    return r.json();
  },
  async createDeployment(body) {
    const r = await fetch('/deployments', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await r.json();
    return { ok: r.ok, data };
  },
  async updateDeployment(id, body) {
    const r = await fetch(`/deployments/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await r.json();
    return { ok: r.ok, data };
  },
  async pauseDeployment(id) {
    return fetch(`/deployments/${id}/pause`, { method: 'POST' });
  },
  async resumeDeployment(id) {
    return fetch(`/deployments/${id}/resume`, { method: 'POST' });
  },
  async stopDeployment(id, forceClose) {
    return fetch(`/deployments/${id}/stop?force_close=${forceClose}`, { method: 'POST' });
  },
  async flattenAll() {
    const r = await fetch('/deployments/flatten-all', { method: 'POST' });
    const data = await r.json();
    return { ok: r.ok, data };
  },
  async clearAllDeployments(password, confirm) {
    const r = await fetch('/deployments/clear-all', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, confirm }),
    });
    const data = await r.json();
    return { ok: r.ok, data };
  },

  // ── Per-deployment detail data ─────────────────────────────────
  // Each throws on a non-2xx response (rather than quietly returning
  // the error body as if it were real data) — Detail.renderBody()'s
  // own try/catch turns that into a visible "could not load" state per
  // tab instead of a silent crash deep inside a render function.
  async getPositions(id, status = 'open') {
    const r = await fetch(`/deployments/${id}/positions?status=${status}`);
    if (!r.ok) throw new Error(`Could not load positions (${r.status})`);
    return r.json();
  },
  async getTrades(id, limit = 200) {
    const r = await fetch(`/deployments/${id}/trades?limit=${limit}`);
    if (!r.ok) throw new Error(`Could not load trades (${r.status})`);
    return r.json();
  },
  async getReport(id) {
    const r = await fetch(`/deployments/${id}/report`);
    if (!r.ok) throw new Error(`Could not load report (${r.status})`);
    return r.json();
  },
  async getEvents(id, limit = 200) {
    const r = await fetch(`/deployments/${id}/events?limit=${limit}`);
    if (!r.ok) throw new Error(`Could not load events (${r.status})`);
    return r.json();
  },
  async getSnapshots(id, limit = 1000) {
    const r = await fetch(`/deployments/${id}/snapshots?limit=${limit}`);
    if (!r.ok) throw new Error(`Could not load snapshots (${r.status})`);
    return r.json();
  },
  async getPnlDigestForDeployment(id, period = 'day', limit = 400) {
    const r = await fetch(`/deployments/${id}/pnl-digest?period=${period}&limit=${limit}`);
    if (!r.ok) throw new Error(`Could not load the P&L calendar data (${r.status})`);
    return r.json();
  },

  // ── Cross-deployment aggregates (Dashboard) ────────────────────
  async getAllPositions(status = 'open') {
    const r = await fetch(`/positions?status=${status}`);
    return r.json();
  },
  async getRecentTrades(limit = 20) {
    const r = await fetch(`/trades/recent?limit=${limit}`);
    return r.json();
  },
  async getPortfolioEquityCurve() {
    const r = await fetch('/portfolio/equity-curve');
    if (!r.ok) throw new Error(`Could not load the portfolio equity curve (${r.status})`);
    return r.json();
  },
  async getPnlDigest(period = 'day', limit = 30) {
    const r = await fetch(`/portfolio/pnl-digest?period=${period}&limit=${limit}`);
    if (!r.ok) throw new Error(`Could not load the P&L digest (${r.status})`);
    return r.json();
  },
  async getPnlReport(period = 'day', offset = 0) {
    const r = await fetch(`/portfolio/pnl-report?period=${period}&offset=${offset}`);
    if (!r.ok) throw new Error(`Could not load the P&L report (${r.status})`);
    return r.json();
  },
  async getStrategyLeaderboard() {
    const r = await fetch('/portfolio/strategy-leaderboard');
    if (!r.ok) throw new Error(`Could not load the strategy leaderboard (${r.status})`);
    return r.json();
  },

  // ── Instruments ─────────────────────────────────────────────────
  async listInstruments() {
    const r = await fetch('/instruments');
    return r.json();
  },
  async searchInstruments(q) {
    const r = await fetch(`/instruments/search?q=${encodeURIComponent(q)}`);
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || `Search failed (${r.status})`);
    return data;
  },
  async addInstrument(token, symbol) {
    const r = await fetch('/instruments', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify([{ instrument_token: token, symbol: symbol || null }]),
    });
    const data = await r.json();
    return { ok: r.ok, data };
  },
  async removeInstrument(token) {
    const r = await fetch(`/instruments/${token}`, { method: 'DELETE' });
    return r.json();
  },

  // ── Kite manual login ───────────────────────────────────────────
  async manualKiteLogin(payload) {
    const r = await fetch('/kite/manual-login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await r.json();
    return { ok: r.ok, data };
  },

  // ── This app's own auth: users, password, audit log ────────────
  // (the "who's currently logged in" cookie/session dance is
  // login.html/index.html's logout() — this is everything reachable
  // from inside the app once already logged in.)
  async me() {
    const r = await fetch('/auth/me');
    if (!r.ok) throw new Error(`Could not load current user (${r.status})`);
    return r.json();
  },
  async changePassword(oldPassword, newPassword) {
    const r = await fetch('/auth/change-password', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    });
    const data = await r.json();
    return { ok: r.ok, data };
  },
  async logoutEverywhere() {
    const r = await fetch('/auth/logout-everywhere', { method: 'POST' });
    const data = await r.json();
    return { ok: r.ok, data };
  },
  async listUsers() {
    const r = await fetch('/auth/users');
    if (!r.ok) throw new Error(`Could not load users (${r.status})`);
    return r.json();
  },
  async createUser(username, password) {
    const r = await fetch('/auth/users', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await r.json();
    return { ok: r.ok, data };
  },
  async getAuditLog(limit = 200) {
    const r = await fetch(`/auth/audit-log?limit=${limit}`);
    if (!r.ok) throw new Error(`Could not load audit log (${r.status})`);
    return r.json();
  },
};

// ── Formatting helpers, shared by every view ───────────────────────
// Money/pnl/date formatting lives here ONCE so Dashboard, Catalog,
// Deployments, and Detail can never drift into slightly different
// number formats for the same underlying value.

function fmtMoney(n) {
  if (n === null || n === undefined) return '—';
  return '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

function fmtSignedMoney(n) {
  if (n === null || n === undefined) return '—';
  const v = Number(n);
  return (v >= 0 ? '+' : '') + fmtMoney(v);
}

function fmtNum(n, decimals = 2) {
  if (n === null || n === undefined) return '—';
  return Number(n).toLocaleString('en-IN', { maximumFractionDigits: decimals });
}

function fmtPct(n, decimals = 2) {
  if (n === null || n === undefined) return '—';
  return Number(n).toFixed(decimals) + '%';
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-IN', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

// Date only, no time-of-day -- the P&L Digest's own period_start is
// already a calendar-day (or calendar-week) boundary; showing hours/
// minutes/seconds on it would just be misleading precision, not more
// information (the digest doesn't know or care WHAT hour the day
// "starts" at from the user's point of view, only which day it is).
function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-IN', { year: 'numeric', month: 'short', day: 'numeric' });
}

function pnlClass(n) {
  return (n || 0) >= 0 ? 'pos' : 'neg';
}

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function spinnerHtml(label = 'Loading…') {
  return `<div class="empty"><span class="spinner"></span> ${label}</div>`;
}

// ── CSV export — records-for-your-own-records, not a data interchange
// format anything else in this app reads back, so this stays a plain
// client-side formatter: no backend endpoint, no server round trip
// beyond the JSON fetch the page already made to render the table in
// the first place. ───────────────────────────────────────────────────
function csvCell(value) {
  if (value === null || value === undefined) return '';
  const s = typeof value === 'object' ? JSON.stringify(value) : String(value);
  // Quote whenever the field could otherwise be misread: contains the
  // delimiter, a quote (doubled per RFC 4180), or a newline that would
  // otherwise look like a new row.
  if (/[",\n\r]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

function toCsv(rows, columns) {
  // columns: [{ key, label }] — label becomes the header, key indexes
  // into each row (or a function(row) for a computed/derived column).
  const header = columns.map(c => csvCell(c.label)).join(',');
  const lines = rows.map(row => columns.map(c => {
    const value = typeof c.key === 'function' ? c.key(row) : row[c.key];
    return csvCell(value);
  }).join(','));
  // \r\n per RFC 4180 -- Excel (still the most likely consumer of a
  // "download my trades" button) is more consistent about column
  // splitting with CRLF than a bare \n.
  return [header, ...lines].join('\r\n');
}

function downloadCsv(filename, csvContent) {
  // A leading UTF-8 BOM is what makes Excel correctly detect UTF-8
  // rather than guessing a legacy codepage and mangling anything
  // non-ASCII (e.g. the ₹ symbol, if it ever ends up in a cell) --
  // invisible in every other CSV consumer, so there's no downside to
  // always including it.
  const blob = new Blob(['﻿' + csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── "Updated Xs ago" freshness labels ──────────────────────────────
// Dashboard/Catalog/Deployments now read from a server-side cache (see
// app/cache.py) instead of paying a fresh multi-second Neon round trip
// on every single page view — a plain client-side "since my last
// successful load" timestamp is enough to be honest about freshness
// without needing the server to report its own cache age over the
// wire. markUpdated() is called at the end of each view's load(); one
// shared ticking interval keeps every registered label's text current
// without each view needing its own timer.
const _updatedAt = {};
function markUpdated(labelId) {
  _updatedAt[labelId] = Date.now();
  _tickUpdatedLabel(labelId);
}
function _tickUpdatedLabel(labelId) {
  const el = document.getElementById(labelId);
  const ts = _updatedAt[labelId];
  if (!el || !ts) return;
  const secs = Math.round((Date.now() - ts) / 1000);
  el.textContent = secs < 2 ? 'Updated just now' : `Updated ${secs}s ago`;
}
setInterval(() => { Object.keys(_updatedAt).forEach(_tickUpdatedLabel); }, 1000);

function emptyHtml(label) {
  return `<div class="empty">${label}</div>`;
}

// ── Equity curve chart ───────────────────────────────────────────────
// Deliberately no charting library — a single inline <polyline>, same
// "no framework, keep it simple" spirit as the rest of this UI. Not
// meant to be a full-featured chart, just enough to see the shape of an
// equity curve over time. Shared by Detail (one deployment's own curve,
// Step 5) and Portfolio (every deployment's combined curve, Step 39) —
// both just need a list of `{snapshot_at, total_value}` points; the
// Portfolio view maps its `bucket_at` field to `snapshot_at` before
// calling this, rather than this function knowing about two field
// names for the same concept.
function renderEquityChart(snapshots, emptyMessage) {
  if (snapshots.length < 2) {
    return emptyHtml(emptyMessage || (
      'Not enough snapshot data yet — equity snapshots are recorded roughly every 5 minutes per ' +
      'active deployment. Check back once this deployment has been running a while.'
    ));
  }
  const values = snapshots.map(s => s.total_value);
  const min = Math.min(...values), max = Math.max(...values);
  const range = (max - min) || 1;
  const W = 600, H = 150, PAD = 6;
  const points = snapshots.map((s, i) => {
    const x = PAD + (i / (snapshots.length - 1)) * (W - 2 * PAD);
    const y = H - PAD - ((s.total_value - min) / range) * (H - 2 * PAD);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const color = values[values.length - 1] >= values[0] ? 'var(--gain)' : 'var(--loss)';
  return `
    <div class="equity-wrap">
      <svg class="equity-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <polyline points="${points}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" />
      </svg>
      <div class="table-note">
        ${snapshots.length} snapshot(s) · ${fmtDateTime(snapshots[0].snapshot_at)} → ${fmtDateTime(snapshots[snapshots.length - 1].snapshot_at)}
        · range ${fmtMoney(min)} – ${fmtMoney(max)}
      </div>
    </div>
  `;
}

// ── Max drawdown ─────────────────────────────────────────────────────
// Largest peak-to-trough decline in an equity-curve snapshot series'
// own total_value — shared by Detail's Stats tab (originally inline
// there) and Compare's comparison table, so two views showing the same
// concept can't quietly drift into two different definitions of it.
// snapshots: [{total_value, ...}] in chronological order (both
// deployment snapshots and portfolio snapshots share this shape).
// Returns null if there isn't enough history to compute a real
// peak-to-trough move (a single point has no "trough" relative to
// anything).
function computeMaxDrawdown(snapshots) {
  if (!snapshots || snapshots.length < 2) return null;
  let peak = snapshots[0].total_value;
  let abs = null, pct = null;
  snapshots.forEach(s => {
    if (s.total_value > peak) peak = s.total_value;
    const dd = peak - s.total_value;
    if (abs == null || dd > abs) {
      abs = dd;
      pct = peak > 0 ? (dd / peak) * 100 : 0;
    }
  });
  return { abs, pct };
}

// ── Reorderable sections ─────────────────────────────────────────────
// Shared by Dashboard and Reports (Step 50) — each view lists its own
// widget/section ids in its own default order, and lets the user move
// any of them up/down; the resulting order is remembered per-view in
// localStorage so it survives reload, same persistence convention as
// Reports' own collapse state (Step 41).
//
// The saved order is a plain list of ids, filtered against the view's
// CURRENT default list on every read — an id from a saved order that
// no longer exists (a section removed in a later version) is silently
// dropped, and any CURRENT id that's NOT in the saved order (a brand
// new section shipped after the user last customized their layout) is
// appended in its default relative position, never silently hidden or
// jumped to the very top. This is what lets a new section like the
// Step 50 Calendar one show up automatically for someone who already
// has a saved order from before it existed.
const SectionOrder = {
  _key(viewKey) { return `sectionOrder:${viewKey}`; },

  getOrder(viewKey, defaultIds) {
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem(this._key(viewKey)) || '[]'); }
    catch (e) { saved = []; }
    const kept = saved.filter(id => defaultIds.includes(id));
    const missing = defaultIds.filter(id => !kept.includes(id));
    return [...kept, ...missing];
  },

  setOrder(viewKey, order) {
    localStorage.setItem(this._key(viewKey), JSON.stringify(order));
  },

  // Moves `id` one slot in direction `delta` (-1 up, +1 down) within
  // the view's current order; a no-op (returns the order unchanged) if
  // already at that edge. Persists and returns the resulting order.
  move(viewKey, defaultIds, id, delta) {
    const order = this.getOrder(viewKey, defaultIds);
    const idx = order.indexOf(id);
    const target = idx + delta;
    if (idx === -1 || target < 0 || target >= order.length) return order;
    [order[idx], order[target]] = [order[target], order[idx]];
    this.setOrder(viewKey, order);
    return order;
  },

  // Reparents each section element (looked up by id) into `container`
  // in `order` -- appendChild on a node already in the right place is
  // a harmless no-op, so this is safe to call on every load(), not
  // just after an explicit reorder. Missing elements are skipped
  // (defensive; getOrder's own filtering should prevent this anyway).
  apply(container, order) {
    order.forEach(id => {
      const el = document.getElementById(id);
      if (el) container.appendChild(el);
    });
  },

  // Sets the disabled state on each section's own ▲/▼ buttons (first
  // section can't move up, last can't move down) -- call after every
  // apply() so the controls stay in sync with the current order.
  syncButtons(order) {
    order.forEach((id, i) => {
      const section = document.getElementById(id);
      if (!section) return;
      const up = section.querySelector('.reorder-btn.up');
      const down = section.querySelector('.reorder-btn.down');
      if (up) up.disabled = i === 0;
      if (down) down.disabled = i === order.length - 1;
    });
  },
};

// ── P&L calendar heatmap ─────────────────────────────────────────────
// GitHub-contribution-graph shaped, but DIVERGING (loss AND gain, not
// just "more contributions"). Shared by Detail's own Calendar tab (one
// deployment) and Reports' portfolio-wide section — both just need a
// list of `{period_start, realized_pnl, positions_closed, wins,
// losses, fills}` day-bucketed rows (the PnlDigestRow shape, period=
// "day"), from either GET /deployments/{id}/pnl-digest or GET
// /portfolio/pnl-digest.
//
// Calendar days are bucketed IST (Asia/Kolkata) on the backend (see
// queries.list_pnl_digest's own docstring) -- "today" here is computed
// the same way (toLocaleDateString with an explicit IST timeZone, not
// the browser's local date) so a user in a different timezone still
// sees the grid end on the SAME calendar day the backend actually
// bucketed data into, not one day off around midnight IST.
//
// Intensity is QUANTILE-based, not a fixed rupee scale: a deployment
// running on ₹10,000 capital and one on ₹10,00,000 capital have wildly
// different absolute P&L, so bucketing by fixed thresholds would leave
// the smaller one's whole calendar looking uniformly pale (or the
// larger one's uniformly saturated). Gains and losses are quantiled
// SEPARATELY against each other (not against combined |pnl|), so an
// asymmetric win/loss distribution doesn't skew one side's color
// spread relative to the other's.
function _isoDateIST(date) {
  return date.toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });   // en-CA => YYYY-MM-DD
}
function _addDaysToIsoDate(isoDate, n) {
  const d = new Date(isoDate + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
function _dayOfWeekIsoDate(isoDate) {
  return new Date(isoDate + 'T00:00:00Z').getUTCDay();   // 0=Sun..6=Sat
}
function _quantileBucket(sortedAbsValues, value) {
  // 1 (weakest) .. 4 (strongest), by where `value` ranks among
  // sortedAbsValues (ascending, all >= 0). A single-value series (or
  // empty) always buckets to 1 -- there's no meaningful "quartile" to
  // spread across yet, and defaulting to 1 (not 4) means a lone small
  // move doesn't paint itself as the most extreme day it's ever seen.
  if (!sortedAbsValues.length) return 1;
  let idx = sortedAbsValues.findIndex(v => v >= value);
  if (idx === -1) idx = sortedAbsValues.length - 1;
  const rank = idx / Math.max(1, sortedAbsValues.length - 1);
  if (rank < 0.25) return 1;
  if (rank < 0.5) return 2;
  if (rank < 0.75) return 3;
  return 4;
}

function renderPnlHeatmap(rows, opts = {}) {
  const weeks = opts.weeks || 53;
  const byDate = new Map();
  rows.forEach(r => byDate.set(_isoDateIST(new Date(r.period_start)), r));

  const today = _isoDateIST(new Date());
  let rangeStart = _addDaysToIsoDate(today, -(weeks * 7 - 1));
  rangeStart = _addDaysToIsoDate(rangeStart, -_dayOfWeekIsoDate(rangeStart));   // snap back to that week's Sunday
  const rangeEndPadded = _addDaysToIsoDate(today, 6 - _dayOfWeekIsoDate(today)); // snap forward to this week's Saturday

  // Quantile buckets computed ONLY from real, in-range days (never the
  // padding cells either side, which carry no data by construction).
  const gainAbs = [], lossAbs = [];
  for (const [date, r] of byDate.entries()) {
    if (date < rangeStart || date > today || !r.realized_pnl) continue;
    (r.realized_pnl > 0 ? gainAbs : lossAbs).push(Math.abs(r.realized_pnl));
  }
  gainAbs.sort((a, b) => a - b);
  lossAbs.sort((a, b) => a - b);

  const cells = [];
  for (let d = rangeStart; d <= rangeEndPadded; d = _addDaysToIsoDate(d, 1)) cells.push(d);

  let totalPnl = 0, winDays = 0, lossDays = 0, bestDay = null, worstDay = null;
  const cellHtml = cells.map(date => {
    if (date > today) return `<div class="pnl-heatmap-cell empty"></div>`;
    const row = byDate.get(date);
    let bg = 'var(--panel)';
    let title = `${fmtDate(date)}: no activity`;
    if (row) {
      const pnl = row.realized_pnl || 0;
      if (pnl > 0) { bg = `var(--heat-gain-${_quantileBucket(gainAbs, pnl)})`; winDays++; }
      else if (pnl < 0) { bg = `var(--heat-loss-${_quantileBucket(lossAbs, Math.abs(pnl))})`; lossDays++; }
      totalPnl += pnl;
      if (bestDay == null || pnl > bestDay.pnl) bestDay = { date, pnl };
      if (worstDay == null || pnl < worstDay.pnl) worstDay = { date, pnl };
      title = `${fmtDate(date)}: ${fmtSignedMoney(pnl)}` +
        (row.positions_closed ? ` · ${row.positions_closed} closed (${row.wins}W/${row.losses}L)` : '') +
        (row.fills ? ` · ${row.fills} fill${row.fills === 1 ? '' : 's'}` : '');
    }
    return `<div class="pnl-heatmap-cell" style="background:${bg}" title="${escapeHtml(title)}"></div>`;
  }).join('');

  // Month labels, one per column that starts a new calendar month —
  // aligned to the grid below via matching grid-auto-columns/gap (CSS).
  let monthHtml = '', lastMonth = null;
  for (let i = 0; i < cells.length; i += 7) {
    const month = cells[i].slice(0, 7);
    if (month !== lastMonth) {
      lastMonth = month;
      const label = new Date(cells[i] + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' });
      monthHtml += `<div class="pnl-heatmap-month-label">${label}</div>`;
    } else {
      monthHtml += `<div></div>`;
    }
  }

  const summary = (winDays + lossDays) > 0
    ? `${fmtSignedMoney(totalPnl)} total over the last ${weeks} weeks — ${winDays} winning day${winDays === 1 ? '' : 's'}, ` +
      `${lossDays} losing day${lossDays === 1 ? '' : 's'}` +
      (bestDay ? ` · best ${fmtDate(bestDay.date)} (${fmtSignedMoney(bestDay.pnl)})` : '') +
      (worstDay && worstDay.pnl < 0 ? ` · worst ${fmtDate(worstDay.date)} (${fmtSignedMoney(worstDay.pnl)})` : '')
    : `No realized P&L recorded in the last ${weeks} weeks yet.`;

  return `
    <div class="pnl-heatmap-wrap">
      <div class="pnl-heatmap-months">${monthHtml}</div>
      <div class="pnl-heatmap-grid">${cellHtml}</div>
      <div class="pnl-heatmap-legend">
        <span>Loss</span>
        ${[4, 3, 2, 1].map(n => `<div class="pnl-heatmap-cell" style="background:var(--heat-loss-${n})"></div>`).join('')}
        <div class="pnl-heatmap-cell" style="background:var(--panel)"></div>
        ${[1, 2, 3, 4].map(n => `<div class="pnl-heatmap-cell" style="background:var(--heat-gain-${n})"></div>`).join('')}
        <span>Gain</span>
      </div>
      <div class="table-note" style="margin-top:8px;">${summary}</div>
    </div>
  `;
}

// ── Trigger-type badges ─────────────────────────────────────────────
// Keyword-based classification of a fill's own `reason` string into a
// small, colored chip — lets a long trade list be scanned for every
// stop-loss / every checkpoint / every roll at a glance, without
// reading each reason individually. The trigger VOCABULARY differs
// strategy to strategy (strangle_monthly_v2's "checkpoint_target" vs.
// intraday_dtt_simple's "profit_target_decay", say), so this is
// deliberately keyword-driven rather than a hardcoded per-strategy
// table — same CATEGORY of trigger always gets the same color, which
// is the actual promise being made here (see the README), not
// byte-for-byte identical vocabulary across strategies.
function triggerBadge(reason) {
  const r = (reason || '').toLowerCase();
  if (!r) return null;
  // Risk-off / forced exits — checked first since some of these
  // overlap textually with other categories (e.g. "convergence_stop"
  // contains neither "profit" nor "adjust", but very much IS a stop).
  if (/stop|force_exit|force_close|spike|backstop/.test(r)) {
    return { cls: 'trig-stop', label: 'stop' };
  }
  // Profit-taking / target-driven closes.
  if (/profit_target|checkpoint|decay/.test(r)) {
    return { cls: 'trig-profit', label: 'profit' };
  }
  // Rebalancing / signal-driven adjustments — rolls, EOD/gap checks,
  // reversal-unwind, convergence (non-stop), directional flips.
  if (/roll|adjust|trigger|eod|gap|unwind|converg|flip/.test(r)) {
    return { cls: 'trig-adjust', label: 'adjust' };
  }
  if (/entry/.test(r)) {
    return { cls: 'trig-entry', label: 'entry' };
  }
  return { cls: 'trig-other', label: 'other' };
}

function triggerBadgeHtml(reason) {
  const b = triggerBadge(reason);
  if (!b) return '';
  return `<span class="trigger-badge ${b.cls}">${b.label}</span>`;
}
