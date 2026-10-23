// Every endpoint Linck calls, behind one fetch wrapper. v1 endpoints are
// the ones the classic UI uses; /v2 ones are additive (app/v2/).

// passwordCheck: the endpoint answers a wrong re-entered password with 401;
// that's an error to show, not a sign that the session ended.
async function req(path, { method = 'GET', body, passwordCheck = false } = {}) {
  const opts = { method, headers: {}, credentials: 'same-origin' };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let r;
  try {
    r = await fetch(path, opts);
  } catch (e) {
    throw new Error('Can’t reach the server. It may be restarting; try again in a moment.');
  }
  if (r.status === 401 && !passwordCheck) {
    window.location.href = `/linck/${window.location.hash}`;   // Linck's sign-in, then back here
    throw new Error('Signed out');
  }
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  if (!r.ok) {
    let detail = data && (data.detail || data.message);
    if (Array.isArray(detail)) detail = detail.map(d => d.msg).join('; ');
    throw new Error(typeof detail === 'string' && detail ? detail : `Request failed (${r.status})`);
  }
  return data;
}
const enc = encodeURIComponent;
const post = (path, body) => req(path, { method: 'POST', body });
const postPw = (path, body) => req(path, { method: 'POST', body, passwordCheck: true });

export const api = {
  // session
  health: () => req('/health'),
  // the health monitor's alerts (app/ops_health.py): open ones on the Repair page
  ops: () => req('/v2/ops'),
  snoozeIssue: (id, period) => post(`/v2/ops/issues/${id}/snooze`, { for: period }),
  dismissIssue: id => post(`/v2/ops/issues/${id}/dismiss`),
  strategyIssues: (all = false) => req(`/v2/strategy-issues${all ? '?all=1' : ''}`),
  dismissStrategyIssue: id => post(`/v2/strategy-issues/${id}/dismiss`),
  // the end-of-day check (app/repair/daycheck.py)
  dayChecks: day => req(`/v2/day-checks${day ? `?day=${enc(day)}` : ''}`),
  runDayCheck: body => post('/v2/day-checks/run', body || {}),
  // repairing a flagged day (app/repair/dayrepair.py)
  repairCase: (day, dep) => req(`/v2/repair-cases/${enc(day)}/${enc(dep)}`),
  planRepair: (day, dep, choices) => post(`/v2/repair-cases/${enc(day)}/${enc(dep)}/plan`, { choices }),
  pushRepair: (day, dep) => post(`/v2/repair-cases/${enc(day)}/${enc(dep)}/push`),
  repairJob: id => req(`/v2/repair-jobs/${id}`),
  undoRepair: id => post(`/v2/repairs/${id}/undo`),
  dismissDay: (day, dep, undo = false) => post(`/v2/repair-cases/${enc(day)}/${enc(dep)}/dismiss`, { undo }),
  me: () => req('/auth/me'),
  logout: () => post('/auth/logout'),
  logoutEverywhere: () => post('/auth/logout-everywhere'),
  changePassword: (old_password, new_password) => postPw('/auth/change-password', { old_password, new_password }),
  users: () => req('/auth/users'),
  auditLog: (offset = 0, limit = 50) => req(`/auth/audit-log?offset=${offset}&limit=${limit}`),
  redeploy: (password, confirm) => postPw('/admin/redeploy', { password, confirm }),
  clearAll: (password, confirm) => postPw('/deployments/clear-all', { password, confirm }),
  createUser: (username, password) => post('/auth/users', { username, password }),
  kiteLoginUrl: () => req('/kite/login-url'),
  kiteStatus: () => req('/kite/status'),
  kiteManualLogin: body => post('/kite/manual-login', body),
  // option price history (app/candles.py)
  backfillStatus: () => req('/candles/backfill'),
  runBackfill: () => post('/candles/backfill', {}),

  // deployments (v1)
  deployments: () => req('/deployments'),
  update: (id, body) => req(`/deployments/${id}`, { method: 'PATCH', body }),
  pause: id => post(`/deployments/${id}/pause`),
  resume: id => post(`/deployments/${id}/resume`),
  stop: (id, closeFirst) => post(`/deployments/${id}/stop?force_close=${closeFirst ? 'true' : 'false'}`),
  flatten: id => post(`/deployments/${id}/flatten`),
  removeTrades: id => post(`/deployments/${id}/remove-trades`),
  remove: id => post(`/deployments/${id}/delete`),
  positions: (id, status = 'all') => req(`/deployments/${id}/positions?status=${status}`),
  lots: (id, limit = 200) => req(`/deployments/${id}/trades?limit=${limit}`),
  digest: (id, period = 'day', limit = 30) => req(`/deployments/${id}/pnl-digest?period=${period}&limit=${limit}`),
  adjustmentHistogram: id => req(`/deployments/${id}/adjustment-histogram`),
  events: (id, limit = 200) => req(`/deployments/${id}/events?limit=${limit}`),
  strategyStatus: id => req(`/deployments/${id}/strategy-status`),
  cycles: id => req(`/deployments/${id}/cycles`),
  geek: (id, view) => req(`/v2/deployments/${id}/geek/${view}`),
  underlying: id => req(`/v2/deployments/${id}/underlying`),
  repairs: id => req(`/v2/deployments/${id}/repairs`),
  // the trade journal (app/routers/journal.py)
  journal: (q = {}) => req(`/v2/journal?${new URLSearchParams(Object.entries(q).filter(([, v]) => v != null && v !== '')).toString()}`),
  journalTags: depId => req(`/v2/journal/tags${depId ? `?deployment_id=${enc(depId)}` : ''}`),
  addNote: body => post('/v2/journal', body),
  editNote: (id, body) => req(`/v2/journal/${enc(id)}`, { method: 'PATCH', body }),
  deleteNote: id => req(`/v2/journal/${enc(id)}`, { method: 'DELETE' }),
  cycleChart: (id, key) => req(`/deployments/${id}/cycles/${enc(key)}/chart`),
  openPositions: () => req('/positions?status=open'),
  recentTrades: (limit = 60) => req(`/trades/recent?limit=${limit}`),

  // catalogue, tags, instruments, notifications (v1)
  presets: name => req(`/strategies/${enc(name)}/presets`),
  savePreset: (name, preset_name, config) => post(`/strategies/${enc(name)}/presets`, { preset_name, config }),
  deletePreset: (name, id) => req(`/strategies/${enc(name)}/presets/${enc(id)}`, { method: 'DELETE' }),
  setStrategyEnabled: (name, enabled) => req(`/strategies/${enc(name)}/enabled`, { method: 'PUT', body: { enabled } }),
  tags: () => req('/tags'),
  createTag: name => post('/tags', { name }),
  deleteTag: id => req(`/tags/${id}`, { method: 'DELETE' }),
  instruments: () => req('/instruments'),
  searchInstruments: q => req(`/instruments/search?q=${enc(q)}`),
  quotes: tokens => req(`/instruments/quotes?tokens=${tokens.join(',')}`),
  subscribe: body => post('/instruments', body),
  unsubscribe: token => req(`/instruments/${token}`, { method: 'DELETE' }),
  vapidKey: () => req('/notifications/vapid-public-key'),
  pushSubscribe: sub => post('/notifications/subscribe', sub),
  pushUnsubscribe: sub => post('/notifications/unsubscribe', sub),
  muteStatus: () => req('/notifications/mute-status'),
  muteToday: () => post('/notifications/mute-today'),
  unmute: () => post('/notifications/unmute'),

  // v2
  book: () => req('/v2/book'),
  session: () => req('/v2/session'),
  review: week => req(`/v2/review${week ? `?week=${encodeURIComponent(week)}` : ''}`),
  vix: (q = {}) => req(`/v2/vix?${new URLSearchParams(Object.entries(q).filter(([, x]) => x != null && x !== '')).toString()}`),
  vixSpike: () => req('/v2/vix/spike'),
  vixExposure: () => req('/v2/vix/exposure'),
  // shadow mode (app/shadow.py): would Kite have taken each fill
  shadow: (id, limit = 50) => req(`/v2/deployments/${id}/shadow?limit=${limit}`),
  shadowRejections: () => req('/v2/shadow/rejections'),
  strategyHealth: (fresh = false) => req(`/v2/strategy-health${fresh ? '?fresh=true' : ''}`),
  strategies: () => req('/v2/strategies'),
  prefs: () => req('/v2/preferences'),
  savePrefs: patch => req('/v2/preferences', { method: 'PUT', body: patch }),
  // alert dismissals, per user (app/v2/alerts.py): {key: token}
  alertDismissals: () => req('/v2/alerts/dismissed'),
  changeAlertDismissals: (items = [], restore = []) => post('/v2/alerts/dismissed', { items, restore }),
  closeEverything: () => post('/v2/close-everything', { confirm: 'CLOSE ALL' }),
  create: body => post('/v2/deployments', body),
  restart: id => post(`/v2/deployments/${enc(id)}/restart`),
};
