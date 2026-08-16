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
