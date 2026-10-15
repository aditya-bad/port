// Data layer: every endpoint the Desk uses, one fetch wrapper.

async function req(path, { method = 'GET', body, raw = false } = {}) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let r;
  try {
    r = await fetch(path, opts);
  } catch (e) {
    throw new Error('Network error — the server may be restarting. Try again in a moment.');
  }
  if (r.status === 401) {
    window.location.href = '/';
    throw new Error('Signed out');
  }
  if (raw) return r;
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  if (!r.ok) {
    const detail = data && (data.detail || data.message);
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${r.status})`);
  }
  return data;
}

const enc = encodeURIComponent;

export const api = {
  health: () => req('/health'),
  me: () => req('/auth/me'),
  kiteLoginUrl: () => req('/kite/login-url'),
  logout: () => req('/auth/logout', { method: 'POST' }),
  logoutEverywhere: () => req('/auth/logout-everywhere', { method: 'POST' }),
  changePassword: (old_password, new_password) =>
    req('/auth/change-password', { method: 'POST', body: { old_password, new_password } }),

  strategies: () => req('/strategies'),
  presets: name => req(`/strategies/${enc(name)}/presets`),
  savePreset: (name, presetName, config) =>
    req(`/strategies/${enc(name)}/presets`, { method: 'POST', body: { preset_name: presetName, config } }),
  deletePreset: (name, id) => req(`/strategies/${enc(name)}/presets/${id}`, { method: 'DELETE' }),

  deployments: () => req('/deployments'),
  deployment: id => req(`/deployments/${id}`),
  createDeployment: body => req('/deployments', { method: 'POST', body }),
  updateDeployment: (id, body) => req(`/deployments/${id}`, { method: 'PATCH', body }),
  pause: id => req(`/deployments/${id}/pause`, { method: 'POST' }),
  resume: id => req(`/deployments/${id}/resume`, { method: 'POST' }),
  stop: (id, forceClose) => req(`/deployments/${id}/stop?force_close=${forceClose ? 'true' : 'false'}`, { method: 'POST' }),
  flatten: id => req(`/deployments/${id}/flatten`, { method: 'POST' }),
  removeTrades: id => req(`/deployments/${id}/remove-trades`, { method: 'POST' }),
  remove: id => req(`/deployments/${id}/delete`, { method: 'POST' }),
  flattenAll: () => req('/deployments/flatten-all', { method: 'POST' }),

  positions: (id, status = 'all') => req(`/deployments/${id}/positions?status=${status}`),
  trades: (id, limit = 500) => req(`/deployments/${id}/trades?limit=${limit}`),
  events: (id, limit = 300) => req(`/deployments/${id}/events?limit=${limit}`),
  snapshots: (id, limit = 1000) => req(`/deployments/${id}/snapshots?limit=${limit}`),
  digest: (id, period = 'day', limit = 400) => req(`/deployments/${id}/pnl-digest?period=${period}&limit=${limit}`),
  strategyStatus: id => req(`/deployments/${id}/strategy-status`),
  cycles: id => req(`/deployments/${id}/cycles`),
  cycleChart: (id, key) => req(`/deployments/${id}/cycles/${enc(key)}/chart`),

  openPositions: () => req('/positions?status=open'),
  recentTrades: (limit = 80) => req(`/trades/recent?limit=${limit}`),
  activePeriods: () => req('/portfolio/active-periods'),
  report: (period, offset) => req(`/portfolio/pnl-report?period=${period}&offset=${offset}`),
  portfolioDigest: (period = 'day', limit = 60) => req(`/portfolio/pnl-digest?period=${period}&limit=${limit}`),
  equityCurve: () => req('/portfolio/equity-curve'),
  leaderboard: () => req('/portfolio/strategy-leaderboard'),

  instruments: () => req('/instruments'),
  searchInstruments: q => req(`/instruments/search?q=${enc(q)}`),
  quotes: tokens => req(`/instruments/quotes?tokens=${tokens.join(',')}`),
  subscribe: body => req('/instruments', { method: 'POST', body }),
  unsubscribe: token => req(`/instruments/${token}`, { method: 'DELETE' }),

  muteStatus: () => req('/notifications/mute-status'),
  muteToday: () => req('/notifications/mute-today', { method: 'POST' }),
  unmute: () => req('/notifications/unmute', { method: 'POST' }),
};

// ── P&L basis (shared with the classic UI via the same storage key) ──
const MODES = ['gross', 'charges', 'all'];
export const MODE_LABEL = { gross: 'Gross', charges: 'After charges', all: 'After charges + slippage' };
export function pnlMode() {
  try {
    const m = localStorage.getItem('pnlMode');
    if (MODES.includes(m)) return m;
    return localStorage.getItem('includeCharges') === 'true' ? 'charges' : 'gross';
  } catch (e) { return 'gross'; }
}
export function setPnlMode(m) {
  try { localStorage.setItem('pnlMode', m); } catch (e) {}
}
const SUFFIX = { gross: '', charges: '_net', all: '_net_slip' };
export const pnlSuffix = () => SUFFIX[pnlMode()];
// pick(row, 'realized_pnl') -> realized_pnl / _net / _net_slip per the
// current basis, falling back toward gross when a variant is missing.
export function pick(row, base) {
  if (!row) return null;
  const m = pnlMode();
  if (m === 'all' && row[`${base}_net_slip`] != null) return Number(row[`${base}_net_slip`]);
  if (m !== 'gross' && row[`${base}_net`] != null) return Number(row[`${base}_net`]);
  return row[base] != null ? Number(row[base]) : null;
}
