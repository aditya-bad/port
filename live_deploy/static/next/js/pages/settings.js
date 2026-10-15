// Settings — experience, Kite, appearance, P&L basis, alerts, market data,
// account. Rare admin tools stay in the classic experience (linked).
import { html, mount, $, on, ago, debounce } from '../util.js';
import { api, pnlMode, MODE_LABEL } from '../api.js';
import { status, pollHealth, loginWithKite } from '../live.js';
import { toast, toastError, confirm } from '../ui.js';
import { setBasis, setTheme, switchToClassic, signOut } from '../app.js';

const theme = () => { try { return localStorage.getItem('nx.theme') || 'system'; } catch (e) { return 'system'; } };

async function load() {
  const [me, inst, mute] = await Promise.all([
    api.me().catch(() => ({})), api.instruments().catch(() => ({ subscribed: [] })), api.muteStatus().catch(() => null),
  ]);
  return { me, inst, mute };
}

function paint(main, s) {
  const m = pnlMode(), t = theme();
  mount(main, html`
    <div class="page-head"><div><h1>Settings</h1><div class="sub">${s.me.username ? `Signed in as ${s.me.username}` : 'Preferences are saved on this device'}</div></div></div>
    <div class="grid halves" style="align-items:start">
      <div class="stack">
        <section class="card">
          <div class="card-head"><div><h2>Experience</h2><div class="sub">You’re using the new Desk experience</div></div></div>
          <div class="card-body"><p class="sub" style="margin:0 0 12px">The classic experience is still fully available — your strategies, history and preferences are shared between both. You can switch back here any time.</p>
            <button class="btn" data-act="classic">Switch to classic experience</button></div>
        </section>
        <section class="card">
          <div class="card-head"><div><h2>Kite connection</h2><div class="sub">Zerodha’s access token expires every day</div></div>
            <span class="badge ${status.kite ? 'active' : 'stopped'}">${status.kite ? 'Connected' : status.needsLogin ? 'Login needed' : 'Disconnected'}</span></div>
          <div class="card-body">
            <div class="toggle-row"><span class="sub">Last tick</span><b>${status.lastTickAt ? ago(status.lastTickAt) : '—'}</b></div>
            ${status.error ? html`<div class="toggle-row"><span class="sub">Last error</span><span class="small">${status.error}</span></div>` : ''}
            <div class="actions" style="margin-top:10px"><button class="btn primary" data-act="kite">${status.kite ? 'Log in again' : 'Log in to Kite'}</button>
              <button class="btn ghost" data-act="classic-account">Manual token login (classic)</button></div>
          </div>
        </section>
        <section class="card">
          <div class="card-head"><h2>Appearance &amp; numbers</h2></div>
          <div class="card-body">
            <div class="toggle-row"><div><b>Theme</b></div>
              <div class="seg" role="group" aria-label="Theme">${[['system', 'System'], ['light', 'Light'], ['dark', 'Dark']].map(([k, l]) => html`<button data-theme-set="${k}" class="${t === k ? 'on' : ''}">${l}</button>`)}</div></div>
            <div class="toggle-row"><div><b>P&amp;L basis</b><div class="sub small">Applies to every P&amp;L figure, here and in classic.</div></div>
              <select class="input" style="width:auto" data-act="basis">${['gross', 'charges', 'all'].map(k => html`<option value="${k}" ${m === k ? 'selected' : ''}>${MODE_LABEL[k]}</option>`)}</select></div>
          </div>
        </section>
        <section class="card">
          <div class="card-head"><div><h2>Alerts</h2><div class="sub">In-app toasts always show; phone pushes can be muted</div></div></div>
          <div class="card-body">
            <div class="toggle-row"><div><b>Mute phone notifications today</b><div class="sub small">${s.mute && s.mute.muted ? `Muted until ${s.mute.muted_until ? new Date(s.mute.muted_until).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) : 'tomorrow'}` : 'For holidays or days you’re not watching.'}</div></div>
              <label class="switch"><input type="checkbox" data-act="mute" ${s.mute && s.mute.muted ? 'checked' : ''}><i></i></label></div>
            <div class="toggle-row"><div><b>Enable push on this device</b><div class="sub small">Set up in the classic experience’s Account page.</div></div>
              <button class="btn sm" data-act="classic-account">Open</button></div>
          </div>
        </section>
      </div>
      <div class="stack">
        <section class="card">
          <div class="card-head"><div><h2>Market data</h2><div class="sub">Instruments the live feed subscribes to</div></div></div>
          <div class="card-body">
            <div class="rows" style="margin-bottom:12px">${(s.inst.subscribed || []).map(i => html`<div class="row-item" style="padding-left:0;padding-right:0">
              <div><div class="cell-title">${i.symbol || i.instrument_token}</div><div class="cell-sub">Token ${i.instrument_token}${i.static ? ' · always on' : ''}</div></div>
              <div class="right">${i.static ? '' : html`<button class="btn ghost sm" data-unsub="${i.instrument_token}">Remove</button>`}</div></div>`)}</div>
            <label class="field"><span>Add an instrument</span><input class="input" data-act="search" placeholder="Search NSE/BSE symbols, e.g. RELIANCE"></label>
            <div class="rows" id="searchResults"></div>
          </div>
        </section>
        <section class="card">
          <div class="card-head"><h2>Account</h2></div>
          <div class="card-body stack">
            <div class="form-grid">
              <label class="field"><span>Current password</span><input class="input" type="password" id="oldPw" autocomplete="current-password"></label>
              <label class="field"><span>New password</span><input class="input" type="password" id="newPw" autocomplete="new-password"></label>
            </div>
            <div class="actions"><button class="btn" data-act="password">Change password</button></div>
            <div class="actions" style="border-top:1px solid var(--line);padding-top:14px">
              <button class="btn" data-act="signout">Sign out</button>
              <button class="btn danger" data-act="signout-all">Sign out everywhere</button></div>
          </div>
        </section>
        <section class="card">
          <div class="card-head"><div><h2>Admin &amp; maintenance</h2><div class="sub">Users, audit log, enabling strategies, redeploy and clear-all live in classic</div></div></div>
          <div class="card-body"><button class="btn" data-act="classic-account">Open admin in classic</button></div>
        </section>
      </div>
    </div>`);
}

export default {
  title: 'Settings',
  async render(main) {
    let s = await load();
    await pollHealth();
    paint(main, s);
    const repaint = async () => { s = await load(); paint(main, s); };
    const search = debounce(async q => {
      const box = $('#searchResults', main);
      if (!box) return;
      if (q.trim().length < 2) { box.innerHTML = ''; return; }
      try {
        const res = await api.searchInstruments(q.trim());
        const list = (Array.isArray(res) ? res : res.results || []).slice(0, 8);
        mount(box, html`${list.map(r => html`<div class="row-item" style="padding-left:0;padding-right:0"><div><div class="cell-title">${r.tradingsymbol || r.symbol}</div><div class="cell-sub">${r.exchange || ''} · ${r.name || ''} · ${r.instrument_token}</div></div>
          <div class="right"><button class="btn sm" data-sub="${r.instrument_token}" data-sym="${r.tradingsymbol || r.symbol || ''}">Subscribe</button></div></div>`)}
          ${list.length ? '' : html`<div class="empty">No matches</div>`}`);
      } catch (e) { mount(box, html`<div class="empty">${e.message}</div>`); }
    }, 300);
    const offs = [
      on(main, 'click', '[data-act="classic"]', () => switchToClassic()),
      on(main, 'click', '[data-act="classic-account"]', () => switchToClassic('#/account')),
      on(main, 'click', '[data-act="kite"]', () => loginWithKite().catch(e => toastError(e, 'Kite login failed'))),
      on(main, 'click', '[data-theme-set]', (e, b) => { setTheme(b.dataset.themeSet); repaint(); }),
      on(main, 'change', '[data-act="basis"]', (e, sel) => setBasis(sel.value)),
      on(main, 'change', '[data-act="mute"]', async (e, inp) => {
        try { if (inp.checked) await api.muteToday(); else await api.unmute(); toast(inp.checked ? 'Phone notifications muted for today' : 'Phone notifications on'); repaint(); }
        catch (err) { inp.checked = !inp.checked; toastError(err, 'Not changed'); }
      }),
      on(main, 'input', '[data-act="search"]', (e, i) => search(i.value)),
      on(main, 'click', '[data-sub]', async (e, b) => {
        try { await api.subscribe({ instrument_token: Number(b.dataset.sub), symbol: b.dataset.sym || null }); toast(`Subscribed to ${b.dataset.sym || b.dataset.sub}`); repaint(); }
        catch (err) { toastError(err, 'Not subscribed'); }
      }),
      on(main, 'click', '[data-unsub]', async (e, b) => {
        try { await api.unsubscribe(b.dataset.unsub); toast('Removed'); repaint(); } catch (err) { toastError(err, 'Not removed'); }
      }),
      on(main, 'click', '[data-act="password"]', async () => {
        const oldPw = $('#oldPw', main).value, newPw = $('#newPw', main).value;
        if (!oldPw || !newPw) { toastError(new Error('Fill in both password fields'), 'Not changed'); return; }
        try { await api.changePassword(oldPw, newPw); toast('Password changed'); $('#oldPw', main).value = ''; $('#newPw', main).value = ''; }
        catch (e) { toastError(e, 'Password not changed'); }
      }),
      on(main, 'click', '[data-act="signout"]', signOut),
      on(main, 'click', '[data-act="signout-all"]', async () => {
        if (!await confirm({ title: 'Sign out everywhere?', description: 'Every signed-in browser and phone, including this one, will need to log in again.', confirmLabel: 'Sign out everywhere', danger: true })) return;
        try { await api.logoutEverywhere(); } catch (e) { /* signed out anyway */ }
        location.href = '/';
      }),
    ];
    return () => offs.forEach(f => f());
  },
};
