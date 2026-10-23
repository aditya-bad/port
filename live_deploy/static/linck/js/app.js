// Linck boot: header, index strip, phone bar, routing, live updates.

import { api } from './core/api.js';
import { html, mount, $, on } from './core/dom.js';
import { inr, pct, price, hhmmss, tone, dayKey, dDay } from './core/format.js';
import * as prefs from './core/prefs.js';
import { BASES, basis, setBasis, cycleBasis, onBasis } from './core/basis.js';
import { parse, go, href } from './core/router.js';
import { listen } from './core/store.js';
import * as book from './data/book.js';
import { INDICES } from './data/config.js';
import { prices, startTicks, startEvents, pollStatus, primeQuotes, kite, backfill, health } from './data/live.js';
import { loadDismissed } from './data/alerts.js';
import { openAlerts, alertCount } from './ui/alertspanel.js';
import { installTooltip } from './ui/tooltip.js';
import { startInstall } from './data/install.js';
import { openDialog, isOpen, closeDialog } from './ui/dialog.js';
import { say } from './ui/toast.js';
import { closeEverything, kiteDialog } from './actions.js';

const NAV = [
  ['desk', 'Desk', ''], ['deployments', 'Deployments', ''], ['repair', 'Repair', ''], ['leaderboard', 'Leaderboard', ''], ['compare', 'Compare', ''],
  ['reports', 'Reports', 'hide-1320'], ['review', 'Review', 'hide-1320'], ['strategies', 'Strategies', 'hide-1320'], ['journal', 'Journal', 'hide-1640'], ['tools', 'Tools', 'hide-1640'],
];
const BNAV = [['desk', 'Desk'], ['deployments', 'Deploys'], ['leaderboard', 'Leaders'], ['compare', 'Compare']];
const IN_MORE = ['repair', 'reports', 'review', 'strategies', 'journal', 'tools', 'account'];
// Open health issues (app/ops_health.py): a count on the Repair link, red
// when any is critical and not snoozed.
function issueBadge() {
  const live = health.issues.filter(x => !x.snoozed_until || new Date(x.snoozed_until) <= new Date());
  if (!health.issues.length) return '';
  const hot = live.some(x => x.severity === 'critical');
  return html`<i class="nav-badge ${hot ? 'hot' : ''}" aria-label="${health.issues.length} open health issue${health.issues.length === 1 ? '' : 's'}">${health.issues.length}</i>`;
}

const app = { route: null, page: null, cleanup: null, me: null };

// ── Header ─────────────────────────────────────────────────────────
function kiteChip() {
  const s = kite.state;
  if (s === 'connected') return html`<button type="button" class="kite-chip" data-act="kite" aria-label="Kite connected"><i></i>Kite connected</button>`;
  if (s === 'connecting') return html`<button type="button" class="kite-chip wait" data-act="kite" aria-label="Connecting to Kite"><i></i>Connecting…</button>`;
  if (s === 'stalled') return html`<button type="button" class="kite-chip wait" data-act="kite" aria-label="Prices stopped, reconnecting" data-tip="${`No prices from Kite for ${Math.round((kite.stalledFor || 0) / 60)} min\nThe connection looks open but nothing arrives. Reconnecting automatically.`}"><i></i><span class="long">Prices stopped · reconnecting</span><span class="short">Reconnecting</span></button>`;
  return html`<button type="button" class="kite-chip off" data-act="kite" aria-label="Kite expired, reconnect"><i></i><span class="long">Kite expired · Reconnect</span><span class="short">Reconnect</span></button>`;
}
// While the daily option-history backfill runs: a slim strip under the
// header with its progress (the header row itself has no room to spare),
// linking to Account where the details are.
function backfillStrip() {
  const b = backfill.running;
  if (!b) return '';
  const pct = b.total ? Math.round(b.done / b.total * 100) : 0;
  return html`<a class="bf-strip" href="${href('account?s=history')}" role="status" aria-live="polite">
    <span class="bf-fill" style="width:${pct}%"></span>
    <i class="spin" aria-hidden="true"></i><b>Saving <span class="hide-phone">${b.day === dayKey(new Date()) ? 'today’s' : dDay(`${b.day}T12:00:00+05:30`)} </span>option history</b>
    <span class="mono">${b.done} of ${b.total || '…'}</span>
    ${b.current ? html`<span class="mono mute hide-phone ell">${b.current}</span>` : ''}
    <span class="grow"></span><span class="hide-phone small">Details in Account ›</span></a>`;
}
// P&L basis is always one tap away: full labels on wide screens, the
// short stamps on laptops, one cycling chip on tablets and phones
// (breakpoints in shell.css).
function basisControl() {
  const cur = basis(), b = BASES.find(x => x.k === cur);
  const seg = (cls, lab) => html`<div class="seg basis-seg ${cls}" role="group" aria-label="P&L basis">${BASES.map(x => html`
    <button type="button" class="${x.k === cur ? 'on' : ''}" aria-pressed="${x.k === cur}" data-act="basis" data-v="${x.k}" data-tip="${x.long || x.l}\n${x.tip}">${lab(x)}</button>`)}</div>`;
  return html`${seg('basis-full', x => x.l)}${seg('basis-stamps', x => x.stamp)}
    <button type="button" class="basis-chip" data-act="basisNext" aria-label="P&L basis: ${b.long || b.l}. Tap to switch." data-tip="${b.long || b.l}\n${b.tip}\nTap to switch">${b.stamp}<i aria-hidden="true">⇄</i></button>`;
}
function renderHeader() {
  const cur = app.route ? app.route.nav : 'desk';
  const n = alertCount();
  const initials = app.me ? app.me.username.slice(0, 2) : '··';
  mount($('#top'), html`
    <header class="hdr">
      <a class="wordmark" href="${href('desk')}" aria-label="Linck, go to the Desk">Linck</a>
      <nav class="nav hide-phone" aria-label="Main">${NAV.map(([k, l, c]) => html`
        <a href="${href(k)}" class="${k === cur ? 'on' : ''} ${c}" ${k === cur ? html`aria-current="page"` : ''}>${l}${k === 'repair' ? issueBadge() : ''}</a>`)}</nav>
      <span class="spacer"></span>
      ${basisControl()}
      ${kiteChip()}
      <button type="button" class="theme-btn hide-1520" data-act="theme" title="Theme"><i></i>${prefs.themeLabel()}</button>
      <button type="button" class="alerts-btn" data-act="alerts" title="Things that need you" aria-label="${n} alert${n === 1 ? '' : 's'}: open the list"><i class="${n ? '' : 'zero'}">${n}</i><span class="hide-phone">Alerts</span></button>
      <button type="button" class="btn btn-sm only-mid" data-act="more">More</button>
      <a class="avatar ${cur === 'account' ? 'on' : ''}" href="${href('account')}" title="Account">${initials}</a>
    </header>
    ${backfillStrip()}
    <div class="strip-idx" id="strip" aria-label="Indices"></div>`);
  renderStrip();
  renderBnav();
}
function renderStrip() {
  const el = $('#strip');
  if (!el) return;
  const live = kite.state === 'connected';
  el.classList.toggle('frozen', !live);
  const cells = INDICES.map(i => {
    const t = prices.get(i.token);
    const ch = t && t.prevClose ? (t.price / t.prevClose - 1) * 100 : null;
    return html`<span class="ix"><b>${i.n}</b><span>${t ? price(t.price) : '—'}</span><span class="${tone(ch)}">${ch == null ? '' : pct(ch)}</span></span>`;
  });
  mount(el, html`${cells}<span class="grow"></span>${live
    ? html`<span class="state on"><i></i>Live · ${hhmmss(new Date())} IST</span>`
    : html`<span class="state off"><i></i>Not live · last known prices</span>`}`);
}
function renderBnav() {
  const cur = app.route ? app.route.nav : 'desk';
  const n = alertCount();
  mount($('#bnav'), html`${BNAV.map(([k, l]) => html`
    <a href="${href(k)}" class="${k === cur ? 'on' : ''}"><span class="n">${k === 'desk' && n ? n : ''}</span>${l}</a>`)}
    <button type="button" class="${IN_MORE.includes(cur) ? 'on' : ''}" data-act="more"><span class="n">${health.issues.length || ''}</span>More</button>`);
}

export function openMore() {
  const b = BASES.find(x => x.k === basis());
  openDialog({
    kicker: 'MENU', title: 'More', cancelLabel: 'Close',
    menu: [
      { l: `Repair${health.issues.length ? ` · ${health.issues.length} open ${health.issues.length === 1 ? 'alert' : 'alerts'}` : ''}`, h: 'The day check and what the deployments reported: what needs fixing', run: () => go('repair') },
      { l: 'Reports', h: 'Statements, account equity, exposure', run: () => go('reports') },
      { l: 'Week in review', h: 'The week against the usual: deployments, trades, rules, notes', run: () => go('review') },
      { l: 'Strategies', h: 'Catalogue and new deployments', run: () => go('strategies') },
      { l: 'Journal', h: 'Notes on days, deployments and trades', run: () => go('journal') },
      { l: 'Tools', h: 'Brokerage, SIP, CAGR, instruments', run: () => go('tools') },
      { l: 'Account', h: 'Kite, emergency stop, appearance, notifications, users, server', run: () => go('account') },
      { l: `Theme: ${prefs.themeLabel()}`, h: 'Switch Paper, Slate, Night', run: () => { prefs.cycleTheme(); openMore(); } },
      { l: `P&L basis: ${b.long || b.l}`, h: 'Switch Gross, After charges, + Slippage', run: () => { cycleBasis(); openMore(); } },
      { l: 'Close everything…', h: 'Emergency exit of every open position', run: closeEverything },
      { l: 'Classic view', h: 'The previous interface, same data', run: toClassic },
    ],
  });
}
export function toClassic() {
  try { localStorage.setItem('uiExperience', 'classic'); } catch (e) {}
  window.location.href = '/';
}

// ── Pages ──────────────────────────────────────────────────────────
let navs = 0;
async function route() {
  const nav = ++navs;
  const { route: r, params } = parse();
  const changed = !app.route || app.route.path !== r.path;
  app.route = r;
  if (app.cleanup) { try { app.cleanup(); } catch (e) { /* page already gone */ } app.cleanup = null; }
  if (isOpen()) closeDialog();
  const main = $('#main');
  geekChrome(!!r.geek);
  let mod;
  try { mod = (await r.load()).default; } catch (e) {
    mount(main, html`<div class="page"><div class="empty"><b>This page didn’t load</b><span>${e.message}</span></div></div>`);
    bootDone();
    return;
  }
  if (nav !== navs) return;   // navigated away while loading (to another page, or the same one with other params)
  app.page = mod;
  document.title = `${mod.title ? `${typeof mod.title === 'function' ? mod.title(params) : mod.title} · ` : ''}Linck`;
  try {
    app.cleanup = mod.render(main, params) || null;
  } catch (e) {
    console.error(e);
    mount(main, html`<div class="page"><div class="empty"><b>Something went wrong on this page</b><span>${e.message}</span></div></div>`);
  }
  if (changed) window.scrollTo(0, 0);
  renderHeader();
  bootDone();
}

// The boot screen (index.html #boot) covers the first paint until the
// first page has rendered, then fades out.
function bootDone() {
  const b = $('#boot');
  if (!b || b.classList.contains('done')) return;
  b.classList.add('done');
  setTimeout(() => b.remove(), 400);
}

// Geek mode (js/data/geek.js) owns the whole screen: the header, index
// strip and phone bar step aside; the theme stays the one chosen.
function geekChrome(on) {
  const b = document.body, was = b.classList.contains('geek-mode');
  b.classList.toggle('geek-mode', on);
  document.documentElement.toggleAttribute('data-geek', on);
  b.classList.remove('geek-leaving');
  if (was && !on) {
    b.classList.add('geek-return');
    setTimeout(() => b.classList.remove('geek-return'), 600);
  }
}

// ── Boot ───────────────────────────────────────────────────────────
async function boot() {
  startInstall();
  prefs.apply();
  installTooltip();
  renderHeader();
  const top = $('#top');
  const handlers = {
    basis: d => setBasis(d.v),
    basisNext: () => { cycleBasis(); const b = BASES.find(x => x.k === basis()); say(`P&L now shown ${b.k === 'gross' ? 'gross, before charges' : b.k === 'charges' ? 'after charges' : 'after charges and slippage'}.`); },
    theme: () => prefs.cycleTheme(),
    kite: () => kiteDialog(),
    more: () => openMore(),
    alerts: () => openAlerts(),
  };
  const click = (e, el) => { const fn = handlers[el.dataset.act]; if (fn) { e.preventDefault(); fn(el.dataset); } };
  on(top, 'click', '[data-act]', click);
  on($('#bnav'), 'click', '[data-act]', click);

  window.addEventListener('hashchange', route);
  onBasis(() => { renderHeader(); route(); });
  prefs.subscribe(() => renderHeader());
  listen('status', () => { renderHeader(); });
  // a health alert: when an issue opens and when it clears. The phone is
  // pushed every minute while it's open; a tab shows it once, and it waits
  // in the Alerts list.
  listen('event', ev => {
    if (ev.event_type === 'health_resolved' || (ev.event_type === 'health_issue' && !(ev.metadata && ev.metadata.repeat))) say(ev.message, null, 8000);
  });
  listen('alerts', () => renderHeader());
  listen('data', () => renderHeader());
  listen('ticks', () => renderStrip());
  setInterval(renderStrip, 1000);

  startTicks();
  startEvents();
  pollStatus();
  setInterval(pollStatus, 20000);
  primeQuotes(INDICES.map(i => i.token));
  prefs.sync();
  loadDismissed();
  api.me().then(me => { app.me = me; renderHeader(); }).catch(() => {});

  route();
  await book.load();
}
boot();
