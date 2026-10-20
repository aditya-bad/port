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
import { prices, startTicks, startEvents, pollStatus, primeQuotes, kite, backfill } from './data/live.js';
import { alerts } from './data/alerts.js';
import { installTooltip } from './ui/tooltip.js';
import { startInstall } from './data/install.js';
import { openDialog, isOpen, closeDialog } from './ui/dialog.js';
import { say } from './ui/toast.js';
import { closeEverything, kiteDialog } from './actions.js';

const NAV = [
  ['desk', 'Desk', ''], ['deployments', 'Deployments', ''], ['leaderboard', 'Leaderboard', ''], ['compare', 'Compare', ''],
  ['reports', 'Reports', 'hide-1320'], ['strategies', 'Strategies', 'hide-1320'], ['tools', 'Tools', 'hide-1320'],
];
const BNAV = [['desk', 'Desk'], ['deployments', 'Deploys'], ['leaderboard', 'Leaders'], ['compare', 'Compare']];
const IN_MORE = ['reports', 'strategies', 'tools', 'account'];

const app = { route: null, page: null, cleanup: null, me: null };

// ── Header ─────────────────────────────────────────────────────────
function kiteChip() {
  const s = kite.state;
  if (s === 'connected') return html`<button type="button" class="kite-chip" data-act="kite"><i></i>Kite connected</button>`;
  if (s === 'connecting') return html`<button type="button" class="kite-chip wait" data-act="kite"><i></i>Connecting…</button>`;
  return html`<button type="button" class="kite-chip off" data-act="kite"><i></i><span class="long">Kite expired · Reconnect</span><span class="short">Reconnect</span></button>`;
}
// While the daily option-history backfill runs: a slim strip under the
// header with its progress (the header row itself has no room to spare),
// linking to Account where the details are.
function backfillStrip() {
  const b = backfill.running;
  if (!b) return '';
  const pct = b.total ? Math.round(b.done / b.total * 100) : 0;
  return html`<a class="bf-strip" href="${href('account')}" role="status" aria-live="polite">
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
  const n = alerts().length;
  const initials = app.me ? app.me.username.slice(0, 2) : '··';
  mount($('#top'), html`
    <header class="hdr">
      <a class="wordmark" href="${href('desk')}" aria-label="Linck, go to the Desk"><img class="mark" src="/icons/linck-mark.svg" alt="" width="30" height="30">Linck</a>
      <nav class="nav hide-phone" aria-label="Main">${NAV.map(([k, l, c]) => html`
        <a href="${href(k)}" class="${k === cur ? 'on' : ''} ${c}" ${k === cur ? html`aria-current="page"` : ''}>${l}</a>`)}</nav>
      <span class="spacer"></span>
      ${basisControl()}
      ${kiteChip()}
      <button type="button" class="theme-btn hide-1520" data-act="theme" title="Theme"><i></i>${prefs.themeLabel()}</button>
      <a class="alerts-btn" href="${href('desk')}" title="Things that need you"><i class="${n ? '' : 'zero'}">${n}</i><span class="hide-phone">Alerts</span></a>
      <button type="button" class="btn btn-sm only-mid" data-act="more">More</button>
      <button type="button" class="btn btn-sm btn-primary hide-1180 hide-phone" data-act="closeAll">Close everything…</button>
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
  const n = alerts().length;
  mount($('#bnav'), html`${BNAV.map(([k, l]) => html`
    <a href="${href(k)}" class="${k === cur ? 'on' : ''}"><span class="n">${k === 'desk' && n ? n : ''}</span>${l}</a>`)}
    <button type="button" class="${IN_MORE.includes(cur) ? 'on' : ''}" data-act="more"><span class="n"></span>More</button>`);
}

export function openMore() {
  const b = BASES.find(x => x.k === basis());
  openDialog({
    kicker: 'MENU', title: 'More', cancelLabel: 'Close',
    menu: [
      { l: 'Reports', h: 'Statements, account equity, exposure', run: () => go('reports') },
      { l: 'Strategies', h: 'Catalogue and new deployments', run: () => go('strategies') },
      { l: 'Tools', h: 'Brokerage, SIP, CAGR, instruments', run: () => go('tools') },
      { l: 'Account', h: 'Kite, users, appearance, notifications, tags', run: () => go('account') },
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
async function route() {
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
  if (app.route !== r) return;   // navigated away while loading
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
    closeAll: () => closeEverything(),
  };
  const click = (e, el) => { const fn = handlers[el.dataset.act]; if (fn) { e.preventDefault(); fn(el.dataset); } };
  on(top, 'click', '[data-act]', click);
  on($('#bnav'), 'click', '[data-act]', click);

  window.addEventListener('hashchange', route);
  onBasis(() => { renderHeader(); route(); });
  prefs.subscribe(() => renderHeader());
  listen('status', () => { renderHeader(); });
  listen('data', () => renderHeader());
  listen('ticks', () => renderStrip());
  setInterval(renderStrip, 1000);

  startTicks();
  startEvents();
  pollStatus();
  setInterval(pollStatus, 20000);
  primeQuotes(INDICES.map(i => i.token));
  prefs.sync();
  api.me().then(me => { app.me = me; renderHeader(); }).catch(() => {});

  route();
  await book.load();
}
boot();
