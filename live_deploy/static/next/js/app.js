// Desk — app shell: top bar, status strip, router, palette, boot.
import { html, raw, mount, $, on, debounce, session, money, pct, tone, ICONS } from './util.js';
import { api, pnlMode, setPnlMode, MODE_LABEL } from './api.js';
import { menu, openPalette, toast, toastError, anyModalOpen } from './ui.js';
import { bus, prices, startTicks, startEvents, primeQuotes, pollHealth, status, loginWithKite } from './live.js';

import Today from './pages/today.js';
import Strategies from './pages/strategies.js';
import Detail from './pages/detail.js';
import Performance from './pages/performance.js';
import Deploy from './pages/deploy.js';
import Tools from './pages/tools.js';
import Settings from './pages/settings.js';

const NAV = [
  { id: 'today', label: 'Today', href: '#/today', icon: ICONS.today },
  { id: 'strategies', label: 'Strategies', href: '#/strategies', icon: ICONS.list },
  { id: 'performance', label: 'Performance', href: '#/performance', icon: ICONS.chart },
  { id: 'deploy', label: 'Deploy', href: '#/deploy', icon: ICONS.plus },
  { id: 'tools', label: 'Tools', href: '#/tools' },
  { id: 'settings', label: 'Settings', href: '#/settings', icon: ICONS.menu, mobileLabel: 'More' },
];
const TAPE = [
  { token: 256265, label: 'NIFTY' },
  { token: 265, label: 'SENSEX' },
  { token: 260105, label: 'BANKNIFTY' },
];

// ── Routing ────────────────────────────────────────────────────────
function parseRoute() {
  const parts = (location.hash || '#/today').replace(/^#\/?/, '').split('/').filter(Boolean);
  const [head = 'today', ...rest] = parts;
  if (head === 's' && rest[0]) return { page: Detail, nav: 'strategies', params: { id: rest[0], tab: rest[1] || 'overview', sub: rest[2] } };
  const map = { today: Today, strategies: Strategies, performance: Performance, deploy: Deploy, tools: Tools, settings: Settings };
  const page = map[head] || Today;
  return { page, nav: map[head] ? head : 'today', params: { tab: rest[0], sub: rest[1] } };
}

let current = { cleanup: null, page: null, key: '' };
export async function render() {
  const route = parseRoute();
  const main = $('#main');
  const key = location.hash;
  if (current.cleanup) { try { current.cleanup(); } catch (e) { /* ignore */ } }
  current = { cleanup: null, page: route.page, key, params: route.params };
  paintNav(route.nav);
  document.title = `${route.page.title || 'Desk'} · live_deploy`;
  main.scrollTop = 0;
  if (!route.page.keepScroll) window.scrollTo(0, 0);
  try {
    const cleanup = await route.page.render(main, route.params);
    if (current.key === key) current.cleanup = cleanup || null;
    else if (cleanup) cleanup();
  } catch (e) {
    console.error(e);
    mount(main, html`<div class="card"><div class="empty"><b>This page couldn't load</b>${e.message || String(e)}
      <div style="margin-top:12px"><button class="btn" id="retry">Try again</button></div></div></div>`);
    $('#retry', main).onclick = render;
  }
}
// Pages that show server data refresh when something changes server-side
// (a fill, a pause...). Debounced so a burst of fills is one refresh.
const refreshCurrent = debounce(() => {
  if (anyModalOpen() || document.hidden) return;
  if (current.page && current.page.onChanged) current.page.onChanged(current.params);
}, 1500);

// ── Shell ──────────────────────────────────────────────────────────
function paintShell() {
  mount($('#topbar'), html`
    <div class="topbar-row">
      <a class="brand" href="#/today" aria-label="live_deploy home">
        <span class="brand-mark">L</span>
        <span>live_deploy<small>Paper trading desk</small></span>
      </a>
      <nav class="nav" aria-label="Primary">${NAV.map(n => html`<a href="${n.href}" data-nav="${n.id}">${n.label}</a>`)}</nav>
      <div class="topbar-spacer"></div>
      <div class="topbar-tools">
        <button class="btn hide-mobile" id="openPalette" title="Search (Ctrl/⌘ K)">${raw(ICONS.search)}<span class="muted">Search</span><span class="kbd">⌘K</span></button>
        <button class="btn icon only-mobile" id="openPaletteM" aria-label="Search">${raw(ICONS.search)}</button>
        <div class="seg hide-mobile" id="basisSeg" role="group" aria-label="P&amp;L basis"></div>
        <button class="btn icon" id="accountBtn" aria-label="Account and preferences">${raw(ICONS.user)}</button>
      </div>
    </div>
    <div class="status-strip">
      <button class="btn ghost sm" id="kiteBtn" style="padding:0 6px"><span class="dot" id="kiteDot"></span><span id="kiteLabel">Checking Kite…</span></button>
      <span class="session-label" id="sessionLabel"></span>
      <div class="topbar-spacer hide-mobile"></div>
      <div class="tape" id="tape">${TAPE.map(t => html`<span class="tape-item" data-token="${t.token}"><span>${t.label}</span><b>—</b><span class="chg"></span></span>`)}</div>
    </div>
    <div class="session-line" id="sessionLine"><i></i></div>`);
  mount($('#bottomNav'), html`${NAV.filter(n => n.icon).map(n => html`<a href="${n.href}" data-nav="${n.id}">${raw(n.icon)}<span>${n.mobileLabel || n.label}</span></a>`)}`);

  $('#openPalette').onclick = palette;
  $('#openPaletteM').onclick = palette;
  $('#accountBtn').onclick = e => accountMenu(e.currentTarget);
  $('#kiteBtn').onclick = () => {
    if (!status.kite) loginWithKite().catch(e => toastError(e, 'Kite login failed'));
    else location.hash = '#/settings';
  };
  paintBasis();
  on($('#basisSeg'), 'click', 'button', (e, b) => setBasis(b.dataset.mode));
}

function paintNav(active) {
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('active', a.dataset.nav === active));
}

function paintBasis() {
  const m = pnlMode();
  const short = { gross: 'Gross', charges: 'Net', all: 'Net + slip' };
  mount($('#basisSeg'), html`${['gross', 'charges', 'all'].map(k =>
    html`<button data-mode="${k}" class="${m === k ? 'on' : ''}" title="${MODE_LABEL[k]}" aria-pressed="${m === k}">${short[k]}</button>`)}`);
}
export function setBasis(m) {
  setPnlMode(m);
  paintBasis();
  render();
}

function paintStatus() {
  const dot = $('#kiteDot'), label = $('#kiteLabel');
  if (!dot) return;
  if (status.kite == null) { dot.className = 'dot'; label.textContent = 'Checking Kite…'; return; }
  const s = session();
  if (status.kite) {
    const fresh = status.lastTickAt && Date.now() - new Date(status.lastTickAt).getTime() < 90_000;
    dot.className = `dot ${s.open && fresh ? 'live' : 'ok'}`;
    label.textContent = s.open ? (fresh ? 'Live' : 'Kite connected · waiting for ticks') : 'Kite connected';
  } else {
    dot.className = 'dot bad';
    label.textContent = status.needsLogin ? 'Kite login needed — tap to log in' : 'Kite disconnected — tap to reconnect';
  }
}
function paintSession() {
  const s = session();
  const el = $('#sessionLabel');
  if (el) el.textContent = s.label;
  const line = $('#sessionLine');
  if (line) {
    line.classList.toggle('closed', !s.open);
    line.firstElementChild.style.width = `${Math.round(s.progress * 1000) / 10}%`;
    line.title = s.open ? `Session ${Math.round(s.progress * 100)}% done` : s.label;
  }
}
function paintTape(tokens) {
  for (const t of TAPE) {
    if (tokens && !tokens.includes(t.token)) continue;
    const q = prices.get(t.token);
    const el = document.querySelector(`.tape-item[data-token="${t.token}"]`);
    if (!el || !q) continue;
    el.querySelector('b').textContent = q.price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const chg = el.querySelector('.chg');
    if (q.prevClose) {
      const p = (q.price - q.prevClose) / q.prevClose * 100;
      chg.textContent = pct(p, { sign: true });
      chg.className = `chg ${tone(p)}`;
    }
  }
}

// ── Account menu & palette ─────────────────────────────────────────
function theme() { try { return localStorage.getItem('nx.theme') || 'system'; } catch (e) { return 'system'; } }
export function setTheme(t) {
  try { localStorage.setItem('nx.theme', t); } catch (e) { /* private mode */ }
  if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
  else document.documentElement.removeAttribute('data-theme');
  bus.dispatchEvent(new CustomEvent('theme'));
}
export function switchToClassic(hash = '') {
  try { localStorage.setItem('uiExperience', 'classic'); } catch (e) { /* ignore */ }
  location.href = `/${hash}`;
}
export async function signOut() {
  try { await api.logout(); } catch (e) { /* already signed out */ }
  location.href = '/';
}
function accountMenu(anchor) {
  const t = theme(), m = pnlMode();
  menu(anchor, [
    { heading: 'P&L basis' },
    ...['gross', 'charges', 'all'].map(k => ({ label: `${m === k ? '✓ ' : '   '}${MODE_LABEL[k]}`, onClick: () => setBasis(k) })),
    { sep: true },
    { heading: 'Theme' },
    ...[['system', 'Match system'], ['light', 'Light'], ['dark', 'Dark']].map(([k, l]) => ({ label: `${t === k ? '✓ ' : '   '}${l}`, onClick: () => setTheme(k) })),
    { sep: true },
    { label: 'Settings', onClick: () => { location.hash = '#/settings'; } },
    { label: 'Switch to classic experience', onClick: () => switchToClassic() },
    { label: 'Sign out', danger: true, onClick: signOut },
  ]);
}
let cachedDeployments = [];
function palette() {
  openPalette(() => [
    ...NAV.map(n => ({ group: 'Pages', label: n.label, run: () => { location.hash = n.href; } })),
    ...cachedDeployments.map(d => ({
      group: 'Strategies', label: d.deployment_name, hint: `${d.strategy_name} · ${d.status}`,
      run: () => { location.hash = `#/s/${d.id}`; },
    })),
    { group: 'Actions', label: 'Deploy a new strategy', run: () => { location.hash = '#/deploy'; } },
    { group: 'Actions', label: status.kite ? 'Kite is connected' : 'Log in to Kite', run: () => { if (!status.kite) loginWithKite(); } },
    { group: 'Actions', label: 'P&L basis: Gross', run: () => setBasis('gross') },
    { group: 'Actions', label: 'P&L basis: After charges', run: () => setBasis('charges') },
    { group: 'Actions', label: 'P&L basis: After charges + slippage', run: () => setBasis('all') },
    { group: 'Actions', label: 'Theme: Light', run: () => setTheme('light') },
    { group: 'Actions', label: 'Theme: Dark', run: () => setTheme('dark') },
    { group: 'Actions', label: 'Theme: Match system', run: () => setTheme('system') },
    { group: 'Actions', label: 'Switch to classic experience', run: () => switchToClassic() },
  ]);
  api.deployments().then(d => { cachedDeployments = d; }).catch(() => {});
}
export function rememberDeployments(list) { cachedDeployments = list || []; }

// ── Boot ───────────────────────────────────────────────────────────
function boot() {
  paintShell();
  startTicks();
  startEvents();
  pollHealth();
  setInterval(pollHealth, 20_000);
  primeQuotes(TAPE.map(t => t.token));
  setInterval(() => primeQuotes(TAPE.map(t => t.token)), 60_000);
  paintSession();
  setInterval(() => { paintSession(); paintStatus(); }, 30_000);
  bus.addEventListener('status', paintStatus);
  bus.addEventListener('ticks', e => paintTape(e.detail));
  bus.addEventListener('changed', refreshCurrent);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { pollHealth(); refreshCurrent(); } });
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); palette(); }
    else if (e.key === '/' && !/input|textarea|select/i.test(document.activeElement.tagName) && !anyModalOpen()) { e.preventDefault(); palette(); }
  });
  window.addEventListener('hashchange', render);
  api.deployments().then(d => { cachedDeployments = d; }).catch(() => {});
  render();
}
boot();
export { toast };
