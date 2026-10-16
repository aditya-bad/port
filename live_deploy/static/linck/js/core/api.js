// Every endpoint Linck calls, behind one fetch wrapper. v1 endpoints are
// the ones the classic UI uses; /v2 ones are additive (app/v2/).

async function req(path, { method = 'GET', body } = {}) {
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
  if (r.status === 401) {
    window.location.href = '/';   // classic login screen, then back here
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

export const api = {
  // session
  health: () => req('/health'),
  me: () => req('/auth/me'),
  logout: () => post('/auth/logout'),
  logoutEverywhere: () => post('/auth/logout-everywhere'),
  changePassword: (old_password, new_password) => post('/auth/change-password', { old_password, new_password }),
  users: () => req('/auth/users'),
  createUser: (username, password) => post('/auth/users', { username, password }),
  kiteLoginUrl: () => req('/kite/login-url'),
  kiteStatus: () => req('/kite/status'),
  kiteManualLogin: body => post('/kite/manual-login', body),

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
  events: (id, limit = 200) => req(`/deployments/${id}/events?limit=${limit}`),
  strategyStatus: id => req(`/deployments/${id}/strategy-status`),
  cycles: id => req(`/deployments/${id}/cycles`),
  cycleChart: (id, key) => req(`/deployments/${id}/cycles/${enc(key)}/chart`),
  openPositions: () => req('/positions?status=open'),
  recentTrades: (limit = 60) => req(`/trades/recent?limit=${limit}`),

  // catalogue, tags, instruments, notifications (v1)
  presets: name => req(`/strategies/${enc(name)}/presets`),
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
  strategies: () => req('/v2/strategies'),
  prefs: () => req('/v2/preferences'),
  savePrefs: patch => req('/v2/preferences', { method: 'PUT', body: patch }),
  closeEverything: () => post('/v2/close-everything', { confirm: 'CLOSE ALL' }),
  create: body => post('/v2/deployments', body),
};
