// Account — Kite, users, sessions, appearance (rendered from the
// preference registry, so new themes/preferences appear here on their
// own), push notifications and tags.

import { api } from '../core/api.js';
import { html, mount, $, actions } from '../core/dom.js';
import { ago, plural, dStamp, dDay, dayKey, hhmm, num } from '../core/format.js';
import * as prefs from '../core/prefs.js';
import { ui, listen } from '../core/store.js';
import { go } from '../core/router.js';
import * as book from '../data/book.js';
import { kite, backfill, noticeBackfill } from '../data/live.js';
import { LAB_TAG } from '../data/config.js';
import { toggle, empty, loading } from '../ui/components.js';
import { openDialog } from '../ui/dialog.js';
import { say } from '../ui/toast.js';
import { kiteDialog } from '../actions.js';
import { installState, promptInstall } from '../data/install.js';
import { toClassic } from '../app.js';

const b64 = s => { const p = '='.repeat((4 - s.length % 4) % 4); const r = atob((s + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from([...r].map(c => c.charCodeAt(0))); };

export default {
  title: 'Account',
  render(main) {
    let users = null, me = null, push = { state: 'loading' }, mute = null, subs = null, newTag = '', audit = null, auditMore = false;
    let hist = null, histTimer = null;   // option price history (GET /candles/backfill)

    async function loadHist() {
      clearTimeout(histTimer);
      try { hist = await api.backfillStatus(); } catch (e) { hist = { error: e.message }; }
      if (hist && hist.running) noticeBackfill(hist.running);   // the header strip too, not 20 s later
      paint();
      // every 2 s while a backfill runs, else every 10 s (a 15:45 run shows up on its own)
      histTimer = setTimeout(loadHist, hist && hist.running ? 2000 : 10000);
    }
    function histBlock() {
      if (hist === null) return loading(3);
      if (hist.error) return empty('', hist.error);
      const r = hist.running, l = hist.last, st = hist.stored;
      const lakh = n => (n >= 1e5 ? `${num(n / 1e5, 1)} lakh` : n.toLocaleString('en-IN'));
      const row = (k, v, sub, cls = '') => html`<div class="pref-row"><div class="col grow" style="gap:0"><b>${k}</b>${sub ? html`<span class="tiny mute">${sub}</span>` : ''}</div><span class="mono small right ${cls}">${v}</span></div>`;
      const pct = r && r.total ? Math.round(r.done / r.total * 100) : 0;
      return html`
        ${r ? html`<div class="bf-run" role="status" aria-live="polite">
            <div class="row" style="gap:10px"><i class="spin" aria-hidden="true"></i><b class="grow">Saving ${r.day === dayKey(new Date()) ? 'today’s' : dDay(`${r.day}T12:00:00+05:30`)} option history</b><span class="mono small">${r.done} of ${r.total || '…'}</span></div>
            <div class="bf-track"><span style="width:${pct}%"></span></div>
            <span class="tiny mute">${r.current ? html`Now: <span class="mono">${r.current}</span> · ` : ''}${plural(r.fetched, 'contract')} fetched from Kite · ${lakh(r.rows)} minutes stored · started ${hhmm(r.started_at)}</span>
          </div>` : ''}
        ${row('Next run', r ? 'running now' : `${dDay(hist.next_at)}, ${hhmm(hist.next_at)}`, 'Every trading day at 15:45: every option contract held that day, before Kite drops it at expiry')}
        ${l ? row('Last run', `${dDay(l.finished_at)}, ${hhmm(l.finished_at)}`,
            l.complete ? `${plural(l.instruments, 'contract')} · ${l.fetched} fetched from Kite · ${lakh(l.rows)} minutes · ${l.seconds}s`
              : `Stopped: ${l.error}. It retries every minute until it completes.`, l.complete ? '' : 'neg') : row('Last run', 'not yet', '')}
        ${row('Stored', `${plural(st.contracts, 'contract')}`, st.minutes ? `${lakh(st.minutes)} one-minute candles${st.since ? ` since ${dDay(st.since)}` : ''} · indices come from Kite, not stored` : 'Nothing yet')}
        <div style="padding-top:12px"><button type="button" class="btn btn-sm" data-act="runHist" ${r ? 'disabled' : ''}>${r ? 'Running…' : 'Run now'}</button>
          <span class="tiny mute" style="padding-left:10px">Fetches only what isn’t stored. One Kite request at a time.</span></div>`;
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

    function paint() {
      const all = book.isLoaded('deployments') ? book.deployments() : [];
      const live = kite.state === 'connected';
      const tagRows = book.tags().map(t => ({ ...t, n: all.filter(v => v.tags.includes(t.name)).length }));
      mount(main, html`
        <div class="page-head"><h1 class="t-h1">Account</h1><span class="grow"></span>
          <button type="button" class="btn btn-quiet" data-act="classic">Classic view</button></div>
        <div class="pad-x grid-two" style="gap:48px;align-items:start;padding-bottom:var(--px)">
          <div class="stack" style="gap:36px">
            <div class="kite-card">
              <div class="row kite-card-head ${live ? 'good' : ''}"><span class="dot ${live ? 'live' : 'off'}"></span>
                <h3 class="t-h3 grow">${live ? 'Kite connected' : kite.state === 'connecting' ? 'Connecting…' : 'Kite session expired'}</h3>
                <button type="button" class="btn ${live ? '' : 'btn-primary'}" data-act="kite">${live ? 'Reconnect or paste token' : 'Connect Kite'}</button></div>
              <div class="kv-grid" style="grid-template-columns:140px 1fr;padding:16px 20px;gap:8px 12px">
                <span class="mute">Session</span><b>${live ? 'Valid until about 08:00 tomorrow' : 'Expired. Log in again to resume trading.'}</b>
                <span class="mute">Live data</span><b>${live ? `Streaming${subs != null ? `, ${plural(subs, 'instrument')}` : ''}` : 'Not streaming. Prices are frozen.'}</b>
                <span class="mute">Last tick</span><b>${kite.lastTickAt ? ago(kite.lastTickAt) : '—'}</b>
                <span class="mute">Mode</span><b>Paper trading. Orders are simulated against live prices.</b>
                ${kite.error ? html`<span class="mute">Last error</span><b class="warn" style="font-weight:600">${kite.error}</b>` : ''}
              </div>
              <div class="small mute" style="padding:12px 20px;border-top:1px solid var(--line)">Kite logins expire every morning at about 08:00. Reconnect before 09:15 so deployments can enter on time.</div>
            </div>
            <div>
              <div class="sect ruled"><h3 class="t-h3 grow">Users</h3><button type="button" class="btn btn-sm" data-act="addUser">Add user</button></div>
              ${users === null ? loading(2) : users.map(u => html`<div class="pref-row"><div class="col grow" style="gap:0"><b>${u.username}${me && me.username === u.username ? html` <span class="tiny mute">· you</span>` : ''}</b>
                <span class="tiny mute">${u.role} · last signed in ${u.last_login_at ? ago(u.last_login_at) : 'never'}</span></div>
                <span class="small ${u.is_active ? '' : 'mute'}">${u.is_active ? 'Active' : 'Disabled'}</span></div>`)}
              <p class="tiny mute" style="margin:8px 0 0">Every user can see everything and place orders today; roles aren’t enforced yet.</p>
            </div>
            <div>
              <div class="sect ruled"><h3 class="t-h3">Sessions</h3></div>
              <div class="pref-row"><div class="col grow" style="gap:0"><b>This browser</b><span class="tiny mute">signed in as ${me ? me.username : '…'}</span></div><button type="button" class="btn btn-sm" data-act="logout">Sign out</button></div>
              <div class="pref-row"><div class="col grow" style="gap:0"><b>Every device</b><span class="tiny mute">Ends all sessions, including phones and other browsers</span></div><button type="button" class="btn btn-sm" data-act="logoutAll">Sign out everywhere…</button></div>
              <div class="pref-row"><div class="col grow" style="gap:0"><b>Password</b><span class="tiny mute">At least 8 characters</span></div><button type="button" class="btn btn-sm" data-act="password">Change password…</button></div>
            </div>
            <div>
              <div class="sect ruled"><h3 class="t-h3 grow">Activity log</h3><span class="tiny mute">every change made through the app or API</span></div>
              ${audit === null ? loading(4) : audit.length ? html`<div class="audit">${audit.map(a => html`<div class="audit-row">
                <span class="mono tiny mute">${dStamp(a.occurred_at)}</span><b class="small">${a.username || 'API key'}</b>
                <span class="mono tiny ell" title="${a.method} ${a.path}">${a.method} ${a.path}</span>
                <span class="mono tiny right ${a.status_code >= 400 ? 'neg' : 'mute'}">${a.status_code}</span></div>`)}</div>
                ${auditMore ? html`<button type="button" class="btn btn-sm" style="margin-top:10px" data-act="auditMore">Show older</button>` : ''}` : empty('', 'Nothing logged yet.')}
            </div>
            <div id="history">
              <div class="sect ruled"><h3 class="t-h3 grow">Option price history</h3><span class="tiny mute">for cycle charts after expiry</span></div>
              ${histBlock()}
            </div>
            <div>
              <div class="sect ruled"><h3 class="t-h3">Server</h3></div>
              <div class="pref-row"><div class="col grow" style="gap:0"><b>Update and restart</b><span class="tiny mute">Pulls the latest code, rebuilds and restarts. Deployments resume from the database.</span></div><button type="button" class="btn btn-sm" data-act="redeploy">Redeploy…</button></div>
              <div class="pref-row"><div class="col grow" style="gap:0"><b class="neg">Delete every deployment</b><span class="tiny mute">Removes all deployments with their trades, events and history. Kite, instruments and strategy settings stay. Can’t be undone.</span></div><button type="button" class="btn btn-sm" data-act="clearAll">Delete all…</button></div>
            </div>
          </div>
          <div class="stack" style="gap:36px">
            <div>
              <div class="sect ruled"><h3 class="t-h3">Appearance</h3></div>
              <div class="themes">${prefs.THEMES.map(t => html`<button type="button" class="theme-card ${prefs.get('theme') === t.id ? 'on' : ''}" data-act="theme" data-v="${t.id}" aria-pressed="${prefs.get('theme') === t.id}">
                <span class="theme-prev" style="background:${t.swatch.paper};border-color:${t.swatch.line}"><span style="color:${t.swatch.ink}">+₹10,720</span><i style="background:${t.swatch.y}"></i></span>
                <b>${t.label}</b><span class="tiny mute">${t.hint}</span></button>`)}</div>
              <div style="padding-top:18px">${prefs.PREFS.map(p => html`<div class="pref-row"><div class="col grow" style="gap:0;min-width:200px"><b>${p.label}</b><span class="tiny mute">${p.hint}</span></div>
                <div class="seg" role="group" aria-label="${p.label}">${p.options.map(([v, l, sw]) => html`<button type="button" class="${prefs.get(p.key) === v ? 'on' : ''}" aria-pressed="${prefs.get(p.key) === v}" data-act="pref" data-k="${p.key}" data-v="${v}">
                  ${sw ? html`<i class="sw" style="background:${sw}"></i>` : ''}${l}</button>`)}</div></div>`)}</div>
              <p class="tiny mute" style="margin:8px 0 0">Saved to your account, so they follow you to every device.</p>
            </div>
            <div>
              <div class="sect ruled"><h3 class="t-h3">App</h3></div>
              ${(() => { const st = installState(); return html`<div class="pref-row" style="align-items:flex-start;flex-wrap:wrap">
                <div class="col grow" style="gap:2px;min-width:220px"><b>${st === 'installed' ? 'Installed on this device' : 'Install Linck'}</b>
                  <span class="tiny mute pretty">${{
                    installed: 'Opens straight to Linck, full screen. It updates itself whenever the server is redeployed.',
                    prompt: 'Adds Linck to your home screen or dock as its own app, full screen, with push notifications.',
                    ios: 'In Safari, tap Share, then Add to Home Screen. Open Linck from there to get push notifications (iOS 16.4 or later).',
                    unavailable: 'Open this page in Chrome on Android or Safari on iPhone to install it. Already installed? Open it from your home screen.',
                  }[st]}</span></div>
                ${st === 'prompt' ? html`<button type="button" class="btn btn-primary" data-act="install">Install Linck</button>` : ''}
              </div>`; })()}
            </div>
            <div>
              <div class="sect ruled"><h3 class="t-h3">Push notifications</h3></div>
              <div class="pref-row"><div class="col grow" style="gap:0"><b>This device</b><span class="tiny mute">${{
                loading: 'Checking…', unconfigured: 'Not configured on this server (custom_scripts/generate_vapid_keys.py).', unsupported: 'This browser can’t receive push notifications.',
                denied: 'Blocked in your browser’s site settings. Allow notifications there, then reload.', on: 'On: a push for every entry and exit, even with the app closed.', off: 'Off: turn on to get a push for every entry and exit.',
              }[push.state]}</span></div>${['on', 'off'].includes(push.state) ? toggle(push.state === 'on', 'pushToggle', {}, 'Push on this device') : ''}</div>
              <div class="pref-row"><div class="col grow" style="gap:0"><b>Market holiday</b><span class="tiny mute">${mute && mute.muted ? `Kite connection alerts are muted until ${dStamp(mute.muted_until)} IST.` : 'Mute Kite connection alerts until 07:00 tomorrow, for a market holiday or a day off'}</span></div>
                ${mute ? toggle(!!mute.muted, 'mute', {}, 'Market holiday') : ''}</div>
              ${all.length ? html`<p class="small b" style="margin:16px 0 4px">By deployment</p>${all.filter(v => v.rawStatus !== 'stopped').map(v => html`<div class="pref-row" style="min-height:44px">
                <span class="grow small">${v.name}</span>${toggle(v.raw.notifications_enabled !== false, 'depNotif', { id: v.id, sm: true }, `Push for ${v.name}`)}</div>`)}` : ''}
            </div>
            <div>
              <div class="sect ruled"><h3 class="t-h3">Tags</h3></div>
              ${tagRows.length ? tagRows.map(t => html`<div class="pref-row" style="min-height:46px"><b class="grow">${t.name}</b><span class="small mute">${plural(t.n, 'deployment')}</span>
                <button type="button" class="small b" data-act="viewTag" data-v="${t.name}">View</button>
                <button type="button" class="small mute" data-act="delTag" data-id="${t.id}" data-v="${t.name}" ${t.n || t.name === LAB_TAG ? html`aria-disabled="true" data-reason="${t.name === LAB_TAG ? 'Strategy Lab is built in.' : `Remove it from ${plural(t.n, 'deployment')} first.`}" style="opacity:.4"` : ''}>Delete</button></div>`) : empty('', 'No tags yet.')}
              <div class="row" style="gap:8px;padding-top:12px"><input class="input grow" placeholder="New tag name" value="${newTag}" data-newtag aria-label="New tag name"><button type="button" class="btn btn-primary" data-act="addTag">Add tag</button></div>
              <p class="tiny mute" style="margin:8px 0 0">Strategy Lab is a built-in tag: deployments with it are hidden from the Desk and rankings unless you choose to show them.</p>
            </div>
          </div>
        </div>`);
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
        kicker: 'IRREVERSIBLE', title: `Delete all ${plural(book.deployments().length, 'deployment')}?`,
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
    Promise.all([
      api.users().then(r => { users = r; }).catch(() => { users = []; }),
      api.auditLog(0, 50).then(r => { audit = r; auditMore = r.length === 50; }).catch(() => { audit = []; }),
      api.me().then(r => { me = r; }).catch(() => {}),
      loadPush(),
      api.muteStatus().then(r => { mute = r; }).catch(() => {}),
      api.instruments().then(r => { subs = (r.subscribed || []).length; }).catch(() => {}),
    ]).then(paint);
    loadHist();
    // the header's status poll saw a backfill start or finish: refresh now
    const onStatus = () => { if (hist && !hist.error && !!backfill.running !== !!hist.running) loadHist(); else paint(); };
    const offs = [listen('data', paint), listen('status', onStatus), listen('install', paint)];
    return () => { off(); offs.forEach(f => f()); clearTimeout(histTimer); };
  },
};
