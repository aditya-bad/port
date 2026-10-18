// Linck sign-in. Self-contained: everything under /linck/js/ is behind
// login, so this file can't import from there.
//
// Steps:  signin → (kite, only when needed) → into the app
//   kite is skipped when Kite is already connected, on weekends, and on
//   days marked as a market holiday (POST /notifications/mute-today,
//   the same switch as classic's "Mark as holiday").

const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const THEMES = [['paper', 'Paper'], ['slate', 'Slate'], ['night', 'Night']];
const st = { step: 'signin', user: '', pw: '', showPw: false, err: '', busy: false, paste: false, override: false, token: '', key: '', secret: '', health: null };

// ── HTTP ───────────────────────────────────────────────────────────
async function call(path, body) {
  const opts = { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', headers: {} };
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  let r;
  try { r = await fetch(path, opts); } catch (e) { throw new Error('Can’t reach the server. It may be restarting; try again in a moment.'); }
  let data = null;
  try { data = await r.json(); } catch (e) { /* empty body */ }
  if (!r.ok) {
    let d = data && data.detail;
    if (Array.isArray(d)) d = d.map(x => x.msg).join('; ');
    const e = new Error(typeof d === 'string' && d ? d : `Request failed (${r.status})`);
    e.status = r.status;
    throw e;
  }
  return data;
}

// ── Market clock (IST, NSE 09:15–15:30; holidays aren't modelled) ────
function market(now = new Date()) {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    .formatToParts(now).map(x => [x.type, x.value]));
  const dw = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(p.weekday);
  const m = +p.hour * 60 + +p.minute, op = 555, cl = 930;
  const hm = x => `${Math.floor(x / 60)}h ${String(x % 60).padStart(2, '0')}m`;
  const wk = dw >= 1 && dw <= 5;
  if (wk && m >= op && m < cl) return { wk, cls: 'open', s: 'NSE · Market open', k: 'CLOSES IN', big: hm(cl - m), sub: 'Your deployments are running. Sign in to see where they stand.' };
  if (wk && m < op) return { wk, cls: 'pre', s: 'NSE · Pre-open', k: 'MARKET OPENS IN', big: hm(op - m), sub: 'Reconnect Kite before 09:15 so deployments enter on time.' };
  return { wk, cls: 'shut', s: 'NSE · Market closed', k: 'NEXT SESSION', big: (dw === 5 && m >= cl) || dw === 6 || dw === 0 ? 'Mon 09:15' : 'Tomorrow 09:15', sub: 'Today’s results are in. Sign in for the day’s statement.' };
}
function paintSide() {
  const k = market();
  $('#side').innerHTML = `<div class="auth-side-in">
    <div class="mk-state"><i class="${k.cls}"></i>${k.s}</div>
    <div class="mk-body"><div class="mk-k">${k.k}</div><div class="mk-big">${k.big}</div><p class="mk-sub">${k.sub}</p></div>
    <div class="mk-foot">Kite logins expire every morning at about 08:00. Linck asks for a fresh one when you sign in on a market day.</div></div>`;
}

// ── Theme ──────────────────────────────────────────────────────────
function prefs() { try { return JSON.parse(localStorage.getItem('linck-prefs') || '{}') || {}; } catch (e) { return {}; } }
function paintTheme() {
  const cur = document.documentElement.getAttribute('data-th');
  $('#theme span').textContent = (THEMES.find(t => t[0] === cur) || THEMES[0])[1];
}
$('#theme').addEventListener('click', () => {
  const cur = document.documentElement.getAttribute('data-th');
  const next = THEMES[(THEMES.findIndex(t => t[0] === cur) + 1) % THEMES.length][0];
  document.documentElement.setAttribute('data-th', next);
  try { localStorage.setItem('linck-prefs', JSON.stringify({ ...prefs(), theme: next })); } catch (e) {}
  paintTheme();
});
$('#classic').addEventListener('click', () => { try { localStorage.setItem('uiExperience', 'classic'); } catch (e) {} });

// ── Steps ──────────────────────────────────────────────────────────
const errBox = () => (st.err ? `<div class="auth-err" role="alert">${esc(st.err)}</div>` : '');
const spin = () => (st.busy ? '<span class="auth-spin" aria-hidden="true"></span>' : '');

const VIEWS = {
  signin: () => `
    <div class="auth-head"><span class="auth-kick">SIGN IN</span><h1 class="auth-title">Welcome back.</h1>
      <div class="mute pretty">Sign in to see your deployments and today’s P&amp;L.</div></div>
    <form class="auth-form" id="f" novalidate>
      <label class="auth-field"><span>Username</span>
        <input class="auth-in" name="user" autocomplete="username" autocapitalize="none" spellcheck="false" value="${esc(st.user)}" required></label>
      <label class="auth-field"><span>Password</span>
        <span class="auth-pw"><input class="auth-in" name="pw" type="${st.showPw ? 'text' : 'password'}" autocomplete="current-password" value="${esc(st.pw)}" required>
          <button type="button" data-a="pw">${st.showPw ? 'Hide' : 'Show'}</button></span></label>
      ${errBox()}
      <button type="submit" class="auth-go" aria-disabled="${st.busy}">${spin()}Sign in</button>
    </form>
    <p class="auth-note">Forgot your password? Ask an admin to reset it. Accounts are added from Account → Users.</p>`,

  kite: () => {
    const h = st.health || {};
    return `
    <div class="auth-head"><span class="auth-kick">STEP 2 OF 2</span><h1 class="auth-title">Connect Kite for today.</h1>
      <div class="mute pretty">Zerodha ends every Kite session at about 08:00. Linck needs a fresh one each morning to read prices and place orders.</div></div>
    <div class="kc">
      <div class="kc-head"><b>Kite isn’t connected</b><span class="mono tiny">${esc(st.user)}</span></div>
      <div class="kc-grid">
        <div><b>Connected</b><span>Live prices. Deployments enter, adjust and exit on schedule.</span></div>
        <div><b>Not connected</b><span>P&amp;L frozen at last known prices. No orders go out.</span></div>
      </div>
      ${h.last_error ? `<div class="kc-err">Last error: ${esc(h.last_error)}</div>` : ''}
    </div>
    <div class="col" style="gap:10px">
      ${st.paste ? `
        <form class="auth-paste" id="f" novalidate>
          <p class="auth-note">Finish Kite’s login in another tab, then paste the <span class="mono">request_token</span> from the address it redirects to. Pasting the whole URL works too.</p>
          <label class="auth-field"><span>request_token or redirect URL</span>
            <input class="auth-in mono" name="token" autocomplete="off" spellcheck="false" value="${esc(st.token)}" placeholder="https://…?request_token=…"></label>
          <button type="button" class="auth-check" role="checkbox" aria-checked="${st.override}" data-a="override"><i></i>Use a different API key and secret, for this login only (never saved)</button>
          ${st.override ? `
            <label class="auth-field"><span>API key</span><input class="auth-in mono" name="key" autocomplete="off" spellcheck="false" value="${esc(st.key)}"></label>
            <label class="auth-field"><span>API secret</span><input class="auth-in mono" name="secret" type="password" autocomplete="off" value="${esc(st.secret)}"></label>` : ''}
          ${errBox()}
          <button type="submit" class="auth-go" aria-disabled="${st.busy}">${spin()}Connect with this token</button>
          <button type="button" class="auth-link" style="align-self:flex-start" data-a="nopaste">Back to Zerodha login</button>
        </form>` : `
        ${errBox()}
        <button type="button" class="auth-go" data-a="kite" aria-disabled="${st.busy}">${spin()}${st.busy ? 'Waiting for Zerodha…' : 'Connect Kite'}</button>
        <button type="button" class="auth-alt" data-a="paste">Paste a request token instead</button>`}
      <div class="auth-or">or</div>
      <button type="button" class="auth-alt" data-a="holiday">Market holiday today: skip, and mute Kite alerts</button>
      <button type="button" class="auth-alt" data-a="skip">Skip, open Linck without Kite</button>
    </div>
    <p class="auth-note">You can connect any time from the Kite button in the header. A holiday mutes Kite reminders until 07:00 tomorrow; undo it from Account.</p>`;
  },
};

function paint(focus) {
  $('#box').innerHTML = VIEWS[st.step]();
  const f = $('#f');
  if (f) {
    f.addEventListener('input', e => { if (e.target.name) st[e.target.name] = e.target.value; });
    f.addEventListener('submit', e => { e.preventDefault(); if (!st.busy) (st.step === 'signin' ? signIn() : pasteToken()); });
  }
  if (focus) {
    const first = [...document.querySelectorAll('#box input')].find(i => !i.value) || $('#box input');
    if (first) first.focus();
  }
}
const set = (patch, focus) => { Object.assign(st, patch); paint(focus); };

$('#box').addEventListener('click', e => {
  const b = e.target.closest('[data-a]');
  if (!b || st.busy) return;
  const a = b.dataset.a;
  if (a === 'pw') return set({ showPw: !st.showPw });
  if (a === 'paste') return set({ paste: true, err: '' }, true);
  if (a === 'nopaste') return set({ paste: false, err: '' });
  if (a === 'override') return set({ override: !st.override }, true);
  if (a === 'kite') return kitePopup();
  if (a === 'holiday') return holiday();
  if (a === 'skip') return enter();
});

// ── Actions ────────────────────────────────────────────────────────
async function signIn() {
  if (!st.user.trim() || !st.pw) return set({ err: 'Enter your username and password.' });
  set({ busy: true, err: '' });
  try {
    await call('/auth/login', { username: st.user.trim(), password: st.pw });
  } catch (e) {
    return set({ busy: false, pw: '', err: e.status === 401 ? 'Wrong username or password.' : e.message }, true);
  }
  st.pw = '';
  // Decide whether today needs a Kite login before going in.
  let h = null, mute = null;
  try { [h, mute] = await Promise.all([call('/health'), call('/notifications/mute-status').catch(() => null)]); } catch (e) { /* go in anyway */ }
  const connected = h && h.kite_connected && !h.needs_login;
  if (!h || connected || (mute && mute.muted) || !market().wk) return enter();
  set({ step: 'kite', busy: false, err: '', health: h });
}

function requestToken(text) {
  const t = (text || '').trim();
  const m = /[?&]request_token=([^&#\s]+)/.exec(t);
  return m ? decodeURIComponent(m[1]) : t;
}
async function pasteToken() {
  const request_token = requestToken(st.token);
  if (!request_token) return set({ err: 'Paste the request_token, or the whole URL Zerodha redirected you to.' }, true);
  const body = { request_token };
  if (st.override) {
    if (!st.key.trim() || !st.secret.trim()) return set({ err: 'Enter both the API key and the API secret, or turn the override off.' }, true);
    body.api_key = st.key.trim(); body.api_secret = st.secret.trim();
  }
  set({ busy: true, err: '' });
  try {
    await call('/kite/manual-login', body);
    enter();
  } catch (e) { set({ busy: false, err: e.message }); }
}

let poll = null;
async function kitePopup() {
  let url;
  try { url = (await call('/kite/login-url')).login_url; } catch (e) { return set({ err: `Couldn’t start the Kite login: ${e.message}` }); }
  const win = window.open(url, 'kite_login', 'width=520,height=720');
  if (!win) return set({ paste: true, err: 'Your browser blocked the Zerodha window. Allow popups for this site, or paste a request token below.' }, true);
  set({ busy: true, err: '' });
  clearInterval(poll);
  poll = setInterval(async () => {
    let h = null;
    try { h = await call('/health'); } catch (e) { /* keep waiting */ }
    if (h && h.kite_connected && !h.needs_login) { clearInterval(poll); try { win.close(); } catch (e) {} enter(); return; }
    if (win.closed) {
      clearInterval(poll);
      set({ busy: false, health: h || st.health, err: 'The Zerodha window closed before Kite connected. Try again, or paste the request_token from its address bar.' });
    }
  }, 1500);
}

async function holiday() {
  set({ busy: true, err: '' });
  try { await call('/notifications/mute-today', {}); enter(); } catch (e) { set({ busy: false, err: e.message }); }
}

function enter() {
  try { localStorage.setItem('uiExperience', 'linck'); } catch (e) {}
  // Same URL means a hash-only change, which wouldn't reload: reload instead.
  if (window.location.pathname === '/linck/') window.location.reload();
  else window.location.replace(`/linck/${window.location.hash || ''}`);
}

// ── Boot ───────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
paintTheme();
paintSide();
setInterval(paintSide, 30000);
paint(true);
