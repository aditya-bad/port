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

  // ── Tags (predefined catalog — Settings -> Tags) ──────────────────
  async listTags() {
    const r = await fetch('/tags');
    if (!r.ok) throw new Error(`Could not load tags (${r.status})`);
    return r.json();
  },
  async createTag(name) {
    const r = await fetch('/tags', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
    });
    const data = await r.json();
    return { ok: r.ok, data };
  },
  async deleteTag(id) {
    // 204 No Content on success -- unlike deletePreset above, there's no
    // body to parse, so this returns the raw Response (same pattern as
    // pauseDeployment/resumeDeployment) and leaves .ok/error-json to the
    // caller.
    return fetch(`/tags/${id}`, { method: 'DELETE' });
  },

  // ── Mobile push notifications (Step 85) ────────────────────────────
  async getVapidPublicKey() {
    const r = await fetch('/notifications/vapid-public-key');
    if (!r.ok) throw new Error(`Could not load VAPID key (${r.status})`);
    return r.json();   // {public_key: string|null}
  },
  async subscribePush(subscription) {
    // `subscription` is a raw PushSubscription (from
    // PushManager.subscribe()) -- .toJSON() gives exactly
    // {endpoint, keys: {p256dh, auth}}, matching the backend's
    // SubscribeIn schema (app/routers/notifications.py) verbatim.
    return fetch('/notifications/subscribe', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(subscription.toJSON()),
    });
  },
  async unsubscribePush(endpoint) {
    return fetch('/notifications/unsubscribe', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint }),
    });
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
  async deleteDeployment(id) {
    return fetch(`/deployments/${id}/delete`, { method: 'POST' });
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
  async getPnlDigestForDeployment(id, period = 'day', limit = 400, year = null) {
    // year (Step 74, the Calendar heatmap's year-picker): the backend
    // ignores `limit` entirely once this is set, returning that whole
    // IST calendar year instead of "the most recent `limit` buckets".
    const yearParam = year ? `&year=${year}` : '';
    const r = await fetch(`/deployments/${id}/pnl-digest?period=${period}&limit=${limit}${yearParam}`);
    if (!r.ok) throw new Error(`Could not load the P&L calendar data (${r.status})`);
    return r.json();
  },
  // Step 87 -- strategy-specific live indicators (SuperTrend value,
  // pivot levels, ...) and the adjustment-count histogram. Both are
  // opt-in per strategy (see StrategyBase's own get_status_fields/
  // ADJUSTMENT_GROUP_BY docstrings) -- callers check `source`/
  // `supported` and render nothing when a strategy has neither.
  async getStrategyStatus(id) {
    const r = await fetch(`/deployments/${id}/strategy-status`);
    if (!r.ok) throw new Error(`Could not load strategy status (${r.status})`);
    return r.json();
  },
  async getAdjustmentHistogram(id) {
    const r = await fetch(`/deployments/${id}/adjustment-histogram`);
    if (!r.ok) throw new Error(`Could not load the adjustment histogram (${r.status})`);
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
  async getPnlDigest(period = 'day', limit = 30, year = null) {
    // year: see getPnlDigestForDeployment's own comment -- same deal.
    const yearParam = year ? `&year=${year}` : '';
    const r = await fetch(`/portfolio/pnl-digest?period=${period}&limit=${limit}${yearParam}`);
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

// Both fmtDateTime and fmtDate pin an explicit timeZone: 'Asia/Kolkata'
// (Step 95) -- toLocaleString/toLocaleDateString silently fall back to
// the VIEWER's own device timezone whenever timeZone is omitted, which
// isn't a cosmetic difference for a trading app: every fill/position
// timestamp in the database is a real market event that happened at a
// specific IST wall-clock moment (see queries.py's own IST-bucketing
// comments), and a viewer whose device isn't set to IST would otherwise
// see a DIFFERENT (wrong) hour for the exact same underlying instant --
// e.g. a 10:00 IST entry rendering as "04:30" to anyone on a UTC device,
// which reads as a bizarre pre-market entry time rather than what it
// actually was. `_isoDateIST` below already established this exact
// pattern for date-only display; these two share it for date+time.
function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-IN', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    timeZone: 'Asia/Kolkata',
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
  return d.toLocaleDateString('en-IN', { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'Asia/Kolkata' });
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

// ── Deployment tag chips (Step 69, tidied in Step 88) — shared by
// Detail's header and the Deployed Strategies list row, so the two
// never render this differently. Also absorbs the "unregistered" chip
// (previously duplicated inline at each call site) and the reserved
// "excluded from reports" one, SYNTHESIZED straight from
// include_in_reports, never read off dep.tags -- see migration 0010's
// own comment for why that one deliberately never enters the real tag
// catalog. Every chip renders inside one `.chip-row` (display:flex;
// flex-wrap:wrap) so a long label (e.g. "excluded from reports") wraps
// the WHOLE ROW onto a new line if it must, never balloons into a
// multi-line box of its own the way a bare inline-block chip can once
// its column gets narrow -- `.tag` itself is white-space:nowrap for
// exactly that reason. Returns '' (not a wrapper element) when a
// deployment has nothing to show, so a call site can drop this straight
// inline without an empty gap.
function deploymentTagsHtml(dep) {
  const chips = [];
  if (!dep.strategy_registered) {
    chips.push(`<span class="tag tag-warn">unregistered</span>`);
  }
  if (!dep.include_in_reports) {
    chips.push(`<span class="tag tag-warn" title="Excluded from Dashboard, Portfolio, and Reports — toggle it back on from Edit">excluded from reports</span>`);
  }
  (dep.tags || []).forEach(name => {
    chips.push(`<span class="tag tag-info">${escapeHtml(name)}</span>`);
  });
  if (!chips.length) return '';
  return `<div class="chip-row">${chips.join('')}</div>`;
}

// ── Shared chart tooltip (Step 88) ───────────────────────────────────
// One floating box, reused by every chart in the app (equity curve,
// Compare's multi-series chart, the P&L heatmap's cells) instead of
// each rolling its own. Deliberately still no charting library —
// this is ~60 lines of plain DOM, same "no framework" spirit the
// original single-<polyline> chart already committed to; it just also
// now answers "what am I looking at" on hover AND on tap, which a bare
// polyline never could.
//
// Two entry points:
//   - App-wide [data-tooltip] delegation (below) — for anything that's
//     a single hoverable/tappable TARGET with one fixed tooltip string,
//     e.g. a heatmap cell. Put the (already HTML-escaped) tooltip HTML
//     in a `data-tooltip` attribute and this handles the rest — no
//     per-element listener wiring needed, one delegated listener does
//     every current AND future such element on the page.
//   - Direct show()/hide() calls — for something that needs to compute
//     ITS OWN tooltip content continuously as the pointer moves across
//     a continuous surface (the equity chart's nearest-point-under-
//     cursor lookup) rather than a fixed per-element string.
const ChartTooltip = {
  _el: null,
  _ensure() {
    if (this._el) return this._el;
    const el = document.createElement('div');
    el.className = 'chart-tooltip';
    document.body.appendChild(el);
    this._el = el;
    return el;
  },
  // (clientX, clientY): viewport coordinates, straight from the
  // triggering mouse/touch event — this positions in `position:fixed`
  // space, so no scroll-offset math needed either way.
  show(clientX, clientY, html) {
    const el = this._ensure();
    el.innerHTML = html;
    el.style.display = 'block';
    // Clamped to the viewport, offset from the finger/cursor rather
    // than centered under it -- centered would put a touch tooltip
    // directly under the fingertip that's still touching the screen,
    // unreadable until released.
    const OFFSET = 14, PAD = 8;
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = clientX + OFFSET, top = clientY + OFFSET;
    if (left + w > window.innerWidth - PAD) left = clientX - w - OFFSET;
    if (top + h > window.innerHeight - PAD) top = clientY - h - OFFSET;
    el.style.left = `${Math.max(PAD, left)}px`;
    el.style.top = `${Math.max(PAD, top)}px`;
  },
  hide() {
    if (this._el) this._el.style.display = 'none';
  },
};

// App-wide delegation for [data-tooltip] elements — registered once.
// Handles BOTH mouse hover and touch tap, which a native `title`
// attribute (what the P&L heatmap used to use) never could: `title`
// simply never fires on a touchscreen at all, so every chart relying
// on it was silently non-interactive on mobile specifically — the
// actual bug report this step fixes.
function _initChartTooltipDelegation() {
  if (window._chartTooltipDelegationInit) return;
  window._chartTooltipDelegationInit = true;
  document.addEventListener('mousemove', (e) => {
    const t = e.target.closest('[data-tooltip]');
    if (t) { ChartTooltip.show(e.clientX, e.clientY, t.getAttribute('data-tooltip')); return; }
    // Charts that manage their own tooltip lifecycle on every pointer
    // move already (the equity chart's nearest-point lookup, see
    // _equityChartPointerAt) are exempt -- hiding it here too would
    // just fight that, since this listener runs AFTER an inline
    // onmousemove handler on the same bubbling event.
    if (e.target.closest('.equity-chart-area')) return;
    ChartTooltip.hide();
  });
  document.addEventListener('mouseleave', () => ChartTooltip.hide());
  // passive: true -- this only ever reads the touch position, never
  // calls preventDefault, so it must never block the page's own
  // scrolling.
  document.addEventListener('touchstart', (e) => {
    const t = e.target.closest('[data-tooltip]');
    if (t) {
      const touch = e.touches[0];
      ChartTooltip.show(touch.clientX, touch.clientY, t.getAttribute('data-tooltip'));
      return;
    }
    // Same exemption as the mousemove listener above -- the equity
    // chart's own ontouchstart handler (see _equityChartTouch) already
    // manages this tooltip itself for a tap landing here.
    if (e.target.closest('.equity-chart-area')) return;
    ChartTooltip.hide();
  }, { passive: true });
}
_initChartTooltipDelegation();

// ── Equity curve chart ───────────────────────────────────────────────
// Deliberately still no charting library — plain SVG + this file's own
// ~40 lines of hover/touch handling, same "no framework, keep it
// simple" spirit as the rest of this UI, just no longer a bare
// unlabeled polyline (Step 88: "no interaction, no scale" was a fair
// complaint). Shared by Detail (one deployment's own curve, Step 5),
// Portfolio (every deployment's combined curve, Step 39), and Compare
// indirectly (its own multi-series chart reuses this same interaction
// MODEL, see compare.js, even though it renders its own polylines for
// multiple series at once) — Detail/Portfolio both just need a list of
// `{snapshot_at, total_value}` points; Portfolio maps its `bucket_at`
// field to `snapshot_at` before calling this, rather than this
// function knowing about two field names for the same concept.
//
// `chartId`: a stable id for THIS chart instance (e.g. "equity-detail",
// "equity-portfolio") — defaults to a fresh random id if omitted, but a
// caller that re-renders the SAME logical chart repeatedly (a tab
// switch, a live refresh) should pass a fixed one, so the registry
// entry below is overwritten in place each time rather than quietly
// accumulating a new orphaned entry per render for the lifetime of the
// page (this is a single-page app — nothing ever unloads on its own).
const _equityChartRegistry = {};   // chartId -> { snapshots, min, max }

function renderEquityChart(snapshots, emptyMessage, chartId) {
  if (snapshots.length < 2) {
    return emptyHtml(emptyMessage || (
      'Not enough equity history yet — one point is recorded per trading day this deployment ' +
      'has been active. Check back after it\'s run a couple of days.'
    ));
  }
  chartId = chartId || `equity-${Math.random().toString(36).slice(2)}`;
  const values = snapshots.map(s => s.total_value);
  const min = Math.min(...values), max = Math.max(...values);
  const mid = (min + max) / 2;
  const range = (max - min) || 1;
  const W = 600, H = 150, PAD = 6;
  const points = snapshots.map((s, i) => {
    const x = PAD + (i / (snapshots.length - 1)) * (W - 2 * PAD);
    const y = H - PAD - ((s.total_value - min) / range) * (H - 2 * PAD);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const color = values[values.length - 1] >= values[0] ? 'var(--gain)' : 'var(--loss)';
  _equityChartRegistry[chartId] = { snapshots, min, max, range };
  return `
    <div class="equity-wrap">
      <div class="equity-chart-row">
        <div class="equity-axis-y">
          <span>${fmtMoney(max)}</span>
          <span>${fmtMoney(mid)}</span>
          <span>${fmtMoney(min)}</span>
        </div>
        <div class="equity-chart-area"
             onmousemove="_equityChartPointerAt('${chartId}', event.clientX, event.clientY, this)"
             onmouseleave="_equityChartClear('${chartId}')"
             ontouchstart="_equityChartTouch('${chartId}', event, this)"
             ontouchmove="_equityChartTouch('${chartId}', event, this)"
             ontouchend="_equityChartClear('${chartId}')">
          <svg class="equity-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
            <polyline points="${points}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" />
          </svg>
          <div class="equity-crosshair" id="${chartId}-crosshair"></div>
          <div class="equity-dot" id="${chartId}-dot" style="background:${color};"></div>
        </div>
      </div>
      <div class="equity-axis-x">
        <span>${fmtDate(snapshots[0].snapshot_at)}</span>
        <span>${fmtDate(snapshots[snapshots.length - 1].snapshot_at)}</span>
      </div>
      <div class="table-note">
        ${snapshots.length} snapshot(s) · ${fmtDateTime(snapshots[0].snapshot_at)} → ${fmtDateTime(snapshots[snapshots.length - 1].snapshot_at)}
        · range ${fmtMoney(min)} – ${fmtMoney(max)}
      </div>
    </div>
  `;
}

// Real CSS-pixel math via the chart area's OWN current
// getBoundingClientRect() -- deliberately not the SVG viewBox's fixed
// W/H/PAD constants, which would misplace the overlay the moment the
// rendered box's aspect ratio differs from the viewBox's (always true
// here: preserveAspectRatio="none" stretches X and Y independently to
// fill whatever real size the flex layout gives it). Recomputing this
// on every pointer move is cheap (one layout read already cached by
// the browser this frame) and means the overlay stays correctly
// aligned across a window resize with zero extra wiring.
function _equityChartPointerAt(chartId, clientX, clientY, areaEl) {
  const chart = _equityChartRegistry[chartId];
  if (!chart) return;
  const rect = areaEl.getBoundingClientRect();
  const xFrac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  const idx = Math.round(xFrac * (chart.snapshots.length - 1));
  const s = chart.snapshots[idx];
  const yFrac = (s.total_value - chart.min) / chart.range;
  const leftPx = xFrac * rect.width;
  const topPx = rect.height - yFrac * rect.height;

  const crosshair = document.getElementById(`${chartId}-crosshair`);
  const dot = document.getElementById(`${chartId}-dot`);
  if (crosshair) { crosshair.style.left = `${leftPx}px`; crosshair.style.display = 'block'; }
  if (dot) { dot.style.left = `${leftPx}px`; dot.style.top = `${topPx}px`; dot.style.display = 'block'; }
  // One clean number per day (Step 96/99) -- no intraday range shown
  // here any more (Step 97 briefly added one, removed again in Step 99
  // once the underlying total_value calc that fed it turned out to be
  // double-counting a still-open short leg's entry premium).
  ChartTooltip.show(clientX, clientY, `<b>${fmtMoney(s.total_value)}</b><br>${fmtDateTime(s.snapshot_at)}`);
}

function _equityChartTouch(chartId, event, areaEl) {
  // Prevents the page from scrolling while a finger drags across the
  // chart reading values off it -- the whole point of touch support
  // here, not an accidental side effect; every other touch elsewhere
  // on the page is completely unaffected.
  event.preventDefault();
  const touch = event.touches[0];
  if (!touch) return;
  _equityChartPointerAt(chartId, touch.clientX, touch.clientY, areaEl);
}

function _equityChartClear(chartId) {
  const crosshair = document.getElementById(`${chartId}-crosshair`);
  const dot = document.getElementById(`${chartId}-dot`);
  if (crosshair) crosshair.style.display = 'none';
  if (dot) dot.style.display = 'none';
  ChartTooltip.hide();
}

// ── Max drawdown ─────────────────────────────────────────────────────
// Largest peak-to-trough decline in a deployment's REALIZED equity
// (Step 105) — shared by Detail's Stats tab (originally inline there)
// and Compare's comparison table, so two views showing the same
// concept can't quietly drift into two different definitions of it.
//
// Deliberately NOT computed off `total_value` (Step 105 correction, at
// explicit user request: "consider drawdown as the amount of capital
// lost forever"). For a "positional" deployment, total_value
// legitimately includes a currently-open position's live
// mark-to-market (see SnapshotOut's own docstring) — a big paper loss
// on an open position that later recovers before it's ever closed
// would show as a "drawdown" under the old definition even though
// nothing was actually, permanently lost. "Capital lost forever" can
// only mean SETTLED history, so this is computed off `initialCapital +
// s.realized_pnl_cumulative` at each point instead — a number that
// only ever moves when a position actually closes. For an "intraday"
// deployment this produces the EXACT same result as before: Step 99
// already made total_value equal exactly this same sum for intraday
// (open_positions_value is always 0 there), so nothing changes for the
// common case, only for a positional deployment carrying a live
// position through a real intraday price swing.
//
// snapshots: [{realized_pnl_cumulative, ...}] in chronological order
// (both deployment snapshots and portfolio snapshots share this
// shape). `initialCapital` is the deployment's own initial_capital (or
// the SUMMED initial_capital across every deployment in view, for a
// portfolio-wide series — same "peak/trough of one running number"
// math either way). Returns null if there isn't enough history to
// compute a real peak-to-trough move (a single point has no "trough"
// relative to anything).
function computeMaxDrawdown(snapshots, initialCapital) {
  if (!snapshots || snapshots.length < 2) return null;
  const equity = s => initialCapital + (s.realized_pnl_cumulative || 0);
  let peak = equity(snapshots[0]);
  let abs = null, pct = null;
  snapshots.forEach(s => {
    const v = equity(s);
    if (v > peak) peak = v;
    const dd = peak - v;
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

// The year-picker's own option list (Step 74) -- current calendar year
// down to 3 years back, computed fresh from the real clock every call
// rather than hardcoded, so "2027" becomes a real, selectable option
// the moment it's actually 2027, with zero further code changes needed
// (same for every year after that). IST, matching this whole feature's
// own day-bucketing convention -- irrelevant in practice except right
// at New Year's in a UTC-behind timezone, but consistent is cheap.
function pnlHeatmapYearOptions() {
  const currentYear = Number(_isoDateIST(new Date()).slice(0, 4));
  const years = [];
  for (let y = currentYear; y >= currentYear - 3; y--) years.push(y);
  return years;
}

function renderPnlHeatmap(rows, opts = {}) {
  const weeks = opts.weeks || 53;
  const year = opts.year || null;   // null = rolling "last N weeks ending today" (the default); a real year = that whole Jan-Dec grid instead
  const byDate = new Map();
  rows.forEach(r => byDate.set(_isoDateIST(new Date(r.period_start)), r));

  const today = _isoDateIST(new Date());
  let rangeStart, rangeEndPadded;
  if (year) {
    rangeStart = _addDaysToIsoDate(`${year}-01-01`, -_dayOfWeekIsoDate(`${year}-01-01`));       // snap back to that week's Sunday
    const dec31 = `${year}-12-31`;
    rangeEndPadded = _addDaysToIsoDate(dec31, 6 - _dayOfWeekIsoDate(dec31));                     // snap forward to that week's Saturday
  } else {
    rangeStart = _addDaysToIsoDate(today, -(weeks * 7 - 1));
    rangeStart = _addDaysToIsoDate(rangeStart, -_dayOfWeekIsoDate(rangeStart));   // snap back to that week's Sunday
    rangeEndPadded = _addDaysToIsoDate(today, 6 - _dayOfWeekIsoDate(today));      // snap forward to this week's Saturday
  }

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
    // data-tooltip (Step 88), not a native `title` attribute -- `title`
    // never fires on a touchscreen at all, so a heatmap relying on it
    // was completely non-interactive on mobile specifically. See the
    // app-wide [data-tooltip] delegation (ChartTooltip, above) that
    // now handles both mouse hover and touch tap for this same markup.
    let tooltip = `<b>${fmtDate(date)}</b><br>no activity`;
    if (row) {
      const pnl = row.realized_pnl || 0;
      if (pnl > 0) { bg = `var(--heat-gain-${_quantileBucket(gainAbs, pnl)})`; winDays++; }
      else if (pnl < 0) { bg = `var(--heat-loss-${_quantileBucket(lossAbs, Math.abs(pnl))})`; lossDays++; }
      totalPnl += pnl;
      if (bestDay == null || pnl > bestDay.pnl) bestDay = { date, pnl };
      if (worstDay == null || pnl < worstDay.pnl) worstDay = { date, pnl };
      tooltip = `<b>${fmtDate(date)}</b><br>${fmtSignedMoney(pnl)}` +
        (row.positions_closed ? `<br>${row.positions_closed} closed (${row.wins}W/${row.losses}L)` : '') +
        (row.fills ? `<br>${row.fills} fill${row.fills === 1 ? '' : 's'}` : '');
    }
    return `<div class="pnl-heatmap-cell" style="background:${bg}" data-tooltip="${escapeHtml(tooltip)}"></div>`;
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

  const rangeLabel = year ? `in ${year}` : `over the last ${weeks} weeks`;
  const summary = (winDays + lossDays) > 0
    ? `${fmtSignedMoney(totalPnl)} total ${rangeLabel} — ${winDays} winning day${winDays === 1 ? '' : 's'}, ` +
      `${lossDays} losing day${lossDays === 1 ? '' : 's'}` +
      (bestDay ? ` · best ${fmtDate(bestDay.date)} (${fmtSignedMoney(bestDay.pnl)})` : '') +
      (worstDay && worstDay.pnl < 0 ? ` · worst ${fmtDate(worstDay.date)} (${fmtSignedMoney(worstDay.pnl)})` : '')
    : `No realized P&L recorded ${rangeLabel} yet.`;

  // Year picker (Step 74) -- deliberately OUTSIDE .pnl-heatmap-wrap
  // (the scrollable box itself), so it stays visible/reachable
  // regardless of how far the grid below is scrolled. opts.selector is
  // required to render it -- a caller that doesn't pass one just gets
  // the grid alone, e.g. for a hypothetical future embed that doesn't
  // want the control at all.
  const selectorHtml = opts.selector ? `
    <div class="pnl-heatmap-range-row">
      <select class="pnl-heatmap-range-select" onchange="${opts.selector.onChange}">
        <option value="recent" ${!year ? 'selected' : ''}>Last 365 days</option>
        ${pnlHeatmapYearOptions().map(y =>
          `<option value="${y}" ${year === y ? 'selected' : ''}>${y}</option>`
        ).join('')}
      </select>
    </div>
  ` : '';

  return `
    <div class="pnl-heatmap-block">
      ${selectorHtml}
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
    </div>
  `;
}

// Scrolls a just-rendered heatmap's own .pnl-heatmap-wrap to its right
// edge -- called by every view right after setting innerHTML from
// renderPnlHeatmap's output (Step 74: reported that reloading always
// left the OLDEST data showing, the left edge, rather than the most
// recent). Scoped to `containerId` specifically (not a bare
// document.querySelector) for the same reason detail.js's own live-
// position wiring already scopes its selectors: this SPA keeps every
// view's DOM alive at once (just toggled via .active, not removed), so
// an unscoped query can find a HIDDEN heatmap on another view before
// the one actually just rendered. requestAnimationFrame, not a bare
// synchronous read right after innerHTML -- gives the browser one
// paint to actually commit layout so scrollWidth reflects the grid
// that was just inserted, not a stale pre-render value.
function scrollPnlHeatmapToEnd(containerId) {
  requestAnimationFrame(() => {
    const container = document.getElementById(containerId);
    const wrap = container && container.querySelector('.pnl-heatmap-wrap');
    if (wrap) wrap.scrollLeft = wrap.scrollWidth;
  });
}

// "Recent Periods" trend table — a bucketed realized-P&L history with a
// center-zero bar per row (see the .report-bar-* CSS), same PnlDigestRow
// shape (period_start/realized_pnl/positions_closed/wins/losses/fills)
// GET /portfolio/pnl-digest and GET /deployments/{id}/pnl-digest both
// return. Originally only Reports' own portfolio-wide trend; extracted
// here once Detail's Stats tab needed the identical table against the
// deployment-scoped endpoint instead (Step 86) — same rendering either
// way, only which digest endpoint fed it differs.
// opts.periodLabel(iso): formats one row's period_start for the first
// column — callers pass their own (day/week/month all read differently).
// `row.max_profit`/`row.max_loss` (Step 100) only exist on a single
// deployment's own digest (queries.list_pnl_digest_for_deployment,
// GET /deployments/{id}/pnl-digest) -- the portfolio-wide one
// (queries.list_pnl_digest, Reports' own use of this same shared
// renderer) never carries them at all (see PnlDigestRow's own
// docstring for why a portfolio-wide mark-to-market digest doesn't
// exist), so the two extra columns only render when at least one row
// actually has them -- Reports' table renders exactly as before.
// `row.is_position_row` (Step 101, redefined in Step 102) marks a
// POSITIONAL deployment's own digest rows -- each one is a whole
// EPISODE (every position/leg/adjustment/roll that was ever open at
// the same time as another, combined -- see
// queries.get_positional_episode_mtm_rows), not a calendar bucket and
// not a single `positions` table row either: `period_start` is the
// episode's earliest constituent leg's opened_at, `period_end` its
// latest leg's closed_at (null if any leg in the episode is still
// open). Can't tell this from `period_end` alone -- an intraday row's
// `period_end` is ALSO always null (it's simply unused there), so null
// means something different in each case; `is_position_row` is what
// actually distinguishes them. `periodLabel` is only ever asked to
// format a plain calendar bucket, so an episode row is formatted
// directly here instead, as a date range.
function _pnlRowPeriodLabel(row, periodLabel) {
  if (!row.is_position_row) return periodLabel(row.period_start);
  const opened = fmtDate(row.period_start);
  const closed = row.period_end ? fmtDate(row.period_end) : 'now';
  return `${opened} → ${closed}`;
}

function renderPnlTrendTable(rows, opts = {}) {
  const periodLabel = opts.periodLabel || (iso => fmtDate(iso));
  if (!rows.length) {
    return emptyHtml('No closed positions recorded yet.');
  }
  const maxAbs = Math.max(...rows.map(r => Math.abs(r.realized_pnl)), 1);
  const showMtm = rows.some(r => r.max_profit !== undefined && r.max_profit !== null);
  // Positional mode's rows are one PER EPISODE (every leg/adjustment/
  // roll that overlapped in time, combined -- see _pnlRowPeriodLabel's
  // own comment), not a calendar bucket -- so the header says so.
  const isPositional = rows.some(r => r.is_position_row);
  return `
    <div class="table-wrap">
    <table><thead><tr>
      <th>${isPositional ? 'Position' : 'Period'}</th><th>Realized P&amp;L</th><th>Positions closed</th><th>Win rate</th><th>Fills</th>
      ${showMtm ? `<th title="${isPositional ? 'This position&#39;s own best combined mark-to-market standing (every leg and adjustment together), from its own open' : 'This period&#39;s own best mark-to-market standing, from its own start'}">M2M Best</th>` +
                  `<th title="${isPositional ? 'This position&#39;s own worst combined mark-to-market standing (every leg and adjustment together), from its own open' : 'This period&#39;s own worst mark-to-market standing, from its own start'}">M2M Worst</th>` : ''}
    </tr></thead>
    <tbody>${rows.map(row => {
      const pct = (Math.abs(row.realized_pnl) / maxAbs) * 50;   // 50% = half the track, since the bar grows from a CENTER zero-line
      const decided = row.wins + row.losses;
      const winRate = decided > 0 ? ((row.wins / decided) * 100).toFixed(0) + '%' : '—';
      return `<tr>
        <td>${_pnlRowPeriodLabel(row, periodLabel)}</td>
        <td>
          <div class="report-row-value">
            <div class="report-bar-track">
              <div class="report-bar-zero"></div>
              <div class="report-bar-fill ${row.realized_pnl >= 0 ? 'gain' : 'loss'}" style="width:${pct}%"></div>
            </div>
            <span class="${pnlClass(row.realized_pnl)}">${fmtSignedMoney(row.realized_pnl)}</span>
          </div>
        </td>
        <td>${row.positions_closed}</td>
        <td>${winRate}</td>
        <td>${row.fills}</td>
        ${showMtm ? `
          <td>${row.max_profit != null ? `<span class="${pnlClass(row.max_profit)}">${fmtSignedMoney(row.max_profit)}</span>` : '—'}</td>
          <td>${row.max_loss != null ? `<span class="${pnlClass(row.max_loss)}">${fmtSignedMoney(row.max_loss)}</span>` : '—'}</td>
        ` : ''}
      </tr>`;
    }).join('')}</tbody></table>
    </div>
  `;
}

// ── Strategy config field widgets ────────────────────────────────────
// Shared by the Deploy modal (Catalog) AND the Edit Config modal
// (Detail, Step 51) — one <div class="field"> per config key, widget
// chosen from the key's own value (boolean -> dropdown, array ->
// comma-separated token list, known enum strings -> dropdown,
// "HH:MM"-shaped strings -> a time picker, everything else -> a plain
// box) — built straight from the strategy's own registered
// default_config (Deploy) or the deployment's own currently-stored
// config (Edit), so this never drifts out of sync with what a strategy
// actually accepts the way a hand-maintained parallel schema could.
// Originally lived only in Catalog (as `_configFieldHtml`); extracted
// here once a second, independent form (Detail's Edit Config modal)
// needed the exact same per-key widget logic — the STATEFUL pieces
// around it (which container/textarea/toggle ids, the current
// _configBase) stay separate per caller, only this pure
// key/value-in, HTML-out piece is actually shared.

// String-valued config keys with a real, fixed set of valid values —
// verified directly against each strategy's own source (pivot_type/
// atr_smoothing's docstrings in pivot_supertrend.py, expiry_selector's
// accepted selectors in options/resolver.py's _resolve_expiry, and
// convergence_mode's own `if ... not in (...)` validation in
// strangle_monthly_v2.py) — not guessed. Any OTHER string field (symbol,
// options_underlying, instrument, or a key none of today's strategies
// use yet) falls back to a plain text box instead of a dropdown.
const CONFIG_ENUM_OPTIONS = {
  pivot_type: ['classic', 'fibonacci', 'camarilla', 'woodie'],
  atr_smoothing: ['wilder', 'sma', 'ema'],
  expiry_selector: ['THIS_WEEK', 'NEXT_WEEK', 'THIS_MONTH', 'NEXT_MONTH'],
  convergence_mode: ['fixed_stop', 'trailing_stop', 'active_management'],
};
const CONFIG_TIME_FIELD_RE = /^\d{2}:\d{2}$/;

// idPrefix keeps the Deploy modal's fields (cfgField_*) and the Edit
// Config modal's fields (editCfgField_*, say) from colliding on
// element ids -- both modals exist in the DOM at once (just hidden via
// .open), so duplicate ids would be invalid HTML even though nothing
// today does a global getElementById lookup on them specifically.
function configFieldHtml(key, value, idPrefix = 'cfgField_') {
  const label = escapeHtml(key.replace(/_/g, ' '));
  const id = `${idPrefix}${key}`;

  if (CONFIG_ENUM_OPTIONS[key]) {
    const opts = CONFIG_ENUM_OPTIONS[key]
      .map(o => `<option value="${o}" ${o === value ? 'selected' : ''}>${o}</option>`).join('');
    return `<div class="field"><label for="${id}">${label}</label>` +
      `<select id="${id}" data-cfg-key="${escapeHtml(key)}" data-cfg-type="string">${opts}</select></div>`;
  }
  if (typeof value === 'boolean') {
    return `<div class="field"><label for="${id}">${label}</label>` +
      `<select id="${id}" data-cfg-key="${escapeHtml(key)}" data-cfg-type="boolean">` +
      `<option value="true" ${value === true ? 'selected' : ''}>true</option>` +
      `<option value="false" ${value === false ? 'selected' : ''}>false</option>` +
      `</select></div>`;
  }
  if (Array.isArray(value)) {
    // instrument_tokens is the one array-shaped field across every
    // strategy's default_config today — a comma-separated list of
    // numbers in, a real array of numbers back out on submit.
    return `<div class="field"><label for="${id}">${label} (comma-separated)</label>` +
      `<input type="text" id="${id}" data-cfg-key="${escapeHtml(key)}" data-cfg-type="number-array" ` +
      `value="${escapeHtml(value.join(', '))}" placeholder="256265, 260105"></div>`;
  }
  if (typeof value === 'number') {
    const step = Number.isInteger(value) ? '1' : 'any';
    return `<div class="field"><label for="${id}">${label}</label>` +
      `<input type="number" id="${id}" data-cfg-key="${escapeHtml(key)}" data-cfg-type="number" step="${step}" value="${value}"></div>`;
  }
  if (typeof value === 'string' && CONFIG_TIME_FIELD_RE.test(value)) {
    return `<div class="field"><label for="${id}">${label} (HH:MM)</label>` +
      `<input type="time" id="${id}" data-cfg-key="${escapeHtml(key)}" data-cfg-type="string" value="${value}"></div>`;
  }
  // Plain string fallback — symbol, options_underlying, instrument,
  // or any key none of today's strategies use yet.
  return `<div class="field"><label for="${id}">${label}</label>` +
    `<input type="text" id="${id}" data-cfg-key="${escapeHtml(key)}" data-cfg-type="string" value="${escapeHtml(String(value))}"></div>`;
}

// Builds the whole fields container's innerHTML for one config object
// -- shared by both modals' render step. `idPrefix` forwarded straight
// to configFieldHtml above.
function configFieldsContainerHtml(config, idPrefix = 'cfgField_') {
  const simpleKeys = Object.keys(config).filter(k => config[k] !== null && config[k] !== undefined);
  const advancedOnlyKeys = Object.keys(config).filter(k => config[k] === null || config[k] === undefined);
  if (!simpleKeys.length && !advancedOnlyKeys.length) {
    return emptyHtml('This strategy has no default config fields — use Advanced to add any.');
  }
  return simpleKeys.map(k => configFieldHtml(k, config[k], idPrefix)).join('')
    + (advancedOnlyKeys.length
        ? `<div class="table-note">${advancedOnlyKeys.map(k => `<code>${escapeHtml(k)}</code>`).join(', ')} ` +
          `left at ${advancedOnlyKeys.length > 1 ? 'their' : 'its'} default (null) — switch to Advanced to set ` +
          `${advancedOnlyKeys.length > 1 ? 'them' : 'it'}.</div>`
        : '');
}

// Reads a fields container back into a config object -- shared read
// step. Starts from `configBase` (not an empty {}) so advanced/null-
// valued keys the simple form never showed still round-trip into the
// result untouched, rather than silently disappearing because the box
// editor never displayed them.
function readConfigFromFields(containerId, configBase) {
  const config = { ...configBase };
  document.querySelectorAll(`#${containerId} [data-cfg-key]`).forEach(el => {
    const key = el.dataset.cfgKey;
    const type = el.dataset.cfgType;
    const raw = el.value;
    if (type === 'boolean') config[key] = raw === 'true';
    else if (type === 'number') config[key] = raw === '' ? null : Number(raw);
    else if (type === 'number-array') {
      config[key] = raw.split(',').map(s => s.trim()).filter(s => s !== '').map(Number);
    } else config[key] = raw;
  });
  return config;
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
