// Account — settings in sections, one page each (#/account?s=key): an
// overview with every area's state at a glance, then Kite, the emergency
// stop, appearance, notifications, sign-in and security, the app install,
// users, tags, option price history, the activity log and the server.
// Each section opens with what it's for; risky actions live apart, in
// Emergency stop and Server & danger zone, behind a typed phrase.
// Appearance is rendered from the preference registry, so new themes and
// preferences appear on their own.

import { api } from '../core/api.js';
import { html, mount, $, actions } from '../core/dom.js';
import { ago, plural, dStamp, dDay, dayKey, hhmm, num, inr } from '../core/format.js';
import * as prefs from '../core/prefs.js';
import { ui, listen } from '../core/store.js';
import { go, href } from '../core/router.js';
import * as book from '../data/book.js';
import { kite, backfill, noticeBackfill } from '../data/live.js';
import { LAB_TAG } from '../data/config.js';
import { toggle, empty, loading } from '../ui/components.js';
import { openDialog } from '../ui/dialog.js';
import { say } from '../ui/toast.js';
import { kiteDialog, closeEverything } from '../actions.js';
import { installState, promptInstall } from '../data/install.js';
import { toClassic } from '../app.js';

const b64 = s => { const p = '='.repeat((4 - s.length % 4) % 4); const r = atob((s + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from([...r].map(c => c.charCodeAt(0))); };

// [key, title, group, what it's for]
export const SECTIONS = [
  ['overview', 'Overview', '', 'Everything on this page at a glance. Pick a card to change it.'],
  ['kite', 'Kite connection', 'Trading', 'Linck reads live prices and places orders through your Zerodha Kite login. Kite ends every login at about 08:00, so reconnect before 09:15 for deployments to enter on time.'],
  ['emergency', 'Emergency stop', 'Trading', 'One action that exits every open position in every deployment and pauses them all, for when something is going wrong and you want out now.'],
  ['appearance', 'Appearance', 'You', 'How Linck looks. Saved to your account, so it follows you to every device.'],
  ['notifications', 'Notifications', 'You', 'Push notifications for entries and exits: on which device, for which deployments, and when to stay quiet.'],
  ['security', 'Sign-in & security', 'You', 'Your user, your password and the devices you are signed in on.'],
  ['app', 'Install app', 'You', 'Linck as its own app on your phone or computer: full screen, opens straight to the Desk, and can receive push notifications.'],
  ['users', 'Users', 'Workspace', 'People who can sign in to this Linck.'],
  ['tags', 'Tags', 'Workspace', 'Labels for grouping deployments. Filter by them on Deployments and Compare.'],
  ['history', 'Option price history', 'Workspace', 'Kite stops serving an option’s minute prices once it expires, so Linck saves them every trading day. Cycle charts and roll scoring use them later.'],
  ['log', 'Activity log', 'Workspace', 'Every change made through the app or the API, newest first: who, what and whether it worked.'],
  ['server', 'Server & danger zone', 'Danger zone', 'Restart the server with the latest code, or delete every deployment. Both ask for the app password and a typed phrase.'],
];
const GROUPS = ['Trading', 'You', 'Workspace', 'Danger zone'];
const sectionOf = k => SECTIONS.find(s => s[0] === k) || SECTIONS[0];

// One setting: what it is and what it does on the left, its control on the right.
const row = (label, desc, control = '', cls = '') => html`<div class="set-row ${cls}"><div class="set-l"><b>${label}</b>${desc ? html`<span>${desc}</span>` : ''}</div>${control ? html`<div class="set-c">${control}</div>` : ''}</div>`;
const group = (title, body, note = '') => html`<div class="set-group">${title ? html`<h3>${title}</h3>` : ''}${body}${note ? html`<p class="set-note">${note}</p>` : ''}</div>`;

// What Close everything would do right now.
export function exposure(deps, live = v => book.live(v)) {
  const all = deps.filter(v => v.rawStatus !== 'stopped');
  const holding = all.filter(v => v.legs.length);
  return {
    legs: holding.reduce((a, v) => a + v.legs.length, 0), holding: holding.length,
    running: all.filter(v => v.rawStatus === 'active').length, unreal: holding.reduce((a, v) => a + live(v).unreal, 0),
  };
}

export default {
  title: 'Account',
  render(main, params = {}) {
    const key = sectionOf(params.s)[0];
    let users = null, me = null, push = { state: 'loading' }, mute = null, subs = null, newTag = '', audit = null, auditMore = false;
    let hist = null, histTimer = null;   // option price history (GET /candles/backfill)

    async function loadHist() {
      clearTimeout(histTimer);
      try { hist = await api.backfillStatus(); } catch (e) { hist = { error: e.message }; }
      if (!alive) return;
      if (hist && hist.running) noticeBackfill(hist.running);   // the header strip too, not 20 s later
      paint();
      // every 2 s while a backfill runs; while its section is open every 4 s, so a
      // run started elsewhere (another tab, the 17:00 schedule) shows up even if
      // it's quick; else every 10 s
      histTimer = setTimeout(loadHist, hist && hist.running ? 2000 : key === 'history' ? 4000 : 10000);
    }
    async function loadPush() {
      try {
        const { public_key } = await api.vapidKey();
        if (!public_key) { push = { state: 'unconfigured' }; return; }
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) { push = { state: 'unsupported' }; return; }
        if (Notification.permission === 'denied') { push = { state: 'denied' }; return; }
        const reg = await navigator.serviceWorker.getRegistration();
        const sub = reg ? await reg.pushManager.getSubscription() : null;
        push = { state: sub ? 'on' : 'off', key: public_key };
      } catch (e) { push = { state: 'unsupported' }; }
    }

    const lakh = n => (n >= 1e5 ? `${num(n / 1e5, 1)} lakh` : n.toLocaleString('en-IN'));
    const live = () => kite.state === 'connected';
    const deps = () => (book.isLoaded('deployments') ? book.everything() : []);
    const PUSH = {
      loading: 'Checking…', unconfigured: 'Not set up on this server (run custom_scripts/generate_vapid_keys.py).', unsupported: 'This browser can’t receive push notifications.',
      denied: 'Blocked in your browser’s site settings. Allow notifications there, then reload.', on: 'On: a push for every entry and exit, even with the app closed.', off: 'Off: turn it on to get a push for every entry and exit.',
    };

    // ── each section ────────────────────────────────────────────────
    const S = {
      overview() {
        const x = exposure(deps()), st = installState();
        const pushShort = { on: 'On', off: 'Off', loading: '…' }[push.state] || 'Unavailable';
        const histBad = hist && hist.last && !hist.last.complete;
        // [section, state, tone, short state for the phone's list]
        const cards = [
          ['kite', live() ? `Connected${subs != null ? ` · streaming ${plural(subs, 'instrument')}` : ''}` : kite.state === 'connecting' ? 'Connecting…' : 'Not connected: prices are frozen and nothing can trade', live() ? 'good' : 'warn',
            live() ? 'Connected' : kite.state === 'connecting' ? 'Connecting' : 'Not connected'],
          ['emergency', x.legs ? `${plural(x.legs, 'open leg')} in ${plural(x.holding, 'deployment')} · ${x.running} running` : `No open positions · ${x.running} running`, '',
            x.legs ? plural(x.legs, 'open leg') : 'Nothing open'],
          ['appearance', `${prefs.themeLabel()} theme`, '', prefs.themeLabel()],
          ['notifications', `This device: ${pushShort.toLowerCase()}${mute && mute.muted ? ' · muted for the holiday' : ''}`, push.state === 'on' ? 'good' : '', mute && mute.muted ? 'Muted' : pushShort],
          ['security', me ? `Signed in as ${me.username}` : '…', '', me ? me.username : ''],
          ['app', st === 'installed' ? 'Installed on this device' : 'Not installed on this device', '', st === 'installed' ? 'Installed' : 'Not installed'],
          ['users', users ? plural(users.length, 'user') : '…', '', users ? String(users.length) : ''],
          ['tags', plural(book.tags().length, 'tag'), '', String(book.tags().length)],
          ['history', hist && hist.running ? 'Saving now…' : hist && hist.last ? `Last saved ${dDay(hist.last.finished_at)}, ${hhmm(hist.last.finished_at)}${hist.last.complete ? '' : ' · stopped early'}`
            : hist && !hist.error && hist.next_at ? `Not run yet · next ${dDay(hist.next_at)}, ${hhmm(hist.next_at)}` : hist && hist.error ? 'Couldn’t check' : '…', histBad ? 'warn' : '',
            hist && hist.running ? 'Saving…' : histBad ? 'Stopped early' : hist && hist.last ? dDay(hist.last.finished_at) : hist && hist.next_at ? 'Not run yet' : ''],
          ['log', audit ? (audit.length ? `Last change ${ago(audit[0].occurred_at)} by ${audit[0].username || 'API key'}` : 'Nothing yet') : '…', '', audit && audit.length ? ago(audit[0].occurred_at) : ''],
          ['server', 'Redeploy · delete every deployment', 'danger', ''],
        ];
        // On a phone: who you are, then anything that needs you, then a short list.
        const attn = [
          !live() && kite.state !== 'connecting' && html`<div class="ov-attn"><div class="grow"><b>Kite isn’t connected</b><span>Prices are frozen and nothing can trade.</span></div><button type="button" class="btn btn-sm btn-primary" data-act="kite">Connect</button></div>`,
          histBad && html`<a class="ov-attn" href="${href('account?s=history')}"><div class="grow"><b>Option history stopped early</b><span>It retries every minute. Tap for details.</span></div><i class="ov-chev" aria-hidden="true">›</i></a>`,
        ].filter(Boolean);
        return html`
          <a class="ov-me" href="${href('account?s=security')}"><span class="avatar">${me ? me.username.slice(0, 2) : '··'}</span>
            <span class="col grow" style="gap:0"><b>${me ? me.username : '…'}</b><span class="tiny mute">${me ? `${me.role || 'admin'} · sign-in & security` : ''}</span></span><i class="ov-chev" aria-hidden="true">›</i></a>
          ${attn.length ? html`<div class="ov-attns">${attn}</div>` : ''}
          <div class="ov-grid">${GROUPS.map(gr => html`<section class="ov-grp ${gr === 'Danger zone' ? 'danger' : ''}"><h3>${gr}</h3>
          <div class="ov-list">${cards.filter(([k]) => sectionOf(k)[2] === gr).map(([k, state, tone, short]) => html`<a class="ov-card ${tone}" href="${href(`account?s=${k}`)}" data-sec="${k}">
            <b>${sectionOf(k)[1]}</b><span class="ov-state">${state}</span><span class="ov-what">${sectionOf(k)[3]}</span>
            <span class="ov-short">${short}</span><i class="ov-chev" aria-hidden="true">›</i></a>`)}</div></section>`)}</div>`;
      },

      kite() {
        const on = live();
        return html`
          <div class="kite-card">
            <div class="row kite-card-head ${on ? 'good' : ''}"><span class="dot ${on ? 'live' : 'off'}"></span>
              <h3 class="t-h3 grow">${on ? 'Kite connected' : kite.state === 'connecting' ? 'Connecting…' : 'Kite not connected'}</h3>
              <button type="button" class="btn ${on ? '' : 'btn-primary'}" data-act="kite">${on ? 'Reconnect or paste a token' : 'Connect Kite'}</button></div>
          </div>
          ${group('Status', html`
            ${row('Login', on ? 'Valid until about 08:00 tomorrow, when Kite ends every login.' : 'Expired or not connected. Connect to resume trading.')}
            ${row('Live prices', on ? `Streaming${subs != null ? ` ${plural(subs, 'instrument')}` : ''}.` : 'Not streaming: prices on every page are the last ones received.')}
            ${row('Last price received', kite.lastTickAt ? ago(kite.lastTickAt) : 'None yet this session')}
            ${row('Trading mode', 'Paper trading: orders are simulated against live prices. No real orders reach Zerodha.')}
            ${kite.error ? row('Last error', html`<span class="neg">${kite.error}</span>`) : ''}`,
            html`A market holiday or a day off? Mute the “not connected” alerts under <a href="${href('account?s=notifications')}">Notifications</a>.`)}`;
      },

      emergency() {
        const x = exposure(deps());
        const tile = (l, v, cls = '') => html`<div class="em-tile"><span>${l}</span><b class="mono ${cls}">${v}</b></div>`;
        return html`
          <div class="em-box">
            <div class="em-tiles">${tile('Open legs', x.legs)}${tile('Deployments holding them', x.holding)}${tile('Running deployments', x.running)}${tile('Would book at current prices', inr(x.unreal), x.unreal >= 0 ? 'pos' : 'neg')}</div>
            <div class="em-act">
              <div class="set-l"><b>Close everything</b><span>Places a market exit for every open leg, longs last so no short is left without its hedge, then pauses every running deployment so nothing re-enters. Nothing is deleted. You’ll see the orders and type CLOSE ALL before anything happens.</span></div>
              <button type="button" class="btn btn-stop" data-act="closeAll" ${x.legs || x.running ? '' : 'disabled'}>Close everything…</button>
            </div>
          </div>
          ${group('Good to know', html`
            ${row('Without Kite', 'If Kite isn’t connected, legs close at their last known prices.')}
            ${row('One deployment only?', html`Open it from <a href="${href('deployments')}">Deployments</a> and use its own Close or Pause instead.`)}
            ${row('On a phone', 'Also under More, so it’s two taps away from any page.')}`)}`;
      },

      appearance() {
        return html`
          ${group('Theme', html`<div class="themes">${prefs.THEMES.map(t => html`<button type="button" class="theme-card ${prefs.get('theme') === t.id ? 'on' : ''}" data-act="theme" data-v="${t.id}" aria-pressed="${prefs.get('theme') === t.id}">
            <span class="theme-prev" style="background:${t.swatch.paper};border-color:${t.swatch.line}"><span style="color:${t.swatch.ink}">+₹10,720</span><i style="background:${t.swatch.y}"></i></span>
            <b>${t.label}</b><span class="tiny mute">${t.hint}</span></button>`)}</div>`)}
          ${group('Display', prefs.PREFS.map(p => row(p.label, p.hint, html`<div class="seg" role="group" aria-label="${p.label}">${p.options.map(([v, l, sw]) => html`<button type="button" class="${prefs.get(p.key) === v ? 'on' : ''}" aria-pressed="${prefs.get(p.key) === v}" data-act="pref" data-k="${p.key}" data-v="${v}">
              ${sw ? html`<i class="sw" style="background:${sw}"></i>` : ''}${l}</button>`)}</div>`)))}
          ${group('Interface', row('Classic view', 'The older interface, with the same data. You can come back from its menu.', html`<button type="button" class="btn btn-sm" data-act="classic">Switch to Classic</button>`))}`;
      },

      notifications() {
        const all = deps().filter(v => v.rawStatus !== 'stopped');
        return html`
          ${group('This device', row('Push notifications', PUSH[push.state], ['on', 'off'].includes(push.state) ? toggle(push.state === 'on', 'pushToggle', {}, 'Push on this device') : ''))}
          ${group('Quiet days', row('Market holiday', mute && mute.muted ? `Kite connection alerts are muted until ${dStamp(mute.muted_until)} IST.` : 'Mute the “Kite not connected” alerts until 07:00 tomorrow, for a market holiday or a day off.',
            mute ? toggle(!!mute.muted, 'mute', {}, 'Market holiday') : ''))}
          ${group('By deployment', all.length ? all.map(v => row(v.name, `${v.strategyLabel} · ${v.mode}`, toggle(v.raw.notifications_enabled !== false, 'depNotif', { id: v.id, sm: true }, `Push for ${v.name}`))) : empty('', 'No deployments yet.'),
            'Off for a deployment: no pushes for its entries and exits, on any device. Its alerts still show on the Desk.')}`;
      },

      security() {
        return html`
          ${group('You', html`${row('Username', me ? me.username : '…')}${row('Role', me ? me.role || 'admin' : '…', '', '')}`)}
          ${group('Password', row('Change password', 'Asks for your current password, then the new one (at least 8 characters).', html`<button type="button" class="btn btn-sm" data-act="password">Change password…</button>`))}
          ${group('Sessions', html`
            ${row('This browser', `Signed in as ${me ? me.username : '…'}.`, html`<button type="button" class="btn btn-sm" data-act="logout">Sign out</button>`)}
            ${row('Every device', 'Ends every session for your user, including phones, other browsers and this one.', html`<button type="button" class="btn btn-sm" data-act="logoutAll">Sign out everywhere…</button>`)}`)}`;
      },

      app() {
        const st = installState();
        return group('', row(st === 'installed' ? 'Installed on this device' : 'Install Linck', {
          installed: 'Opens straight to Linck, full screen. It updates itself whenever the server is redeployed.',
          prompt: 'Adds Linck to your home screen or dock as its own app, full screen, with push notifications.',
          ios: 'In Safari, tap Share, then Add to Home Screen. Open Linck from there to get push notifications (iOS 16.4 or later).',
          unavailable: 'Open this page in Chrome on Android or Safari on iPhone to install it. Already installed? Open it from your home screen.',
        }[st], st === 'prompt' ? html`<button type="button" class="btn btn-primary" data-act="install">Install Linck</button>` : ''));
      },

      users() {
        return group('', html`
          ${users === null ? loading(2) : users.map(u => row(html`${u.username}${me && me.username === u.username ? html` <span class="tiny mute">· you</span>` : ''}`,
            `${u.role} · last signed in ${u.last_login_at ? ago(u.last_login_at) : 'never'}`, html`<span class="small ${u.is_active ? '' : 'mute'}">${u.is_active ? 'Active' : 'Disabled'}</span>`))}
          <div class="set-add"><button type="button" class="btn btn-sm" data-act="addUser">Add a user…</button></div>`,
        'Everyone can see everything and place orders: roles aren’t enforced yet.');
      },

      tags() {
        const all = deps();
        const tags = book.tags().map(t => ({ ...t, n: all.filter(v => v.tags.includes(t.name)).length }));
        return group('', html`
          ${tags.length ? tags.map(t => row(t.name, `${plural(t.n, 'deployment')}${t.name === LAB_TAG ? ' · built in' : ''}`, html`
            <button type="button" class="btn btn-xs" data-act="viewTag" data-v="${t.name}">View</button>
            <button type="button" class="btn btn-xs btn-quiet" data-act="delTag" data-id="${t.id}" data-v="${t.name}" ${t.n || t.name === LAB_TAG ? html`aria-disabled="true" data-reason="${t.name === LAB_TAG ? 'Strategy Lab is built in.' : `Remove it from ${plural(t.n, 'deployment')} first.`}"` : ''}>Delete</button>`)) : empty('', 'No tags yet.')}
          <div class="set-add row" style="gap:8px"><input class="input grow" placeholder="New tag name" value="${newTag}" data-newtag aria-label="New tag name"><button type="button" class="btn btn-primary" data-act="addTag">Add tag</button></div>`,
        'Strategy Lab is built in: deployments with it are left out of the Desk and the rankings unless you choose to show them. A tag can be deleted once no deployment uses it.');
      },

      history() {
        if (hist === null) return loading(3);
        if (hist.error) return empty('', hist.error);
        const r = hist.running, l = hist.last, st = hist.stored;
        const pct = r && r.total ? Math.round(r.done / r.total * 100) : 0;
        return html`<div id="history">
          ${r ? html`<div class="bf-run" role="status" aria-live="polite">
              <div class="row" style="gap:10px"><i class="spin" aria-hidden="true"></i><b class="grow">Saving ${r.day === dayKey(new Date()) ? 'today’s' : dDay(`${r.day}T12:00:00+05:30`)} option history</b><span class="mono small">${r.done} of ${r.total || '…'}</span></div>
              <div class="bf-track"><span style="width:${pct}%"></span></div>
              <span class="tiny mute">${r.current ? html`Now: <span class="mono">${r.current}</span> · ` : ''}${plural(r.fetched, 'contract')} fetched from Kite · ${lakh(r.rows)} minutes stored · started ${hhmm(r.started_at)}</span>
            </div>` : ''}
          ${group('Schedule', html`
            ${row('Next run', 'Every trading day at 17:00: every option contract held that day.', html`<span class="mono small">${r ? 'running now' : `${dDay(hist.next_at)}, ${hhmm(hist.next_at)}`}</span>`)}
            ${row('Last run', l ? (l.complete ? `${plural(l.instruments, 'contract')} · ${l.fetched} fetched from Kite · ${lakh(l.rows)} minutes · ${l.seconds}s`
              : `Stopped: ${l.error}. It retries every minute until it completes.`) : '', html`<span class="mono small ${l && !l.complete ? 'neg' : ''}">${l ? `${dDay(l.finished_at)}, ${hhmm(l.finished_at)}` : 'not yet'}</span>`)}
            ${row('Stored', st.minutes ? `${lakh(st.minutes)} one-minute candles${st.since ? ` since ${dDay(st.since)}` : ''}. Index prices come from Kite and aren’t stored.` : 'Nothing yet.', html`<span class="mono small">${plural(st.contracts, 'contract')}</span>`)}`)}
          ${group('Run it now', row('Save missing history', 'Fetches only what isn’t stored yet, one Kite request at a time. Needs Kite connected.',
            html`<button type="button" class="btn btn-sm" data-act="runHist" ${r ? 'disabled' : ''}>${r ? 'Running…' : 'Run now'}</button>`))}
        </div>`;
      },

      log() {
        return audit === null ? loading(4) : audit.length ? html`<div class="audit">${audit.map(a => html`<div class="audit-row">
            <span class="mono tiny mute">${dStamp(a.occurred_at)}</span><b class="small">${a.username || 'API key'}</b>
            <span class="mono tiny ell" title="${a.method} ${a.path}">${a.method} ${a.path}</span>
            <span class="mono tiny right ${a.status_code >= 400 ? 'neg' : 'mute'}" title="${a.status_code >= 400 ? 'Failed' : 'Worked'}">${a.status_code}</span></div>`)}</div>
          ${auditMore ? html`<button type="button" class="btn btn-sm" style="margin-top:10px" data-act="auditMore">Show older</button>` : ''}
          <p class="set-note">The last column is the server’s answer: 2xx worked, 4xx or 5xx (red) didn’t.</p>` : empty('', 'Nothing logged yet.');
      },

      server() {
        return html`
          ${group('Server', row('Update and restart', 'Pulls the latest code, rebuilds and restarts. Linck is unreachable for a minute or two; running deployments pick up where they left off.',
            html`<button type="button" class="btn btn-sm" data-act="redeploy">Redeploy…</button>`))}
          <div class="set-group danger"><h3>Danger zone</h3>
            ${row(html`<span class="neg">Delete every deployment</span>`, 'Stops every runner, then removes all deployments with their trades, events and history. Open positions are removed, not exited: close them first from Emergency stop. Kite, instruments and strategy settings stay. Can’t be undone.',
              html`<button type="button" class="btn btn-sm btn-stop-quiet" data-act="clearAll">Delete all…</button>`)}
          </div>`;
      },
    };

    function nav() {
      return html`<nav class="acct-nav" aria-label="Account sections">
        <a href="${href('account')}" class="${key === 'overview' ? 'on' : ''}" ${key === 'overview' ? html`aria-current="page"` : ''}>Overview</a>
        ${GROUPS.map(gr => html`<h4 class="${gr === 'Danger zone' ? 'neg' : ''}">${gr}</h4>${SECTIONS.filter(s => s[2] === gr).map(([k, t]) => html`
          <a href="${href(`account?s=${k}`)}" class="${k === key ? 'on' : ''}" ${k === key ? html`aria-current="page"` : ''}>${t}${k === 'kite' && !live() ? html`<i class="nav-dot warn" title="Not connected"></i>` : ''}</a>`)}`)}
      </nav>`;
    }

    let alive = true;   // a load finishing after you've left must not paint this section over the next
    function paint() {
      if (!alive) return;
      const [k, title, gr, what] = sectionOf(key);
      mount(main, html`<div class="acct-page ${k === 'overview' ? 'is-ov' : 'is-sec'}">
        <div class="page-head"><h1 class="t-h1">Account</h1><span class="mute pretty" style="max-width:520px">Your Kite login, how Linck looks and talks to you, who can sign in, and the server.</span></div>
        <div class="acct pad-x">
          ${nav()}
          <div class="acct-main" data-sec="${k}">
            ${k !== 'overview' ? html`<a class="acct-back small" href="${href('account')}">← Account</a>` : ''}
            <header class="acct-head">${gr ? html`<span class="acct-grp ${gr === 'Danger zone' ? 'neg' : ''}">${gr}</span>` : ''}<h2>${title}</h2><p>${what}</p></header>
            ${S[k]()}
          </div>
        </div></div>`);
    }

    // Password + typed phrase, both checked by the server (same as classic).
    const guarded = ({ kicker, title, lines, phrase, okLabel, run }) => openDialog({
      kicker, hot: true, title, lines,
      fields: [{ key: 'pw', label: 'App password', type: 'password', mono: false }],
      input: { label: `Type ${phrase} to arm`, placeholder: phrase },
      armed: st => st.text.trim() === phrase && !!st.f.pw,
      okLabel, onOk: st => run(st.f.pw, st.text.trim()),
    });
    const off = actions(main, {
      closeAll: () => closeEverything(),
      runHist: async () => {
        try { hist = await api.runBackfill(); } catch (e) { say(e.message); }
        loadHist();
      },
      auditMore: async () => {
        try { const r = await api.auditLog(audit.length, 50); audit = audit.concat(r); auditMore = r.length === 50; } catch (e) { say(e.message); }
        paint();
      },
      redeploy: () => guarded({
        kicker: 'SERVER', title: 'Update and restart the server?',
        lines: ['Pulls the latest code, rebuilds and restarts. Linck is unreachable for a minute or two, then reloads by itself. Running deployments pick up where they left off.'],
        phrase: 'REDEPLOY', okLabel: 'Redeploy',
        run: async (pw, phrase) => {
          await api.redeploy(pw, phrase);
          say('Redeploy started. Waiting for the new version…');
          setTimeout(function wait() {
            fetch('/health', { credentials: 'same-origin' }).then(r => (r.ok ? window.location.reload() : setTimeout(wait, 3000))).catch(() => setTimeout(wait, 3000));
          }, 6000);
        },
      }),
      clearAll: () => guarded({
        kicker: 'IRREVERSIBLE', title: `Delete all ${plural(book.everything().length, 'deployment')}?`,
        lines: ['Stops every runner, then deletes every deployment with its positions, trades, events and snapshots. Open positions are not closed with exit orders; they are simply removed.', 'Kite, subscribed instruments and strategy settings are kept.'],
        phrase: 'DELETE ALL', okLabel: 'Delete everything',
        run: async (pw, phrase) => { const r = await api.clearAll(pw, phrase); await book.load(); say(`${plural(r.deleted, 'deployment')} deleted.`); },
      }),
      classic: () => toClassic(),
      install: async () => { const ok = await promptInstall(); say(ok ? 'Linck is installed. Open it from your home screen.' : 'Not installed. You can install it any time from here.'); paint(); },
      kite: () => kiteDialog(),
      theme: d => { prefs.set('theme', d.v); paint(); },
      pref: d => { prefs.set(d.k, d.v); paint(); },
      logout: async () => { await api.logout().catch(() => {}); window.location.href = '/linck/'; },
      logoutAll: () => openDialog({
        kicker: 'CONFIRM', title: 'Sign out everywhere?', lines: ['Ends every session for your user, including this one. You’ll need to sign in again on each device.'],
        okLabel: 'Sign out everywhere', onOk: async () => { await api.logoutEverywhere(); window.location.href = '/linck/'; },
      }),
      password: () => {
        let oldPw = '';
        openDialog({
          kicker: 'ACCOUNT', title: 'Change password', lines: ['First your current password, then the new one (at least 8 characters).'],
          input: { label: 'Current password', placeholder: '', type: 'password', mono: false },
          okLabel: 'Next', armed: s => s.text.length > 0,
          onOk: async s => {
            oldPw = s.text;
            openDialog({
              kicker: 'ACCOUNT', title: 'Choose a new password', input: { label: 'New password', placeholder: 'at least 8 characters', type: 'password', mono: false },
              okLabel: 'Change password', armed: s2 => s2.text.length >= 8,
              onOk: async s2 => { await api.changePassword(oldPw, s2.text); say('Password changed.'); },
            });
            return false;
          },
        });
      },
      addUser: () => {
        let name = '';
        openDialog({
          kicker: 'USERS', title: 'Add a user', lines: ['They sign in with this username and a password you give them, then can change it from Account.'],
          input: { label: 'Username', placeholder: 'e.g. riya', mono: false }, okLabel: 'Next', armed: s => s.text.trim().length > 1,
          onOk: async s => {
            name = s.text.trim();
            openDialog({
              kicker: 'USERS', title: `Password for ${name}`, input: { label: 'Temporary password', placeholder: 'at least 8 characters', type: 'password', mono: false },
              okLabel: 'Add user', armed: s2 => s2.text.length >= 8,
              onOk: async s2 => { await api.createUser(name, s2.text); users = await api.users(); paint(); say(`${name} can now sign in.`); },
            });
            return false;
          },
        });
      },
      pushToggle: async () => {
        try {
          const reg = await navigator.serviceWorker.ready;
          const sub = await reg.pushManager.getSubscription();
          if (sub) { await api.pushUnsubscribe({ endpoint: sub.endpoint }); await sub.unsubscribe(); say('Push notifications off on this device.'); }
          else {
            const s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(push.key) });
            await api.pushSubscribe(s.toJSON());
            say('Push notifications on for this device.');
          }
        } catch (e) { say(Notification.permission === 'denied' ? 'Blocked in your browser’s site settings.' : `Couldn’t change notifications: ${e.message}`); }
        await loadPush(); paint();
      },
      mute: async () => {
        try { mute = mute && mute.muted ? await api.unmute() : await api.muteToday(); mute = await api.muteStatus(); } catch (e) { say(e.message); }
        paint();
      },
      depNotif: async d => {
        const v = book.find(d.id);
        try { await api.update(v.id, { notifications_enabled: v.raw.notifications_enabled === false }); await book.load(['deployments']); } catch (e) { say(e.message); }
      },
      viewTag: d => { ui('deployments', {}).set({ tag: d.v, page: 0, ...(d.v === LAB_TAG ? { lab: true } : {}) }); go('deployments'); },
      delTag: async (d, el) => {
        if (el.dataset.reason) return say(el.dataset.reason);
        try { await api.deleteTag(d.id); await book.load(['tags']); say(`Tag “${d.v}” deleted.`); } catch (e) { say(e.message); }
      },
      addTag: async () => {
        const name = ($('[data-newtag]', main).value || '').trim();
        if (!name) return say('Type a tag name first.');
        if (book.tags().some(t => t.name === name)) return say('That tag exists.');
        try { await api.createTag(name); newTag = ''; await book.load(['tags']); say(`Tag “${name}” added.`); } catch (e) { say(e.message); }
      },
    });
    paint();
    // load only what the open section (or the overview) shows
    const need = k => key === 'overview' || key === k;
    Promise.all([
      need('users') || need('security') ? api.users().then(r => { users = r; }).catch(() => { users = []; }) : null,
      need('log') ? api.auditLog(0, 50).then(r => { audit = r; auditMore = r.length === 50; }).catch(() => { audit = []; }) : null,
      api.me().then(r => { me = r; }).catch(() => {}),
      need('notifications') ? loadPush() : null,
      need('notifications') ? api.muteStatus().then(r => { mute = r; }).catch(() => {}) : null,
      need('kite') ? api.instruments().then(r => { subs = (r.subscribed || []).length; }).catch(() => {}) : null,
    ]).then(paint);
    if (need('history')) loadHist();
    // the header's status poll saw a backfill start or finish: refresh now
    const onStatus = () => { if (hist && !hist.error && !!backfill.running !== !!hist.running) loadHist(); else paint(); };
    const offs = [listen('data', paint), listen('status', onStatus), listen('install', paint)];
    return () => { alive = false; off(); offs.forEach(f => f()); clearTimeout(histTimer); };
  },
};
