// Account — Kite, users, sessions, appearance (rendered from the
// preference registry, so new themes/preferences appear here on their
// own), push notifications and tags.

import { api } from '../core/api.js';
import { html, mount, $, actions } from '../core/dom.js';
import { ago, plural } from '../core/format.js';
import * as prefs from '../core/prefs.js';
import { ui, listen } from '../core/store.js';
import { go } from '../core/router.js';
import * as book from '../data/book.js';
import { kite } from '../data/live.js';
import { LAB_TAG } from '../data/config.js';
import { toggle, empty, loading } from '../ui/components.js';
import { openDialog } from '../ui/dialog.js';
import { say } from '../ui/toast.js';
import { kiteDialog } from '../actions.js';
import { toClassic } from '../app.js';

const b64 = s => { const p = '='.repeat((4 - s.length % 4) % 4); const r = atob((s + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from([...r].map(c => c.charCodeAt(0))); };

export default {
  title: 'Account',
  render(main) {
    let users = null, me = null, push = { state: 'loading' }, mute = null, subs = null, newTag = '';

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
              <div class="sect ruled"><h3 class="t-h3">Push notifications</h3></div>
              <div class="pref-row"><div class="col grow" style="gap:0"><b>This device</b><span class="tiny mute">${{
                loading: 'Checking…', unconfigured: 'Not configured on this server (custom_scripts/generate_vapid_keys.py).', unsupported: 'This browser can’t receive push notifications.',
                denied: 'Blocked in your browser’s site settings. Allow notifications there, then reload.', on: 'On: a push for every entry and exit, even with the app closed.', off: 'Off: turn on to get a push for every entry and exit.',
              }[push.state]}</span></div>${['on', 'off'].includes(push.state) ? toggle(push.state === 'on', 'pushToggle', {}, 'Push on this device') : ''}</div>
              <div class="pref-row"><div class="col grow" style="gap:0"><b>Mute for today</b><span class="tiny mute">${mute && mute.muted ? 'Muted until the end of today.' : 'Silence every push until tomorrow, for a holiday or a day off'}</span></div>
                ${mute ? toggle(!!mute.muted, 'mute', {}, 'Mute for today') : ''}</div>
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

    const off = actions(main, {
      classic: () => toClassic(),
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
      api.me().then(r => { me = r; }).catch(() => {}),
      loadPush(),
      api.muteStatus().then(r => { mute = r; }).catch(() => {}),
      api.instruments().then(r => { subs = (r.subscribed || []).length; }).catch(() => {}),
    ]).then(paint);
    const offs = [listen('data', paint), listen('status', paint)];
    return () => { off(); offs.forEach(f => f()); };
  },
};
