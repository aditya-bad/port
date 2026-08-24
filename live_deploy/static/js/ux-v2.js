/*
 * live_deploy UX v2
 *
 * This file is intentionally a progressive-enhancement/refactor layer on
 * the existing application. It DOES NOT replace the current API, router,
 * strategy runtime, SSE streams, or data model. Instead it patches the
 * already-loaded vanilla-JS modules and reuses their public helpers.
 *
 * Load order: after account.js and before the inline app-shell script in
 * static/index.html. apply_ux_v2.py adds that one script tag.
 */
(function () {
  'use strict';

  const UXV2 = window.UXV2 = {
    version: '2.0.0',
    initialized: false,
    activeSummaryCache: new Map(),
    selectedDeployments: new Set(),
    dashboardLiveHandler: null,
    detailLiveHandler: null,
    detailHistoryMode: 'positions',
    detailHistoryRange: null,
    historyTrades: [],
    historyEvents: [],
    comparePeriod: 'common',
    notifications: [],
    notificationUnread: 0,
    tableObserver: null,
    _openPopover: null,
  };

  // ------------------------------------------------------------------
  // Bootstrap CSS immediately; no need to edit the large inline style
  // block in index.html.
  // ------------------------------------------------------------------
  if (!document.querySelector('link[data-ux-v2]')) {
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/js/ux-v2.css';
    css.dataset.uxV2 = '1';
    document.head.appendChild(css);
  }

  function safeJsonParse(value, fallback) {
    try { return JSON.parse(value); } catch (_) { return fallback; }
  }

  function debounce(fn, ms) {
    let timer = null;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  function istDateKey(value) {
    const d = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(d.getTime())) return '';
    // en-CA gives YYYY-MM-DD in modern browsers.
    return d.toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
  }

  function nowIstDateKey() { return istDateKey(new Date()); }

  function istMonthKey(value) { return istDateKey(value).slice(0, 7); }

  function startOfMonthIso(year, monthIndex) {
    // An ISO string at midnight IST. Date.parse understands +05:30.
    return `${year}-${String(monthIndex + 1).padStart(2, '0')}-01T00:00:00+05:30`;
  }

  function endOfMonthIso(year, monthIndex) {
    const nextYear = monthIndex === 11 ? year + 1 : year;
    const nextMonth = monthIndex === 11 ? 0 : monthIndex + 1;
    const next = new Date(`${nextYear}-${String(nextMonth + 1).padStart(2, '0')}-01T00:00:00+05:30`);
    return new Date(next.getTime() - 1).toISOString();
  }

  function dateRangeContains(iso, range) {
    if (!range) return true;
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return false;
    return t >= new Date(range.start).getTime() && t <= new Date(range.end).getTime();
  }

  function humanAgo(iso) {
    if (!iso) return '—';
    const delta = Math.max(0, Date.now() - new Date(iso).getTime());
    if (!Number.isFinite(delta)) return '—';
    if (delta < 60_000) return `${Math.max(1, Math.floor(delta / 1000))}s ago`;
    if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`;
    if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)}h ago`;
    return `${Math.floor(delta / 86_400_000)}d ago`;
  }

  function asMoney(n) {
    return typeof fmtSignedMoney === 'function' ? fmtSignedMoney(n) : `${Number(n || 0).toFixed(2)}`;
  }

  function pnlClassSafe(n) {
    return typeof pnlClass === 'function' ? pnlClass(n) : (Number(n) >= 0 ? 'pos' : 'neg');
  }

  // One batched server-side read for settled active-period semantics.
  // Added by app/routers/ux_summary.py in this package. The per-deployment
  // path below remains as a compatibility fallback if only the frontend
  // overlay is applied against an older server.
  if (typeof Api !== 'undefined' && !Api.getActivePeriods) {
    Api.getActivePeriods = async function getActivePeriods() {
      const r = await fetch('/portfolio/active-periods');
      if (!r.ok) throw new Error(`Could not load active-period summaries (${r.status})`);
      return r.json();
    };
  }

  // ------------------------------------------------------------------
  // Active-period P&L semantics
  // Intraday -> current IST trading day.
  // Positional -> currently open episode/cycle, including realized P&L
  // from legs already adjusted/rolled/closed inside that still-open unit.
  // ------------------------------------------------------------------
  UXV2.getActiveSummary = async function getActiveSummary(dep, force = false) {
    const cached = this.activeSummaryCache.get(dep.id);
    if (!force && cached && Date.now() - cached.ts < 10_000) return cached.value;

    const blank = {
      deployment_id: dep.id,
      mode: dep.mode,
      period_kind: dep.mode === 'positional' ? 'positional_cycle' : 'intraday_day',
      period_label: dep.mode === 'positional' ? 'Current cycle' : 'Today',
      active: dep.mode !== 'positional',
      started_at: null,
      realized_pnl: 0,
      today_realized_pnl: 0,
      unrealized_pnl: 0,
      total_pnl: 0,
      return_pct: dep.initial_capital ? 0 : null,
      open_positions: 0,
      last_action_at: null,
      last_action: null,
      last_cycle_pnl: null,
      last_cycle_opened_at: null,
      last_cycle_closed_at: null,
    };

    try {
      const requests = [
        Api.getPositions(dep.id, dep.mode === 'positional' ? 'all' : 'open'),
        Api.getPnlDigestForDeployment(dep.id, 'day', 4),
        Api.getTrades(dep.id, 1),
      ];
      const [positionsRaw, dayDigest, tradesPage] = await Promise.all(requests);
      const positions = Array.isArray(positionsRaw) ? positionsRaw : [];
      const openPositions = positions.filter(p => p.status === 'open');
      const today = dayDigest.find(r => istDateKey(r.period_start) === nowIstDateKey());
      blank.today_realized_pnl = today ? Number(today.realized_pnl || 0) : 0;
      blank.unrealized_pnl = openPositions.reduce((s, p) => s + Number(p.unrealized_pnl || 0), 0);
      blank.open_positions = openPositions.length;
      const latestLot = tradesPage && tradesPage.lots && tradesPage.lots[0];
      if (latestLot) {
        blank.last_action_at = latestLot.executed_at;
        blank.last_action = `${latestLot.action || ''} ${latestLot.symbol || ''}`.trim();
      }

      if (dep.mode !== 'positional') {
        blank.active = true;
        blank.realized_pnl = blank.today_realized_pnl;
        blank.total_pnl = blank.realized_pnl + blank.unrealized_pnl;
        blank.started_at = `${nowIstDateKey()}T00:00:00+05:30`;
      } else {
        let units = [];
        try {
          units = typeof groupPositionsIntoUnits === 'function'
            ? groupPositionsIntoUnits(positions, 'position')
            : [];
        } catch (_) { units = []; }
        const openUnits = units.filter(u => u.status === 'open');
        const closedUnits = units.filter(u => u.status === 'closed')
          .slice().sort((a, b) => new Date(a.closed_at || a.opened_at) - new Date(b.closed_at || b.opened_at));

        if (openUnits.length) {
          blank.active = true;
          blank.period_label = openUnits.length > 1 ? `${openUnits.length} active cycles` : 'Current cycle';
          blank.realized_pnl = openUnits.reduce((s, u) => s + Number(u.realized_pnl || 0), 0);
          blank.started_at = openUnits.map(u => u.opened_at).filter(Boolean)
            .sort((a, b) => new Date(a) - new Date(b))[0] || null;
          blank.total_pnl = blank.realized_pnl + blank.unrealized_pnl;
        } else {
          blank.active = false;
          blank.realized_pnl = 0;
          blank.unrealized_pnl = 0;
          blank.total_pnl = 0;
          const last = closedUnits[closedUnits.length - 1];
          if (last) {
            blank.last_cycle_pnl = Number(last.realized_pnl || 0);
            blank.last_cycle_opened_at = last.opened_at || null;
            blank.last_cycle_closed_at = last.closed_at || null;
          }
        }
      }
      blank.return_pct = dep.initial_capital ? (blank.total_pnl / dep.initial_capital) * 100 : null;
    } catch (e) {
      console.warn('UXV2 active summary failed for', dep.deployment_name, e);
      // Fallback to data already present in DeploymentOut so the UI never
      // disappears just because one supplemental endpoint failed.
      blank.unrealized_pnl = Number(dep.unrealized_pnl || 0);
      blank.total_pnl = dep.mode === 'positional'
        ? blank.unrealized_pnl
        : Number(dep.unrealized_pnl || 0);
    }

    this.activeSummaryCache.set(dep.id, { ts: Date.now(), value: blank });
    return blank;
  };

  UXV2.enrichDeployments = async function enrichDeployments(deployments, openPositions = null) {
    // Preferred path: one active-period request + one existing aggregate
    // open-positions request, independent of deployment count. This keeps
    // Dashboard/Deployments from turning into an N+1 client as the roster
    // grows. If the backend addition has not been applied yet, fall back to
    // the original per-deployment derivation so the overlay remains safe.
    try {
      const [serverRows, positions] = await Promise.all([
        Api.getActivePeriods(),
        openPositions ? Promise.resolve(openPositions) : Api.getAllPositions('open'),
      ]);
      const byServer = new Map((serverRows || []).map(r => [String(r.deployment_id), r]));
      const openByDep = new Map();
      (positions || []).forEach(p => {
        const key = String(p.deployment_id);
        const current = openByDep.get(key) || { pnl: 0, count: 0 };
        current.pnl += Number(p.unrealized_pnl || 0);
        current.count += 1;
        openByDep.set(key, current);
      });
      deployments.forEach(dep => {
        const key = String(dep.id);
        const row = byServer.get(key);
        if (!row) return;
        const open = openByDep.get(key) || { pnl: 0, count: 0 };
        const summary = {
          ...row,
          deployment_id: dep.id,
          unrealized_pnl: open.pnl,
          open_positions: open.count,
          total_pnl: Number(row.realized_pnl || 0) + open.pnl,
        };
        summary.return_pct = dep.initial_capital ? (summary.total_pnl / dep.initial_capital) * 100 : null;
        dep._uxActive = summary;
        this.activeSummaryCache.set(dep.id, { ts: Date.now(), value: summary });
      });
      // A just-created row can theoretically race the aggregate query;
      // fill only those rare misses through the safe individual fallback.
      const missing = deployments.filter(d => !d._uxActive);
      if (missing.length) {
        const fallbacks = await Promise.all(missing.map(d => this.getActiveSummary(d)));
        fallbacks.forEach((summary, i) => { missing[i]._uxActive = summary; });
      }
      return deployments;
    } catch (e) {
      console.warn('UXV2 batched active-period endpoint unavailable; using compatibility fallback.', e);
      const results = await Promise.all(deployments.map(d => this.getActiveSummary(d)));
      results.forEach((summary, i) => { deployments[i]._uxActive = summary; });
      return deployments;
    }
  };

  // ------------------------------------------------------------------
  // Common drawer / dialog surfaces
  // ------------------------------------------------------------------
  UXV2.ensureSurfaces = function ensureSurfaces() {
    if (!document.getElementById('uxDrawer')) {
      const backdrop = document.createElement('div');
      backdrop.id = 'uxDrawerBackdrop';
      backdrop.className = 'ux-drawer-backdrop';
      backdrop.onclick = () => this.closeDrawer();
      document.body.appendChild(backdrop);

      const drawer = document.createElement('aside');
      drawer.id = 'uxDrawer';
      drawer.className = 'ux-drawer';
      drawer.setAttribute('role', 'dialog');
      drawer.setAttribute('aria-modal', 'true');
      drawer.innerHTML = `
        <div class="ux-drawer-head">
          <div><h2 id="uxDrawerTitle">Details</h2><div class="card-sub" id="uxDrawerSub"></div></div>
          <button class="btn btn-secondary btn-sm" onclick="UXV2.closeDrawer()" aria-label="Close">✕</button>
        </div>
        <div class="ux-drawer-body" id="uxDrawerBody"></div>`;
      document.body.appendChild(drawer);
    }

    if (!document.getElementById('uxDialog')) {
      const backdrop = document.createElement('div');
      backdrop.id = 'uxDialogBackdrop';
      backdrop.className = 'ux-dialog-backdrop';
      backdrop.onclick = () => this.closeDialog();
      document.body.appendChild(backdrop);

      const dialog = document.createElement('div');
      dialog.id = 'uxDialog';
      dialog.className = 'ux-dialog';
      dialog.setAttribute('role', 'dialog');
      dialog.setAttribute('aria-modal', 'true');
      document.body.appendChild(dialog);
    }
  };

  UXV2.openDrawer = function openDrawer(title, html, sub = '') {
    this.ensureSurfaces();
    document.getElementById('uxDrawerTitle').textContent = title;
    document.getElementById('uxDrawerSub').textContent = sub || '';
    document.getElementById('uxDrawerBody').innerHTML = html;
    document.getElementById('uxDrawerBackdrop').classList.add('open');
    document.getElementById('uxDrawer').classList.add('open');
    document.body.style.overflow = 'hidden';
  };

  UXV2.closeDrawer = function closeDrawer() {
    document.getElementById('uxDrawerBackdrop')?.classList.remove('open');
    document.getElementById('uxDrawer')?.classList.remove('open');
    if (!document.querySelector('.modal-overlay.open') && !document.getElementById('uxDialog')?.classList.contains('open')) {
      document.body.style.overflow = '';
    }
  };

  UXV2.openDialog = function openDialog(html) {
    this.ensureSurfaces();
    document.getElementById('uxDialog').innerHTML = html;
    document.getElementById('uxDialogBackdrop').classList.add('open');
    document.getElementById('uxDialog').classList.add('open');
    document.body.style.overflow = 'hidden';
  };

  UXV2.closeDialog = function closeDialog() {
    document.getElementById('uxDialogBackdrop')?.classList.remove('open');
    document.getElementById('uxDialog')?.classList.remove('open');
    if (!document.querySelector('.modal-overlay.open') && !document.getElementById('uxDrawer')?.classList.contains('open')) {
      document.body.style.overflow = '';
    }
  };

  UXV2.openStopDialog = async function openStopDialog(id, name) {
    let dep = null;
    let positions = [];
    try {
      [dep, positions] = await Promise.all([Api.getDeployment(id), Api.getPositions(id, 'open')]);
    } catch (_) { /* fallback copy below */ }
    const label = name || dep?.deployment_name || 'this deployment';
    const n = positions.length;
    this.openDialog(`
      <h2>Stop ${escapeHtml(label)}?</h2>
      <div class="card-sub">
        ${n ? `This deployment currently has <b>${n} open position${n === 1 ? '' : 's'}</b>. Choose exactly what should happen to them.`
          : 'This deployment has no open positions.'}
      </div>
      ${n ? `<div class="ux-dialog-options">
        <label class="ux-dialog-option">
          <input type="radio" name="uxStopMode" value="leave" checked>
          <span><b>Stop strategy and leave positions open</b><br><span class="card-sub">No more strategy decisions will run. Existing positions remain untouched.</span></span>
        </label>
        <label class="ux-dialog-option">
          <input type="radio" name="uxStopMode" value="close">
          <span><b>Square off positions, then stop</b><br><span class="card-sub">Close every currently open position first, then stop the deployment.</span></span>
        </label>
      </div>` : ''}
      <div class="btn-row" style="display:flex; justify-content:flex-end; gap:8px; margin-top:18px;">
        <button class="btn btn-secondary" onclick="UXV2.closeDialog()">Cancel</button>
        <button class="btn btn-danger" id="uxStopConfirmBtn" onclick="UXV2.confirmStop('${id}', ${n ? 'true' : 'false'})">Stop deployment</button>
      </div>
      <div class="modal-msg" id="uxStopMsg"></div>`);
  };

  UXV2.confirmStop = async function confirmStop(id, hasPositions) {
    const msg = document.getElementById('uxStopMsg');
    const close = hasPositions && document.querySelector('input[name="uxStopMode"]:checked')?.value === 'close';
    msg.innerHTML = '<span class="spinner"></span> Stopping…';
    const r = await Api.stopDeployment(id, !!close);
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      msg.innerHTML = `<span style="color:var(--loss)">${escapeHtml(data.detail || 'Could not stop deployment')}</span>`;
      return;
    }
    this.activeSummaryCache.delete(id);
    this.closeDialog();
    if (window.location.hash.includes(`/deployments/${id}`)) await Detail.load(id);
    else if (typeof Deployments !== 'undefined') await Deployments.load(true);
  };

  // ------------------------------------------------------------------
  // Navigation/top bar
  // ------------------------------------------------------------------
  UXV2.groupNavigation = function groupNavigation() {
    const group = document.getElementById('sidebarNavGroup');
    if (!group || group.dataset.uxGrouped) return;
    group.dataset.uxGrouped = '1';

    const items = Object.fromEntries([...group.querySelectorAll('.nav-item')].map(a => [a.dataset.nav, a]));
    const sections = [
      ['LIVE', ['dashboard']],
      ['STRATEGIES', ['deployments', 'catalog']],
      ['ANALYZE', ['portfolio', 'compare', 'reports']],
      ['DATA', ['instruments']],
      ['SYSTEM', ['account']],
    ];
    const labels = {
      dashboard: 'Overview', deployments: 'Deployed', catalog: 'Catalog', portfolio: 'Portfolio',
      compare: 'Compare', reports: 'Reports', instruments: 'Instruments', account: 'Settings',
    };

    const footer = group.querySelector('.sidebar-footer');
    sections.forEach(([section, names]) => {
      const label = document.createElement('div');
      label.className = 'ux-nav-section-label';
      label.textContent = section;
      group.insertBefore(label, footer);
      names.forEach(name => {
        const item = items[name];
        if (!item) return;
        item.textContent = labels[name] || item.textContent;
        if (names.length > 1) item.classList.add('ux-nav-child');
        group.insertBefore(item, footer);
      });
    });

    const accountTitle = document.querySelector('#view-account .view-header h1');
    if (accountTitle) accountTitle.textContent = 'Settings';
  };

  UXV2.ensureTopbar = function ensureTopbar() {
    const main = document.querySelector('main.main');
    if (!main || document.getElementById('uxTopbar')) return;
    const bar = document.createElement('div');
    bar.id = 'uxTopbar';
    bar.className = 'ux-topbar';
    bar.innerHTML = `
      <div class="ux-topbar-left">
        <span class="ux-topbar-title">Trading control</span>
        <button class="ux-status-btn" id="uxKiteStatus" onclick="UXV2.openKitePopover(event)">
          <span class="ux-status-dot" id="uxKiteDot"></span><span id="uxKiteText">Checking Kite…</span>
        </button>
      </div>
      <div class="ux-topbar-right">
        <button class="ux-icon-btn" title="Notifications" onclick="UXV2.toggleNotificationPanel(event)">🔔<span class="ux-unread-badge" id="uxUnreadBadge" style="display:none;">0</span></button>
        <button class="ux-icon-btn" title="Toggle theme" onclick="toggleTheme()">◐</button>
      </div>`;
    const ticker = document.getElementById('tickerBar');
    main.insertBefore(bar, ticker || main.firstChild);

    const status = document.getElementById('statusBar');
    if (status) {
      const sync = () => this.syncKiteStatus();
      new MutationObserver(sync).observe(status, { childList: true, subtree: true, characterData: true });
      sync();
    }
  };

  UXV2.syncKiteStatus = function syncKiteStatus() {
    const source = document.getElementById('statusBar');
    const text = document.getElementById('uxKiteText');
    const dot = document.getElementById('uxKiteDot');
    if (!source || !text || !dot) return;
    const raw = source.textContent.replace(/\s+/g, ' ').trim();
    const connected = /kite connected/i.test(raw);
    const bad = /not connected|disconnected|unreachable|login required/i.test(raw);
    text.textContent = connected ? 'Kite connected' : bad ? 'Kite disconnected' : 'Kite checking…';
    dot.className = `ux-status-dot ${connected ? 'ok' : bad ? 'bad' : ''}`;
  };

  UXV2.openKitePopover = function openKitePopover(event) {
    event.stopPropagation();
    const statusText = document.getElementById('statusBar')?.textContent.replace(/\s+/g, ' ').trim() || 'Status unavailable';
    this.openPopover(event.currentTarget, `
      <div style="padding:7px 9px 9px;">
        <div style="font-size:9px;color:var(--parchment);text-transform:uppercase;font-weight:800;letter-spacing:.06em;">Kite connection</div>
        <div style="font-size:11px;font-weight:700;margin-top:5px;">${escapeHtml(statusText)}</div>
      </div>
      <div class="ux-menu-sep"></div>
      <button class="ux-menu-item" onclick="UXV2.closePopover(); loginWithKite()">Re-login with Kite</button>
      <button class="ux-menu-item" onclick="UXV2.closePopover(); openManualLoginModal()">Enter token manually</button>`);
  };

  UXV2.openPopover = function openPopover(anchor, html) {
    this.closePopover();
    const pop = document.createElement('div');
    pop.className = 'ux-popover';
    pop.innerHTML = html;
    document.body.appendChild(pop);
    const r = anchor.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 24);
    pop.style.width = `${width}px`;
    pop.style.left = `${Math.max(12, Math.min(window.innerWidth - width - 12, r.left))}px`;
    pop.style.top = `${Math.min(window.innerHeight - pop.offsetHeight - 12, r.bottom + 6)}px`;
    this._openPopover = pop;
  };

  UXV2.closePopover = function closePopover() {
    if (this._openPopover) this._openPopover.remove();
    this._openPopover = null;
  };

  // ------------------------------------------------------------------
  // Notification centre - uses the EXISTING event stream by wrapping
  // showToast after the inline app-shell script has defined it.
  // ------------------------------------------------------------------
  UXV2.loadNotifications = function loadNotifications() {
    const saved = safeJsonParse(sessionStorage.getItem('uxV2Notifications') || '[]', []);
    this.notifications = Array.isArray(saved) ? saved.slice(0, 50) : [];
    this.notificationUnread = Number(sessionStorage.getItem('uxV2NotificationUnread') || 0);
    this.renderUnreadBadge();
  };

  UXV2.saveNotifications = function saveNotifications() {
    sessionStorage.setItem('uxV2Notifications', JSON.stringify(this.notifications.slice(0, 50)));
    sessionStorage.setItem('uxV2NotificationUnread', String(this.notificationUnread));
  };

  UXV2.captureNotification = function captureNotification(ev) {
    if (!ev || !ev.event_type) return;
    this.notifications.unshift({
      event_type: ev.event_type,
      deployment_id: ev.deployment_id || null,
      deployment_name: ev.deployment_name || '',
      message: ev.message || '',
      at: new Date().toISOString(),
    });
    this.notifications = this.notifications.slice(0, 50);
    this.notificationUnread += 1;
    this.saveNotifications();
    this.renderUnreadBadge();
  };

  UXV2.renderUnreadBadge = function renderUnreadBadge() {
    const badge = document.getElementById('uxUnreadBadge');
    if (!badge) return;
    badge.textContent = this.notificationUnread > 99 ? '99+' : String(this.notificationUnread);
    badge.style.display = this.notificationUnread > 0 ? '' : 'none';
  };

  UXV2.toggleNotificationPanel = function toggleNotificationPanel(event) {
    event.stopPropagation();
    let panel = document.getElementById('uxNotificationPanel');
    if (!panel) {
      panel = document.createElement('div');
      panel.id = 'uxNotificationPanel';
      panel.className = 'ux-notification-panel';
      document.body.appendChild(panel);
    }
    const opening = !panel.classList.contains('open');
    panel.classList.toggle('open', opening);
    if (opening) {
      this.notificationUnread = 0;
      this.saveNotifications();
      this.renderUnreadBadge();
      this.renderNotificationPanel();
    }
  };

  UXV2.renderNotificationPanel = function renderNotificationPanel() {
    const panel = document.getElementById('uxNotificationPanel');
    if (!panel) return;
    panel.innerHTML = `
      <div class="ux-notification-head"><b>Notifications</b><button class="btn btn-secondary btn-sm" onclick="UXV2.clearNotifications()">Clear</button></div>
      ${this.notifications.length ? this.notifications.map((n, i) => `
        <div class="ux-notification-item" onclick="UXV2.openNotification(${i})">
          <div class="ux-notification-title">${escapeHtml(n.deployment_name || 'live_deploy')} · ${escapeHtml(n.event_type.replace(/_/g, ' '))}</div>
          <div class="ux-notification-msg">${escapeHtml(n.message || '')}</div>
          <div class="ux-notification-time">${humanAgo(n.at)}</div>
        </div>`).join('') : '<div class="empty" style="padding:24px;">No notifications yet.</div>'}`;
  };

  UXV2.openNotification = function openNotification(i) {
    const n = this.notifications[i];
    if (n?.deployment_id) window.location.hash = `#/deployments/${n.deployment_id}/overview`;
    document.getElementById('uxNotificationPanel')?.classList.remove('open');
  };

  UXV2.clearNotifications = function clearNotifications() {
    this.notifications = [];
    this.notificationUnread = 0;
    this.saveNotifications();
    this.renderUnreadBadge();
    this.renderNotificationPanel();
  };

  UXV2.installToastCapture = function installToastCapture() {
    if (window.__uxV2ToastPatched || typeof window.showToast !== 'function') return;
    const base = window.showToast;
    window.showToast = function (ev) {
      UXV2.captureNotification(ev);
      return base(ev);
    };
    window.__uxV2ToastPatched = true;
  };

  // ------------------------------------------------------------------
  // Dashboard fixed operational zone
  // ------------------------------------------------------------------
  UXV2.ensureDashboardZone = function ensureDashboardZone() {
    const view = document.getElementById('view-dashboard');
    const sections = document.getElementById('dashboardSections');
    if (!view || !sections) return null;
    let zone = document.getElementById('uxOperationalZone');
    if (!zone) {
      zone = document.createElement('div');
      zone.id = 'uxOperationalZone';
      zone.className = 'ux-operational-zone';
      sections.parentNode.insertBefore(zone, sections);
    }
    return zone;
  };

  UXV2.renderDashboardOperational = async function renderDashboardOperational() {
    const zone = this.ensureDashboardZone();
    if (!zone) return;
    zone.innerHTML = '<div class="empty"><span class="spinner"></span> Building live operational view…</div>';

    window.LivePnl?.untrack(this.dashboardLiveHandler);
    this.dashboardLiveHandler = null;

    try {
      const [deployments, openPositions, recentTrades] = await Promise.all([
        Api.listDeployments(),
        Api.getAllPositions('open'),
        Api.getRecentTrades(20),
      ]);
      await this.enrichDeployments(deployments, openPositions);
      this.dashboardDeployments = deployments;
      const byId = new Map(deployments.map(d => [d.id, d]));
      const live = deployments.filter(d => d.status !== 'stopped');
      const activeSummaries = live.map(d => d._uxActive).filter(Boolean);

      const activePnl = activeSummaries.reduce((s, a) => s + Number(a.total_pnl || 0), 0);
      const todayRealized = deployments.reduce((s, d) => s + Number(d._uxActive?.today_realized_pnl || 0), 0);
      // How many deployments actually CONTRIBUTED to that total -- not
      // simply every deployment that exists (deployments.length), which
      // read as "N deployments realized this today" when most of them
      // may have closed nothing at all today. A closed position with
      // realized_pnl netting to exactly 0 is rare enough, and harmless
      // enough here, not to need its own separate "count of fills"
      // plumbing just to distinguish from "no activity."
      const todayRealizedDepCount = deployments.filter(d => Number(d._uxActive?.today_realized_pnl || 0) !== 0).length;
      const openUnrealized = openPositions.reduce((s, p) => s + Number(p.unrealized_pnl || 0), 0);
      const totalCapital = live.reduce((s, d) => s + Number(d.initial_capital || 0), 0);
      // "Capital at work" = the value actually tied up in currently
      // OPEN positions -- exactly what deployments.js's own "Open Cost"
      // column already shows per deployment (Math.abs since a bought/
      // long leg's own open_cost_basis is a DEBIT, i.e. negative, while
      // a sold/short leg's is a credit/positive -- "at work" cares
      // about the MAGNITUDE of exposure, not which side of the trade
      // it's on).
      //
      // NOT `initial_capital - current_cash`: current_cash is its own
      // running ledger, current_cash = initial_capital + realized_pnl +
      // open_cost_basis (see deployments.js's own "Open Cost" header
      // tooltip) -- so initial_capital - current_cash algebraically
      // reduces to `-(realized_pnl + open_cost_basis)`, a mixed
      // quantity that has nothing to do with "how much capital is
      // currently deployed." That formula was the actual bug behind
      // this card showing a number that didn't match Open Cost's own
      // total on the Deployed Strategies table for the exact same
      // deployments.
      const capitalAtWork = live.reduce((s, d) => s + Math.abs(Number(d.open_cost_basis || 0)), 0);
      const capitalPct = totalCapital ? (capitalAtWork / totalCapital) * 100 : 0;
      const activeIntraday = activeSummaries.filter(a => a.mode !== 'positional').reduce((s, a) => s + Number(a.total_pnl || 0), 0);
      const activeCycles = activeSummaries.filter(a => a.mode === 'positional' && a.active).reduce((s, a) => s + Number(a.total_pnl || 0), 0);

      const positionsByDep = {};
      openPositions.forEach(p => { (positionsByDep[p.deployment_id] ||= []).push(p); });
      const attention = [];
      deployments.forEach(d => {
        const count = (positionsByDep[d.id] || []).length;
        if (count && d.status === 'paused') attention.push({ cls: '', d, text: `Paused with ${count} open position${count === 1 ? '' : 's'}`, detail: 'The strategy is not making new decisions while market risk remains open.' });
        if (count && d.status === 'stopped') attention.push({ cls: 'bad', d, text: `Stopped with ${count} open position${count === 1 ? '' : 's'}`, detail: 'Review immediately: the deployment is stopped but exposure remains.' });
      });
      // Strategy errors already arrive through the existing /sse/events
      // stream. Surface the latest per deployment as persistent dashboard
      // attention instead of relying on an 8-second toast alone.
      const seenErrorDeps = new Set();
      (this.notifications || []).filter(n => n.event_type === 'strategy_error').forEach(n => {
        if (!n.deployment_id || seenErrorDeps.has(n.deployment_id)) return;
        seenErrorDeps.add(n.deployment_id);
        const dep = deployments.find(d => String(d.id) === String(n.deployment_id));
        attention.unshift({ cls: 'bad', d: dep || null, text: 'Strategy error', detail: n.message || `Recorded ${humanAgo(n.at)}` });
      });
      const statusRaw = document.getElementById('statusBar')?.textContent || '';
      if (/not connected|disconnected|unreachable|login required/i.test(statusRaw)) {
        attention.unshift({ cls: 'bad', d: null, text: 'Kite is disconnected', detail: 'Live prices and strategy execution may be affected.' });
      }

      zone.innerHTML = `
        <div class="ux-zone-heading">
          <div><h2>Right now</h2><div class="ux-live-caption">Operational truth — includes every live deployment, even if excluded from analytics.</div></div>
          <span class="updated-label">Live prices update from the existing SSE stream</span>
        </div>
        <div class="ux-kpi-grid">
          <div class="ux-kpi ux-kpi-clickable" onclick="UXV2.openActivePnlBreakdown()">
            <div class="ux-kpi-label">Active P&amp;L ⓘ</div>
            <div class="ux-kpi-value ${pnlClassSafe(activePnl)}" id="uxActivePnlValue">${asMoney(activePnl)}</div>
            <div class="ux-kpi-sub">
              <div class="ux-kpi-sub-row"><span>Intraday · Today</span><b class="${pnlClassSafe(activeIntraday)}" id="uxIntradayPnl">${asMoney(activeIntraday)}</b></div>
              <div class="ux-kpi-sub-row"><span>Positional · active cycles</span><b class="${pnlClassSafe(activeCycles)}" id="uxCyclesPnl">${asMoney(activeCycles)}</b></div>
            </div>
          </div>
          <div class="ux-kpi">
            <div class="ux-kpi-label">Today realized</div>
            <div class="ux-kpi-value ${pnlClassSafe(todayRealized)}">${asMoney(todayRealized)}</div>
            <div class="ux-kpi-sub"><div class="ux-kpi-sub-row"><span>Deployments with activity today</span><b>${todayRealizedDepCount}</b></div></div>
          </div>
          <div class="ux-kpi">
            <div class="ux-kpi-label">Open risk</div>
            <div class="ux-kpi-value ${pnlClassSafe(openUnrealized)}" id="uxOpenRiskPnl">${asMoney(openUnrealized)}</div>
            <div class="ux-kpi-sub">
              <div class="ux-kpi-sub-row"><span>Open positions</span><b>${openPositions.length}</b></div>
              <div class="ux-kpi-sub-row"><span>Deployments exposed</span><b>${Object.keys(positionsByDep).length}</b></div>
            </div>
          </div>
          <div class="ux-kpi">
            <div class="ux-kpi-label">Capital at work</div>
            <div class="ux-kpi-value">${typeof fmtMoney === 'function' ? fmtMoney(capitalAtWork) : capitalAtWork.toFixed(0)}</div>
            <div class="ux-kpi-sub">
              <div class="ux-kpi-sub-row"><span>Total live capital</span><b>${typeof fmtMoney === 'function' ? fmtMoney(totalCapital) : totalCapital.toFixed(0)}</b></div>
              <div class="ux-kpi-sub-row"><span>Utilization</span><b>${capitalPct.toFixed(1)}%</b></div>
            </div>
          </div>
        </div>
        <div class="ux-attention">
          <div class="ux-attention-head"><span>Needs attention${attention.length ? ` · ${attention.length}` : ''}</span>${attention.length ? '' : '<span class="ux-attention-empty">✓ All running deployments look operationally healthy</span>'}</div>
          ${attention.map(a => `
            <div class="ux-attention-item ${a.cls}">
              <span class="ux-attention-dot"></span>
              <div><b>${a.d ? escapeHtml(a.d.deployment_name) : 'Connection'}</b> — ${escapeHtml(a.text)}<div class="ux-attention-detail">${escapeHtml(a.detail)}</div></div>
              ${a.d ? `<a href="#/deployments/${a.d.id}/overview">View</a>` : `<button class="btn btn-secondary btn-sm" onclick="loginWithKite()">Reconnect</button>`}
            </div>`).join('')}
        </div>
        <div class="ux-operational-positions">
          <div class="ux-card-head"><strong>Open positions · all live exposure</strong><span class="card-sub">Analytics exclusions never hide risk here.</span></div>
          ${this.operationalPositionsTable(openPositions, byId)}
        </div>`;

      // Recent Activity is operational, like open risk. Do not let a
      // performance-analytics opt-out hide live executions from it.
      if (typeof Dashboard?.renderActivity === 'function') Dashboard.renderActivity(recentTrades || []);
      document.getElementById('dashSectionPositions')?.classList.add('ux-v2-replaced');
      this.applyDashboardDefaults();
      this.setupDashboardCustomize();

      if (window.LivePnl && openPositions.length) {
        const realizedByDep = new Map(activeSummaries.map(s => [s.deployment_id, Number(s.realized_pnl || 0)]));
        const activePositional = new Set(activeSummaries.filter(s => s.mode === 'positional' && s.active).map(s => s.deployment_id));
        const intradayIds = new Set(activeSummaries.filter(s => s.mode !== 'positional').map(s => s.deployment_id));
        this.dashboardLiveHandler = window.LivePnl.track(openPositions, ({ pnlFor, priceFor, totalPnl }) => {
          let activeLive = 0;
          let intradayLive = 0;
          let cycleLive = 0;
          deployments.forEach(d => {
            const open = totalPnl(d.id);
            const realized = realizedByDep.get(d.id) || 0;
            if (intradayIds.has(d.id)) {
              const v = realized + (open == null ? Number(d._uxActive?.unrealized_pnl || 0) : open);
              activeLive += v; intradayLive += v;
            } else if (activePositional.has(d.id)) {
              const v = realized + (open == null ? Number(d._uxActive?.unrealized_pnl || 0) : open);
              activeLive += v; cycleLive += v;
            }
          });
          const totalOpen = totalPnl();
          this.setLiveMoney('uxActivePnlValue', activeLive);
          this.setLiveMoney('uxIntradayPnl', intradayLive);
          this.setLiveMoney('uxCyclesPnl', cycleLive);
          if (totalOpen != null) this.setLiveMoney('uxOpenRiskPnl', totalOpen);
          openPositions.forEach(p => {
            const row = zone.querySelector(`tr[data-ux-position-id="${p.id}"]`);
            if (!row) return;
            const px = priceFor(p.instrument_token);
            const pp = pnlFor(p.id);
            if (px != null) row.querySelector('.ux-live-price').textContent = fmtNum(px);
            if (pp != null) {
              const cell = row.querySelector('.ux-live-pnl');
              cell.textContent = asMoney(pp);
              cell.className = `ux-live-pnl ${pnlClassSafe(pp)}`;
            }
          });
        });
      }
      this.enhanceTablesSoon();
    } catch (e) {
      console.error('UXV2 dashboard operational render failed', e);
      zone.innerHTML = `<div class="empty">Could not build the live operational summary — ${escapeHtml(e.message || String(e))}</div>`;
    }
  };

  UXV2.setLiveMoney = function setLiveMoney(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = asMoney(value);
    const preserve = [...el.classList].filter(c => !['pos', 'neg'].includes(c));
    el.className = `${preserve.join(' ')} ${pnlClassSafe(value)}`.trim();
  };

  UXV2.operationalPositionsTable = function operationalPositionsTable(positions, byId) {
    if (!positions.length) return '<div class="empty" style="padding:18px;">No open positions across any deployment.</div>';
    return `<div class="table-wrap"><table><thead><tr>
      <th>Symbol</th><th>Deployment</th><th>Strategy</th><th>Side</th><th>Qty</th><th>Avg</th><th>Price</th><th>Unrealized</th>
    </tr></thead><tbody>${positions.map(p => {
      const d = byId.get(p.deployment_id);
      return `<tr data-ux-position-id="${p.id}">
        <td>${escapeHtml(p.symbol)}</td>
        <td><a href="#/deployments/${p.deployment_id}/overview">${escapeHtml(p.deployment_name || d?.deployment_name || '')}</a>${d && !d.include_in_reports ? ' <span class="tag tag-warn" title="Still shown here because live risk is operational truth.">analytics excluded</span>' : ''}</td>
        <td>${escapeHtml(p.strategy_name || d?.strategy_name || '')}</td>
        <td>${escapeHtml(p.side)}</td><td>${fmtNum(p.qty)}</td><td>${fmtNum(p.avg_entry_price)}</td>
        <td class="ux-live-price">${p.current_price != null ? fmtNum(p.current_price) : '—'}</td>
        <td class="ux-live-pnl ${pnlClassSafe(p.unrealized_pnl)}">${p.unrealized_pnl != null ? asMoney(p.unrealized_pnl) : '—'}</td>
      </tr>`;
    }).join('')}</tbody></table></div>`;
  };

  UXV2.openActivePnlBreakdown = function openActivePnlBreakdown() {
    const source = (typeof Deployments !== 'undefined' && Deployments._all?.length) ? Deployments._all : (this.dashboardDeployments || []);
    if (!source.length) { window.location.hash = '#/deployments'; return; }
    const rows = source.filter(d => d.status !== 'stopped').map(d => ({ d, s: d._uxActive })).filter(x => x.s);
    this.openDrawer('Active P&L', `
      <div class="table-note" style="margin-bottom:10px;">Intraday deployments reset at the IST trading date. Positional deployments use the currently-open strategic cycle and reset only after the whole cycle is flat.</div>
      <div class="table-wrap"><table><thead><tr><th>Deployment</th><th>Period</th><th>Realized</th><th>Open</th><th>Total</th></tr></thead><tbody>
      ${rows.map(({ d, s }) => `<tr class="ux-row-navigate" onclick="location.hash='#/deployments/${d.id}/overview'; UXV2.closeDrawer();">
        <td>${escapeHtml(d.deployment_name)}</td><td>${escapeHtml(s.period_label)}</td>
        <td class="${pnlClassSafe(s.realized_pnl)}">${asMoney(s.realized_pnl)}</td>
        <td class="${pnlClassSafe(s.unrealized_pnl)}">${asMoney(s.unrealized_pnl)}</td>
        <td class="${pnlClassSafe(s.total_pnl)}"><b>${asMoney(s.total_pnl)}</b></td>
      </tr>`).join('')}</tbody></table></div>`);
    this.enhanceTablesSoon();
  };

  // ------------------------------------------------------------------
  // Widget drag/drop and Dashboard customize mode
  // ------------------------------------------------------------------
  UXV2.layoutKey = function layoutKey(name) { return `uxV2Layout:${name}:v2`; };
  UXV2.visibilityKey = function visibilityKey(name) { return `uxV2LayoutVisible:${name}:v2`; };
  UXV2.sizeKey = function sizeKey(name) { return `uxV2LayoutSize:${name}:v2`; };

  UXV2.applySavedLayout = function applySavedLayout(containerId, name) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const saved = safeJsonParse(localStorage.getItem(this.layoutKey(name)) || '[]', []);
    if (Array.isArray(saved)) saved.forEach(id => {
      const el = document.getElementById(id);
      if (el && el.parentNode === container) container.appendChild(el);
    });
    const visible = safeJsonParse(localStorage.getItem(this.visibilityKey(name)) || '{}', {});
    [...container.children].forEach(el => {
      if (!el.id) return;
      if (visible[el.id] === false) el.style.display = 'none';
      else if (!el.classList.contains('ux-v2-replaced')) el.style.removeProperty('display');
    });
    const sizes = safeJsonParse(localStorage.getItem(this.sizeKey(name)) || '{}', {});
    [...container.children].forEach(el => { if (el.id) el.dataset.uxSize = sizes[el.id] || 'full'; });
  };

  UXV2.setupSortableSections = function setupSortableSections(containerId, name) {
    const container = document.getElementById(containerId);
    if (!container) return;
    this.applySavedLayout(containerId, name);

    [...container.children].forEach(item => {
      if (!item.id || item.dataset.uxSortable) return;
      item.dataset.uxSortable = '1';
      item.classList.add('ux-sortable-item');
      const header = item.querySelector('.dash-section-header, .report-section-header');
      if (!header) return;
      const handle = document.createElement('button');
      handle.type = 'button';
      handle.className = 'ux-drag-handle';
      handle.title = 'Drag to reorder · Alt+↑/↓ for keyboard';
      handle.setAttribute('aria-label', 'Drag to reorder section; Alt plus arrow keys moves it');
      handle.textContent = '⠿';
      header.insertBefore(handle, header.firstChild);

      let dragging = false;
      let pointerId = null;
      // Where `item` would land if the pointer released right now --
      // tracked as DATA during the drag, not applied to the DOM until
      // finish() (drop). Reparenting the dragged node itself mid-drag
      // (even a same-parent "move" via insertBefore) reliably released
      // this pointer's capture in real testing (Chromium): the section
      // would even visibly jump once -- the DOM move genuinely
      // happened -- but every event after that point, including
      // pointerup, silently stopped reaching `handle`, so finish()
      // (and its localStorage save) never ran and the drag never
      // cleanly ended. Keeping the actual move to one single
      // insertBefore call in finish(), after the gesture is already
      // over, sidesteps the whole issue -- only the CSS drop-indicator
      // (harmless, no reparenting) updates during pointermove now.
      let pendingTarget = null;
      handle.addEventListener('pointerdown', e => {
        if (e.button != null && e.button !== 0) return;
        dragging = true;
        pointerId = e.pointerId;
        pendingTarget = null;
        handle.setPointerCapture(pointerId);
        item.classList.add('ux-dragging');
        e.preventDefault();
      });
      handle.addEventListener('pointermove', e => {
        if (!dragging) return;
        e.preventDefault();
        let target = document.elementFromPoint(e.clientX, e.clientY);
        while (target && target.parentNode !== container) target = target.parentNode;
        [...container.children].forEach(x => x.classList.remove('ux-drop-before', 'ux-drop-after'));
        if (!target || target === item || target.parentNode !== container) { pendingTarget = null; return; }
        const rect = target.getBoundingClientRect();
        const before = e.clientY < rect.top + rect.height / 2;
        target.classList.toggle('ux-drop-before', before);
        target.classList.toggle('ux-drop-after', !before);
        pendingTarget = { el: target, before };
      });
      const finish = () => {
        if (!dragging) return;
        dragging = false;
        item.classList.remove('ux-dragging');
        [...container.children].forEach(x => x.classList.remove('ux-drop-before', 'ux-drop-after'));
        if (pendingTarget && pendingTarget.el.isConnected && pendingTarget.el.parentNode === container) {
          if (pendingTarget.before) container.insertBefore(item, pendingTarget.el);
          else container.insertBefore(item, pendingTarget.el.nextSibling);
        }
        pendingTarget = null;
        const ids = [...container.children].map(x => x.id).filter(Boolean);
        localStorage.setItem(this.layoutKey(name), JSON.stringify(ids));
      };
      handle.addEventListener('pointerup', finish);
      handle.addEventListener('pointercancel', finish);
      handle.addEventListener('keydown', e => {
        if (!e.altKey || !['ArrowUp', 'ArrowDown'].includes(e.key)) return;
        e.preventDefault();
        const children = [...container.children];
        const index = children.indexOf(item);
        const targetIndex = index + (e.key === 'ArrowUp' ? -1 : 1);
        if (targetIndex < 0 || targetIndex >= children.length) return;
        if (targetIndex < index) container.insertBefore(item, children[targetIndex]);
        else container.insertBefore(children[targetIndex], item);
        localStorage.setItem(UXV2.layoutKey(name), JSON.stringify([...container.children].map(x => x.id).filter(Boolean)));
        handle.focus();
      });
    });
  };

  UXV2.applyDashboardDefaults = function applyDashboardDefaults() {
    const key = this.visibilityKey('dashboard');
    let visible = safeJsonParse(localStorage.getItem(key) || 'null', null);
    if (!visible) {
      visible = {
        dashSectionStats: false,
        dashSectionPositions: false,
        dashSectionCalendar: true,
        dashSectionActivity: true,
        dashSectionInstruments: true,
      };
      localStorage.setItem(key, JSON.stringify(visible));
    }
    this.applySavedLayout('dashboardSections', 'dashboard');
    this.setupSortableSections('dashboardSections', 'dashboard');
  };

  UXV2.setupDashboardCustomize = function setupDashboardCustomize() {
    const view = document.getElementById('view-dashboard');
    const actions = view?.querySelector('.view-header-actions');
    if (!view || !actions) return;
    if (!document.getElementById('uxCustomizeDashboardBtn')) {
      const btn = document.createElement('button');
      btn.id = 'uxCustomizeDashboardBtn';
      btn.className = 'btn btn-secondary btn-sm ux-customize-toggle';
      btn.textContent = 'Customize';
      btn.onclick = () => this.toggleDashboardCustomize();
      actions.appendChild(btn);
    }
    if (!document.getElementById('uxDashboardCustomizePanel')) {
      const panel = document.createElement('div');
      panel.id = 'uxDashboardCustomizePanel';
      panel.className = 'ux-customize-panel';
      document.getElementById('dashboardSections')?.parentNode.insertBefore(panel, document.getElementById('dashboardSections'));
    }
  };

  UXV2.toggleDashboardCustomize = function toggleDashboardCustomize() {
    const panel = document.getElementById('uxDashboardCustomizePanel');
    if (!panel) return;
    const opening = !panel.classList.contains('open');
    panel.classList.toggle('open', opening);
    document.getElementById('uxCustomizeDashboardBtn').textContent = opening ? 'Done' : 'Customize';
    if (!opening) return;
    const ids = ['dashSectionStats', 'dashSectionCalendar', 'dashSectionActivity', 'dashSectionInstruments'];
    const labels = {
      dashSectionStats: 'Legacy aggregate overview', dashSectionCalendar: 'Daily P&L Calendar',
      dashSectionActivity: 'Recent Activity', dashSectionInstruments: 'Subscribed Instruments',
    };
    const visible = safeJsonParse(localStorage.getItem(this.visibilityKey('dashboard')) || '{}', {});
    const sizes = safeJsonParse(localStorage.getItem(this.sizeKey('dashboard')) || '{}', {});
    panel.innerHTML = `<div class="table-note" style="margin-bottom:7px;">Drag widgets by the ⠿ handle on the page itself. The fixed "Right now" summary above is deliberately not customizable.</div>` + ids.map(id => `
      <div class="ux-customize-row">
        <label><input type="checkbox" style="width:auto;" ${visible[id] !== false ? 'checked' : ''} onchange="UXV2.setWidgetVisible('${id}', this.checked)"> ${labels[id]}</label>
        <select onchange="UXV2.setWidgetSize('${id}', this.value)"><option value="full" ${(sizes[id] || 'full') === 'full' ? 'selected' : ''}>Full width</option><option value="half" ${sizes[id] === 'half' ? 'selected' : ''}>Half width</option></select>
        <button class="btn btn-secondary btn-sm" onclick="document.getElementById('${id}').scrollIntoView({behavior:'smooth',block:'center'})">Locate</button>
      </div>`).join('') + `<div style="display:flex;justify-content:flex-end;margin-top:8px;"><button class="btn btn-secondary btn-sm" onclick="UXV2.resetDashboardLayout()">Reset layout</button></div>`;
  };

  UXV2.setWidgetVisible = function setWidgetVisible(id, visible) {
    const key = this.visibilityKey('dashboard');
    const prefs = safeJsonParse(localStorage.getItem(key) || '{}', {});
    prefs[id] = visible;
    localStorage.setItem(key, JSON.stringify(prefs));
    const el = document.getElementById(id);
    if (el) el.style.display = visible ? '' : 'none';
  };

  UXV2.setWidgetSize = function setWidgetSize(id, size) {
    const key = this.sizeKey('dashboard');
    const prefs = safeJsonParse(localStorage.getItem(key) || '{}', {});
    prefs[id] = size;
    localStorage.setItem(key, JSON.stringify(prefs));
    const el = document.getElementById(id);
    if (el) el.dataset.uxSize = size;
    document.getElementById('dashboardSections')?.classList.add('ux-widget-grid');
  };

  UXV2.resetDashboardLayout = function resetDashboardLayout() {
    localStorage.removeItem(this.layoutKey('dashboard'));
    localStorage.removeItem(this.visibilityKey('dashboard'));
    localStorage.removeItem(this.sizeKey('dashboard'));
    this.applyDashboardDefaults();
    this.toggleDashboardCustomize();
    this.toggleDashboardCustomize();
  };

  // ------------------------------------------------------------------
  // Generic tables: width resize, header reorder, persisted density.
  // It only enhances simple one-row-header tables where every body row
  // has the same cell count. Complex matrix/colspan tables opt out.
  // ------------------------------------------------------------------
  UXV2.tableKey = function tableKey(table) {
    if (table.dataset.uxTableKey) return table.dataset.uxTableKey;
    const parentId = table.closest('[id]')?.id || 'generic';
    const idx = [...document.querySelectorAll(`#${CSS.escape(parentId)} table`)].indexOf(table);
    const key = `${parentId}:${Math.max(0, idx)}`;
    table.dataset.uxTableKey = key;
    return key;
  };

  UXV2.normalizeColKey = function normalizeColKey(text, index) {
    const clean = String(text || '').replace(/[▲▼↕]/g, '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
    return clean || `col_${index}`;
  };

  UXV2.enhanceTable = function enhanceTable(table) {
    if (!table || table.dataset.uxEnhanced === '1' || table.classList.contains('ux-performance-matrix') || table.closest('.trade-json')) return;
    const headRow = table.tHead?.rows?.[0];
    if (!headRow || table.tHead.rows.length !== 1) return;
    const ths = [...headRow.cells];
    if (ths.some(th => Number(th.colSpan || 1) !== 1)) return;
    const bodyRows = [...(table.tBodies?.[0]?.rows || [])];
    if (bodyRows.some(r => r.cells.length !== ths.length)) return;

    table.dataset.uxEnhanced = '1';
    table.classList.add('ux-table');
    const key = this.tableKey(table);
    ths.forEach((th, i) => {
      if (!th.dataset.uxColKey) th.dataset.uxColKey = this.normalizeColKey(th.textContent, i);
    });

    this.applyTableOrder(table, key);
    this.applyTableWidths(table, key);
    this.applyTableVisibility(table, key);
    this.applyTableDensity(table, key);
    this.attachTableColumnInteractions(table, key);
    this.ensureTableFloatingSettings(table);
  };

  // A tfoot totals row often has a leading LABEL cell spanning several
  // logical columns (colspan, e.g. detail.js's Positions total row:
  // colspan=4 over Symbol/Side/Qty/Avg, then one real cell each for
  // Price's own "Total" caption and Unrealized's actual sum) -- its
  // cells never line up 1:1 with the header's own `row.cells[index]`
  // the way a normal body row's do. Every tfoot cell that DOES
  // represent one specific column is tagged `data-ux-col-key` with
  // that column's own key (matching the header th's own key exactly --
  // see detail.js/deployments.js's own tfoot markup) precisely so
  // order/resize/visibility can find it by KEY instead of assuming
  // position, and correctly leave an untagged spacer cell (the label)
  // alone rather than misapplying a numeric column's own move/resize/
  // hide to whatever DOM cell happens to sit at that index instead.
  UXV2.tfootCellsByKey = function tfootCellsByKey(table) {
    if (!table.tFoot) return [];
    const cells = [];
    [...table.tFoot.rows].forEach(row => [...row.cells].forEach(cell => {
      if (cell.dataset.uxColKey) cells.push(cell);
    }));
    return cells;
  };

  UXV2.applyTableOrder = function applyTableOrder(table, key) {
    const saved = safeJsonParse(localStorage.getItem(`uxV2TableOrder:${key}`) || '[]', []);
    if (!Array.isArray(saved) || !saved.length) return;
    const head = table.tHead.rows[0];
    const currentKeys = [...head.cells].map(th => th.dataset.uxColKey);
    const desired = saved.filter(k => currentKeys.includes(k)).concat(currentKeys.filter(k => !saved.includes(k)));
    desired.forEach(k => {
      const idx = [...head.cells].findIndex(th => th.dataset.uxColKey === k);
      if (idx < 0) return;
      const th = head.cells[idx];
      head.appendChild(th);
      [...table.tBodies].forEach(tb => [...tb.rows].forEach(row => row.appendChild(row.cells[idx])));
      this.tfootCellsByKey(table).filter(c => c.dataset.uxColKey === k).forEach(c => c.parentNode.appendChild(c));
    });
  };

  UXV2.applyTableWidths = function applyTableWidths(table, key) {
    const widths = safeJsonParse(localStorage.getItem(`uxV2TableWidths:${key}`) || '{}', {});
    const head = table.tHead.rows[0];
    [...head.cells].forEach((th, i) => {
      const w = Number(widths[th.dataset.uxColKey]);
      if (w > 0) this.setColumnWidth(table, i, w);
    });
  };

  UXV2.setColumnWidth = function setColumnWidth(table, index, px) {
    const width = Math.max(70, Math.min(520, Math.round(px)));
    const applyTo = cell => {
      cell.style.width = `${width}px`;
      cell.style.minWidth = `${width}px`;
      cell.style.maxWidth = `${width}px`;
    };
    const head = table.tHead?.rows?.[0];
    const colKey = head?.cells?.[index]?.dataset.uxColKey;
    const rows = [head, ...(table.tBodies?.[0]?.rows || [])].filter(Boolean);
    rows.forEach(row => {
      const cell = row.cells[index];
      if (!cell || Number(cell.colSpan || 1) !== 1) return;
      applyTo(cell);
    });
    if (colKey) this.tfootCellsByKey(table).filter(c => c.dataset.uxColKey === colKey).forEach(applyTo);
  };

  UXV2.applyTableVisibility = function applyTableVisibility(table, key) {
    const prefs = safeJsonParse(localStorage.getItem(`uxV2TableVisible:${key}`) || '{}', {});
    const head = table.tHead?.rows?.[0];
    if (!head) return;
    [...head.cells].forEach((th, index) => {
      const colKey = th.dataset.uxColKey;
      const visible = prefs[colKey] !== false;
      const display = visible ? '' : 'none';
      const rows = [head, ...(table.tBodies?.[0]?.rows || [])];
      rows.forEach(row => {
        const cell = row.cells[index];
        if (!cell || Number(cell.colSpan || 1) !== 1) return;
        cell.style.display = display;
      });
      this.tfootCellsByKey(table).filter(c => c.dataset.uxColKey === colKey).forEach(c => { c.style.display = display; });
    });
  };

  UXV2.setTableColumnVisible = function setTableColumnVisible(table, colKey, visible) {
    const key = this.tableKey(table);
    const prefs = safeJsonParse(localStorage.getItem(`uxV2TableVisible:${key}`) || '{}', {});
    prefs[colKey] = visible;
    localStorage.setItem(`uxV2TableVisible:${key}`, JSON.stringify(prefs));
    this.applyTableVisibility(table, key);
  };

  UXV2.ensureTableFloatingSettings = function ensureTableFloatingSettings(table) {
    const wrap = table.closest('.table-wrap');
    if (!wrap || wrap.querySelector(':scope > .ux-table-floating-settings')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ux-table-floating-settings';
    btn.textContent = 'Table ▾';
    btn.setAttribute('aria-label', 'Table settings');
    btn.addEventListener('click', e => this.openTableSettingsForElement(e, table));
    wrap.insertBefore(btn, wrap.firstChild);
  };

  UXV2.openTableSettingsForElement = function openTableSettingsForElement(event, table) {
    event.stopPropagation();
    const key = this.tableKey(table);
    const density = localStorage.getItem(`uxV2TableDensity:${key}`) || 'comfortable';
    const visibility = safeJsonParse(localStorage.getItem(`uxV2TableVisible:${key}`) || '{}', {});
    const columns = [...(table.tHead?.rows?.[0]?.cells || [])];
    this.openPopover(event.currentTarget, `
      <div class="ux-menu-title">Columns</div>
      <div class="ux-table-column-list">${columns.map((th, i) => {
        const colKey = th.dataset.uxColKey;
        const label = String(th.textContent || '').trim() || `Column ${i + 1}`;
        return `<label><input type="checkbox" ${visibility[colKey] === false ? '' : 'checked'} data-ux-toggle-col="${escapeHtml(colKey)}"> ${escapeHtml(label)}</label>`;
      }).join('')}</div>
      <div class="ux-menu-sep"></div>
      <button class="ux-menu-item" data-ux-density="comfortable">Comfortable density <span>${density === 'comfortable' ? '✓' : ''}</span></button>
      <button class="ux-menu-item" data-ux-density="compact">Compact density <span>${density === 'compact' ? '✓' : ''}</span></button>
      <div class="ux-menu-sep"></div>
      <button class="ux-menu-item" data-ux-reset-table>Reset layout</button>`);
    const pop = this._openPopover;
    pop.querySelectorAll('[data-ux-toggle-col]').forEach(input => input.addEventListener('change', () => this.setTableColumnVisible(table, input.dataset.uxToggleCol, input.checked)));
    pop.querySelectorAll('[data-ux-density]').forEach(btn => btn.addEventListener('click', () => {
      localStorage.setItem(`uxV2TableDensity:${key}`, btn.dataset.uxDensity);
      this.applyTableDensity(table, key); this.closePopover();
    }));
    pop.querySelector('[data-ux-reset-table]')?.addEventListener('click', () => {
      localStorage.removeItem(`uxV2TableOrder:${key}`);
      localStorage.removeItem(`uxV2TableWidths:${key}`);
      localStorage.removeItem(`uxV2TableVisible:${key}`);
      localStorage.removeItem(`uxV2TableDensity:${key}`);
      this.closePopover();
      window.location.reload();
    });
  };

  UXV2.applyTableDensity = function applyTableDensity(table, key) {
    const density = localStorage.getItem(`uxV2TableDensity:${key}`) || 'comfortable';
    table.classList.toggle('ux-table-density-compact', density === 'compact');
  };

  UXV2.attachTableColumnInteractions = function attachTableColumnInteractions(table, key) {
    const head = table.tHead.rows[0];
    [...head.cells].forEach((th, index) => {
      th.classList.add('ux-col-draggable');
      th.draggable = true;
      const handle = document.createElement('span');
      handle.className = 'ux-col-resizer';
      handle.title = 'Drag to resize · double-click to auto-fit';
      th.appendChild(handle);

      handle.addEventListener('pointerdown', e => {
        e.preventDefault(); e.stopPropagation();
        const currentIndex = [...head.cells].indexOf(th);
        const startX = e.clientX;
        const startW = th.getBoundingClientRect().width;
        document.body.classList.add('ux-resizing');
        handle.setPointerCapture(e.pointerId);
        const move = ev => this.setColumnWidth(table, currentIndex, startW + ev.clientX - startX);
        const up = ev => {
          handle.releasePointerCapture?.(ev.pointerId);
          handle.removeEventListener('pointermove', move);
          handle.removeEventListener('pointerup', up);
          handle.removeEventListener('pointercancel', up);
          document.body.classList.remove('ux-resizing');
          const widths = safeJsonParse(localStorage.getItem(`uxV2TableWidths:${key}`) || '{}', {});
          widths[th.dataset.uxColKey] = Math.round(th.getBoundingClientRect().width);
          localStorage.setItem(`uxV2TableWidths:${key}`, JSON.stringify(widths));
        };
        handle.addEventListener('pointermove', move);
        handle.addEventListener('pointerup', up);
        handle.addEventListener('pointercancel', up);
      });
      handle.addEventListener('dblclick', e => {
        e.preventDefault(); e.stopPropagation();
        const currentIndex = [...head.cells].indexOf(th);
        let max = Math.max(88, th.scrollWidth + 24);
        [...(table.tBodies?.[0]?.rows || [])].slice(0, 150).forEach(row => {
          const cell = row.cells[currentIndex];
          if (cell) max = Math.max(max, cell.scrollWidth + 22);
        });
        this.setColumnWidth(table, currentIndex, Math.min(420, max));
        const widths = safeJsonParse(localStorage.getItem(`uxV2TableWidths:${key}`) || '{}', {});
        widths[th.dataset.uxColKey] = Math.round(Math.min(420, max));
        localStorage.setItem(`uxV2TableWidths:${key}`, JSON.stringify(widths));
      });

      th.addEventListener('dragstart', e => {
        if (document.body.classList.contains('ux-resizing')) { e.preventDefault(); return; }
        th.classList.add('ux-col-dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/ux-col-key', th.dataset.uxColKey);
      });
      th.addEventListener('dragend', () => {
        th.classList.remove('ux-col-dragging');
        [...head.cells].forEach(x => x.classList.remove('ux-col-drop-target'));
      });
      th.addEventListener('dragover', e => {
        if (!e.dataTransfer.types.includes('text/ux-col-key')) return;
        e.preventDefault();
        th.classList.add('ux-col-drop-target');
      });
      th.addEventListener('dragleave', () => th.classList.remove('ux-col-drop-target'));
      th.addEventListener('drop', e => {
        e.preventDefault();
        th.classList.remove('ux-col-drop-target');
        const fromKey = e.dataTransfer.getData('text/ux-col-key');
        const fromIndex = [...head.cells].findIndex(x => x.dataset.uxColKey === fromKey);
        const toIndex = [...head.cells].indexOf(th);
        if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return;
        this.moveTableColumn(table, fromIndex, toIndex);
        const order = [...head.cells].map(x => x.dataset.uxColKey);
        localStorage.setItem(`uxV2TableOrder:${key}`, JSON.stringify(order));
      });
    });
  };

  UXV2.moveTableColumn = function moveTableColumn(table, fromIndex, toIndex) {
    const head = table.tHead.rows[0];
    const fromKey = head.cells[fromIndex]?.dataset.uxColKey;
    const toKey = head.cells[toIndex]?.dataset.uxColKey;
    const moveCell = row => {
      if (!row || row.cells.length <= Math.max(fromIndex, toIndex)) return;
      const cell = row.cells[fromIndex];
      const ref = fromIndex < toIndex ? row.cells[toIndex]?.nextSibling : row.cells[toIndex];
      row.insertBefore(cell, ref || null);
    };
    moveCell(head);
    [...table.tBodies].forEach(tb => [...tb.rows].forEach(moveCell));
    // tfoot -- move by COLUMN KEY, not DOM index (see tfootCellsByKey's
    // own comment): a totals row's label cell commonly spans several
    // logical columns via colspan, so `row.cells[fromIndex]` there is
    // almost never the cell that actually corresponds to the column
    // being dragged. Only cells explicitly tagged for one of the two
    // columns actually involved in this move need to move at all --
    // an untagged spacer cell is deliberately left exactly where it is.
    if (fromKey && toKey) this.tfootCellsByKey(table).forEach(cell => {
      if (cell.dataset.uxColKey !== fromKey) return;
      const row = cell.parentNode;
      const toCell = [...row.cells].find(c => c.dataset.uxColKey === toKey);
      if (!toCell) return;
      const fromIdx = [...row.cells].indexOf(cell);
      const toIdx = [...row.cells].indexOf(toCell);
      const ref = fromIdx < toIdx ? toCell.nextSibling : toCell;
      row.insertBefore(cell, ref || null);
    });
  };

  UXV2.enhanceTables = function enhanceTables(root = document) {
    root.querySelectorAll?.('table').forEach(t => this.enhanceTable(t));
  };
  UXV2.enhanceTablesSoon = debounce(function () { UXV2.enhanceTables(document); }, 20);

  UXV2.startTableObserver = function startTableObserver() {
    if (this.tableObserver) return;
    this.tableObserver = new MutationObserver(() => { this.enhanceTablesSoon(); this.renameAnalyticsSemantics(document); });
    const main = document.querySelector('main.main');
    if (main) this.tableObserver.observe(main, { childList: true, subtree: true });
    this.enhanceTablesSoon();
  };

  UXV2.openTableSettings = function openTableSettings(event, containerId) {
    event.stopPropagation();
    const table = document.querySelector(`#${CSS.escape(containerId)} table`);
    if (!table) return;
    const key = this.tableKey(table);
    const density = localStorage.getItem(`uxV2TableDensity:${key}`) || 'comfortable';
    this.openPopover(event.currentTarget, `
      <button class="ux-menu-item" onclick="UXV2.setTableDensity('${containerId}','comfortable')">Comfortable density <span>${density === 'comfortable' ? '✓' : ''}</span></button>
      <button class="ux-menu-item" onclick="UXV2.setTableDensity('${containerId}','compact')">Compact density <span>${density === 'compact' ? '✓' : ''}</span></button>
      <div class="ux-menu-sep"></div>
      <button class="ux-menu-item" onclick="UXV2.resetTableLayout('${containerId}')">Reset widths &amp; column order</button>`);
  };

  UXV2.setTableDensity = function setTableDensity(containerId, density) {
    const table = document.querySelector(`#${CSS.escape(containerId)} table`);
    if (!table) return;
    localStorage.setItem(`uxV2TableDensity:${this.tableKey(table)}`, density);
    this.applyTableDensity(table, this.tableKey(table));
    this.closePopover();
  };

  UXV2.resetTableLayout = function resetTableLayout(containerId) {
    const table = document.querySelector(`#${CSS.escape(containerId)} table`);
    if (!table) return;
    const key = this.tableKey(table);
    localStorage.removeItem(`uxV2TableOrder:${key}`);
    localStorage.removeItem(`uxV2TableWidths:${key}`);
    localStorage.removeItem(`uxV2TableVisible:${key}`);
    this.closePopover();
    // The containing view's normal render restores the canonical order.
    if (containerId === 'deploymentsTable') Deployments.render();
    else window.location.reload();
  };

  // ------------------------------------------------------------------
  // Deployments view: mode-aware current P&L, quick status chips,
  // compare selection tray and calmer default columns.
  // ------------------------------------------------------------------
  UXV2.installDeploymentColumns = function installDeploymentColumns() {
    if (typeof DEPLOY_COLUMNS === 'undefined' || DEPLOY_COLUMNS.some(c => c.key === 'ux_current_pnl')) return;
    DEPLOY_COLUMNS.unshift({
      key: 'ux_select', label: '', always: true, sortable: false,
      render: d => `<span class="ux-select-cell"><input type="checkbox" aria-label="Select ${escapeHtml(d.deployment_name)} for comparison" ${UXV2.selectedDeployments.has(d.id) ? 'checked' : ''} onclick="event.stopPropagation()" onchange="UXV2.toggleDeploymentSelection('${d.id}', this.checked)"></span>`,
      csvValue: () => '', sortValue: () => '',
    });
    const modeIndex = DEPLOY_COLUMNS.findIndex(c => c.key === 'mode');
    const insertAt = modeIndex >= 0 ? modeIndex + 1 : 4;
    DEPLOY_COLUMNS.splice(insertAt, 0,
      {
        key: 'ux_current_pnl', label: 'Current P&L', numeric: true,
        headerTitle: 'Intraday = Today. Positional = currently active strategic cycle.',
        sortValue: d => d._uxActive?.total_pnl ?? -Infinity,
        render: d => {
          const a = d._uxActive;
          if (!a) return '<span class="card-sub">calculating…</span>';
          if (d.mode === 'positional' && !a.active) {
            return `<span class="ux-current-pnl"><span>—</span><span class="ux-pnl-period">Flat${a.last_cycle_pnl != null ? ` · last ${asMoney(a.last_cycle_pnl)}` : ''}</span></span>`;
          }
          return `<span class="ux-current-pnl ${pnlClassSafe(a.total_pnl)}">${asMoney(a.total_pnl)}<span class="ux-pnl-period">${escapeHtml(a.period_label)}</span></span>`;
        },
        csvValue: d => d._uxActive?.total_pnl ?? '',
        // total(ctx) -- deployments.js's own totals row (tfoot) calls
        // this for whichever numeric columns are currently visible;
        // see DEPLOY_COLUMNS' own comment (deployments.js) for why a
        // column now owns its own total instead of deployments.js
        // hardcoding a lookup keyed by column -- before this, adding
        // this column here made its own tfoot cell render permanently
        // empty (numeric:true told the totals row to render a cell,
        // but nothing ever computed a value for it), which is what
        // made the ENTIRE totals row look blank the moment this
        // became a default-visible column.
        //
        // Deliberately sums over ctx.allRows, not ctx.reportRows --
        // unlike the legacy accounting columns (Capital/Cash/
        // Realized/...), which stay scoped to include_in_reports=true
        // rows for historical-performance consistency, Current P&L is
        // the SAME operational-truth number the Dashboard's own
        // "Right now" zone shows (Active P&L, "includes every live
        // deployment, even if excluded from analytics") -- scoping it
        // to reportRows here would silently disagree with that.
        total: ({ allRows }) => {
          const ready = allRows.filter(d => d._uxActive);
          if (!ready.length) return '';
          const t = ready.reduce((s, d) => s + (d._uxActive.total_pnl || 0), 0);
          return `<span class="ux-current-pnl ${pnlClassSafe(t)}">${asMoney(t)}</span>`;
        },
      },
      {
        key: 'ux_open_positions', label: 'Open', numeric: true,
        sortValue: d => d._uxActive?.open_positions ?? 0,
        render: d => String(d._uxActive?.open_positions ?? 0),
        total: ({ allRows }) => String(allRows.reduce((s, d) => s + (d._uxActive?.open_positions || 0), 0)),
      },
      {
        key: 'ux_last_action', label: 'Last action',
        sortValue: d => d._uxActive?.last_action_at ? new Date(d._uxActive.last_action_at).getTime() : 0,
        render: d => d._uxActive?.last_action_at
          ? `<span title="${escapeHtml(d._uxActive.last_action || '')}">${humanAgo(d._uxActive.last_action_at)}</span>`
          : '<span class="card-sub">—</span>',
        csvValue: d => d._uxActive?.last_action_at || '',
      },
    );
  };

  UXV2.toggleDeploymentSelection = function toggleDeploymentSelection(id, checked) {
    if (checked) {
      if (this.selectedDeployments.size >= 6) {
        alert('Compare supports up to 6 deployments at a time.');
        Deployments.render();
        return;
      }
      this.selectedDeployments.add(id);
    } else this.selectedDeployments.delete(id);
    this.updateSelectionTray();
  };

  UXV2.ensureSelectionTray = function ensureSelectionTray() {
    let tray = document.getElementById('uxSelectionTray');
    if (!tray) {
      tray = document.createElement('div');
      tray.id = 'uxSelectionTray';
      tray.className = 'ux-selection-tray';
      tray.innerHTML = `<strong id="uxSelectionCount">0 selected</strong><div style="display:flex;gap:7px;"><button class="btn btn-secondary btn-sm" onclick="UXV2.clearDeploymentSelection()">Clear</button><button class="btn btn-primary btn-sm" onclick="UXV2.compareDeploymentSelection()">Compare</button></div>`;
      document.body.appendChild(tray);
    }
    return tray;
  };

  UXV2.updateSelectionTray = function updateSelectionTray() {
    const tray = this.ensureSelectionTray();
    tray.classList.toggle('open', this.selectedDeployments.size >= 1);
    document.getElementById('uxSelectionCount').textContent = `${this.selectedDeployments.size} selected`;
    const compareBtn = tray.querySelector('.btn-primary');
    compareBtn.disabled = this.selectedDeployments.size < 2;
  };

  UXV2.clearDeploymentSelection = function clearDeploymentSelection() {
    this.selectedDeployments.clear();
    this.updateSelectionTray();
    if (typeof Deployments !== 'undefined') Deployments.render();
  };

  UXV2.compareDeploymentSelection = function compareDeploymentSelection() {
    if (this.selectedDeployments.size < 2) return;
    sessionStorage.setItem('uxV2CompareSelection', JSON.stringify([...this.selectedDeployments]));
    window.location.hash = '#/compare';
  };

  UXV2.ensureDeploymentQuickFilters = function ensureDeploymentQuickFilters() {
    const view = document.getElementById('view-deployments');
    const filters = view?.querySelector('.filters');
    if (!view || !filters) return;
    let chips = document.getElementById('uxDeploymentStatusChips');
    if (!chips) {
      chips = document.createElement('div');
      chips.id = 'uxDeploymentStatusChips';
      chips.className = 'ux-status-chips';
      filters.parentNode.insertBefore(chips, filters);
    }
    const counts = { '': Deployments._all.length, active: 0, paused: 0, stopped: 0 };
    Deployments._all.forEach(d => { counts[d.status] = (counts[d.status] || 0) + 1; });
    const current = document.getElementById('filterStatus')?.value || '';
    chips.innerHTML = [
      ['', 'All'], ['active', 'Active'], ['paused', 'Paused'], ['stopped', 'Stopped'],
    ].map(([value, label]) => `<button class="ux-filter-chip ${current === value ? 'active' : ''}" onclick="UXV2.setDeploymentStatusFilter('${value}')">${label} ${counts[value] || 0}</button>`).join('');

    if (!document.getElementById('uxDeploymentTableSettings')) {
      const colWrap = document.getElementById('colSelectorWrap');
      if (colWrap) {
        const btn = document.createElement('button');
        btn.id = 'uxDeploymentTableSettings';
        btn.className = 'btn btn-secondary btn-sm';
        btn.textContent = 'Table ▾';
        btn.onclick = e => this.openTableSettings(e, 'deploymentsTable');
        colWrap.parentNode.insertBefore(btn, colWrap.nextSibling);
      }
    }
  };

  UXV2.setDeploymentStatusFilter = function setDeploymentStatusFilter(value) {
    const select = document.getElementById('filterStatus');
    if (select) select.value = value;
    Deployments.render();
    this.ensureDeploymentQuickFilters();
  };

  UXV2.saveDeploymentListState = function saveDeploymentListState() {
    if (typeof Deployments === 'undefined') return;
    sessionStorage.setItem('uxV2DeploymentListState', JSON.stringify({
      status: document.getElementById('filterStatus')?.value || '',
      strategy: document.getElementById('filterStrategy')?.value || '',
      search: document.getElementById('deploymentsSearch')?.value || '',
      sortKey: Deployments._sortKey,
      sortDir: Deployments._sortDir,
      scrollY: window.scrollY,
    }));
  };

  UXV2.restoreDeploymentListState = function restoreDeploymentListState() {
    const state = safeJsonParse(sessionStorage.getItem('uxV2DeploymentListState') || 'null', null);
    if (!state) return;
    const status = document.getElementById('filterStatus');
    const strategy = document.getElementById('filterStrategy');
    const search = document.getElementById('deploymentsSearch');
    if (status) status.value = state.status || '';
    if (strategy && [...strategy.options].some(o => o.value === state.strategy)) strategy.value = state.strategy || '';
    if (search) search.value = state.search || '';
    if (state.sortKey) Deployments._sortKey = state.sortKey;
    if (state.sortDir) Deployments._sortDir = state.sortDir;
    Deployments.render();
    requestAnimationFrame(() => window.scrollTo(0, Number(state.scrollY || 0)));
  };

  // ------------------------------------------------------------------
  // Detail information architecture
  // ------------------------------------------------------------------
  UXV2.detailRoute = function detailRoute() {
    const clean = (window.location.hash || '').replace(/^#\/?/, '');
    const parts = clean.split('/');
    if (parts[0] !== 'deployments' || !parts[1]) return { id: null, section: 'overview' };
    const section = ['overview', 'analytics', 'history', 'configuration'].includes(parts[2]) ? parts[2] : 'overview';
    return { id: parts[1], section };
  };

  UXV2.detailTabLabel = { overview: 'Overview', analytics: 'Analytics', history: 'History', configuration: 'Configuration' };

  UXV2.renderDetailHeader = function renderDetailHeader(dep) {
    const actions = [];
    if (dep.status === 'active') actions.push(`<button class="btn btn-primary btn-sm" onclick="Detail.pause()">Pause</button>`);
    if (dep.status === 'paused') actions.push(`<button class="btn btn-primary btn-sm" onclick="Detail.resume()">Resume</button>`);
    actions.push(`<button class="btn btn-secondary btn-sm" onclick="UXV2.toggleDetailMenu(event)">⋯ More</button>`);
    document.getElementById('detailHeader').innerHTML = `
      <div class="ux-detail-header">
        <div>
          <div class="ux-detail-title-row"><h1>${escapeHtml(dep.deployment_name)}</h1><span class="tag tag-${dep.status}">${dep.status}</span><span class="tag tag-info">${escapeHtml(dep.mode)}</span></div>
          <div class="ux-detail-sub">${escapeHtml(dep.strategy_name)}${dep.created_at ? ` · deployed ${humanAgo(dep.created_at)}` : ''}</div>
          <div style="margin-top:7px;">${deploymentTagsHtml(dep)}</div>
          ${dep.notes ? `<div class="card-sub" style="margin-top:7px;max-width:760px;">📝 ${escapeHtml(dep.notes)}</div>` : ''}
        </div>
        <div class="ux-detail-actions">${actions.join('')}</div>
      </div>`;
  };

  UXV2.toggleDetailMenu = function toggleDetailMenu(event) {
    event.stopPropagation();
    const dep = Detail._dep;
    this.openPopover(event.currentTarget, `
      <button class="ux-menu-item" onclick="UXV2.closePopover(); Detail.openEditModal()">Edit details</button>
      ${dep.status === 'paused' ? '<button class="ux-menu-item" onclick="UXV2.closePopover(); Detail.openEditConfigModal()">Edit configuration</button>' : ''}
      <button class="ux-menu-item" onclick="UXV2.closePopover(); location.hash='#/deployments/${dep.id}/configuration'">View configuration</button>
      <div class="ux-menu-sep"></div>
      ${dep.status !== 'stopped' ? `<button class="ux-menu-item" style="color:var(--loss)" onclick="UXV2.closePopover(); UXV2.openStopDialog('${dep.id}', ${JSON.stringify(dep.deployment_name)})">Stop deployment</button>` : ''}
      ${dep.status === 'stopped' ? `<button class="ux-menu-item" style="color:var(--loss)" onclick="UXV2.closePopover(); Detail.deleteDeployment()">Delete deployment</button>` : ''}`);
  };

  UXV2.renderDetailTabs = function renderDetailTabs() {
    const tabs = ['overview', 'analytics', 'history', 'configuration'];
    const route = this.detailRoute();
    Detail._tab = route.section;
    const el = document.getElementById('detailTabs');
    el.className = 'tabs ux-detail-nav';
    el.innerHTML = tabs.map(t => `<button class="${route.section === t ? 'active' : ''}" onclick="Detail.switchTab('${t}')">${this.detailTabLabel[t]}</button>`).join('');
  };

  UXV2.renderDetailOverview = async function renderDetailOverview() {
    window.LivePnl?.untrack(this.detailLiveHandler);
    this.detailLiveHandler = null;
    const dep = Detail._dep;
    const body = document.getElementById('detailBody');
    body.innerHTML = spinnerHtml();
    try {
      const [summary, openPositions, allPositions, status, snapshots, tradesPage] = await Promise.all([
        this.getActiveSummary(dep, true),
        Api.getPositions(dep.id, 'open'),
        Api.getPositions(dep.id, 'all'),
        Api.getStrategyStatus(dep.id).catch(() => null),
        Api.getSnapshots(dep.id).catch(() => []),
        Api.getTrades(dep.id, 8).catch(() => ({ lots: [] })),
      ]);
      dep._uxActive = summary;
      const totalPnl = Number(dep.realized_pnl || 0) + Number(dep.unrealized_pnl || 0);
      const totalReturn = dep.initial_capital ? (totalPnl / dep.initial_capital) * 100 : null;
      const units = typeof groupPositionsIntoUnits === 'function' ? groupPositionsIntoUnits(allPositions, 'position') : [];
      const closed = units.filter(u => u.status === 'closed');
      const pnls = closed.map(u => Number(u.realized_pnl || 0));
      const wins = pnls.filter(v => v > 0);
      const losses = pnls.filter(v => v <= 0);
      const winRate = pnls.length ? wins.length / pnls.length * 100 : null;
      const grossWin = wins.reduce((a, b) => a + b, 0);
      const grossLoss = losses.filter(v => v < 0).reduce((a, b) => a + b, 0);
      const profitFactor = grossLoss < 0 ? grossWin / Math.abs(grossLoss) : grossWin > 0 ? Infinity : null;
      const avgPosition = pnls.length ? pnls.reduce((a, b) => a + b, 0) / pnls.length : null;
      const dd = typeof computeMaxDrawdown === 'function' ? computeMaxDrawdown(snapshots, dep.initial_capital) : null;

      body.innerHTML = `
        <div class="ux-detail-summary-grid">
          <div class="ux-detail-summary-card">
            <div class="label">${dep.mode === 'positional' ? (summary.active ? 'Current cycle' : 'Current cycle') : "Today's P&L"}</div>
            <div class="value ${pnlClassSafe(summary.total_pnl)}" id="uxDetailActivePnl">${dep.mode === 'positional' && !summary.active ? 'Flat' : asMoney(summary.total_pnl)}</div>
            ${dep.mode === 'positional' && !summary.active
              ? `<div class="row"><span>Last cycle</span><b class="${pnlClassSafe(summary.last_cycle_pnl)}">${summary.last_cycle_pnl == null ? '—' : asMoney(summary.last_cycle_pnl)}</b></div>`
              : `<div class="row"><span>Realized</span><b class="${pnlClassSafe(summary.realized_pnl)}">${asMoney(summary.realized_pnl)}</b></div><div class="row"><span>Open</span><b class="${pnlClassSafe(summary.unrealized_pnl)}" id="uxDetailOpenPnl">${asMoney(summary.unrealized_pnl)}</b></div>`}
            <div class="row"><span>${summary.started_at ? `Started ${humanAgo(summary.started_at)}` : 'Open positions'}</span><b>${summary.open_positions}</b></div>
          </div>
          <div class="ux-detail-summary-card">
            <div class="label">Total return</div>
            <div class="value ${pnlClassSafe(totalPnl)}">${asMoney(totalPnl)}</div>
            <div class="row"><span>Return</span><b class="${pnlClassSafe(totalReturn)}">${totalReturn == null ? '—' : `${totalReturn >= 0 ? '+' : ''}${totalReturn.toFixed(2)}%`}</b></div>
            <div class="row"><span>Realized all-time</span><b class="${pnlClassSafe(dep.realized_pnl)}">${asMoney(dep.realized_pnl)}</b></div>
            <div class="row"><span>Initial capital</span><b>${fmtMoney(dep.initial_capital)}</b></div>
          </div>
          <div class="ux-detail-summary-card">
            <div class="label">Open positions</div>
            <div class="value">${openPositions.length}</div>
            <div class="row"><span>Live unrealized</span><b class="${pnlClassSafe(summary.unrealized_pnl)}">${asMoney(summary.unrealized_pnl)}</b></div>
            <div class="row"><span>Cash</span><b>${fmtMoney(dep.current_cash)}</b></div>
            <div class="row"><span>Analytics</span><b>${dep.include_in_reports ? 'Included' : 'Excluded'}</b></div>
          </div>
        </div>

        ${openPositions.length ? `<section class="ux-section">
          <div class="ux-section-head"><h2>${dep.mode === 'positional' ? 'Current position / cycle' : "Today's open positions"}</h2><span class="card-sub">Live from existing tick SSE</span></div>
          ${this.detailPositionsTable(openPositions)}
        </section>` : `<section class="ux-section"><div class="ux-section-head"><h2>Current position</h2></div><div class="empty">Flat right now${summary.last_cycle_pnl != null ? ` · last cycle ${asMoney(summary.last_cycle_pnl)}` : ''}</div></section>`}

        ${status?.fields?.length ? `<section class="ux-section">
          <div class="ux-section-head"><h2>Live strategy state</h2>${status.source === 'persisted' ? '<span class="tag tag-warn">as of last checkpoint</span>' : '<span class="tag tag-active">live</span>'}</div>
          <div class="ux-live-state-grid">${status.fields.map(f => `<div class="ux-live-state-item"><div class="k">${escapeHtml(f.label)}</div><div class="v">${escapeHtml(String(f.value))}</div></div>`).join('')}</div>
        </section>` : ''}

        <section class="ux-section">
          <div class="ux-section-head"><h2>Performance snapshot</h2><a href="#/deployments/${dep.id}/analytics">Full analytics →</a></div>
          <div class="stat-grid">
            <div class="stat-card"><div class="stat-label">Win rate</div><div class="stat-value">${winRate == null ? '—' : `${winRate.toFixed(1)}%`}</div><div class="stat-sub">${closed.length} closed strategic position${closed.length === 1 ? '' : 's'}</div></div>
            <div class="stat-card"><div class="stat-label">Profit factor</div><div class="stat-value">${profitFactor == null ? '—' : profitFactor === Infinity ? '∞' : profitFactor.toFixed(2)}</div><div class="stat-sub">Gross wins ÷ gross losses</div></div>
            <div class="stat-card"><div class="stat-label">Max drawdown</div><div class="stat-value ${dd ? 'neg' : ''}">${dd ? `${dd.pct.toFixed(2)}%` : '—'}</div><div class="stat-sub">${dd ? fmtMoney(dd.abs) : 'Not enough history'}</div></div>
            <div class="stat-card"><div class="stat-label">Avg position P&amp;L</div><div class="stat-value ${avgPosition == null ? '' : pnlClassSafe(avgPosition)}">${avgPosition == null ? '—' : asMoney(avgPosition)}</div><div class="stat-sub">Whole strategic cycles, not individual legs</div></div>
          </div>
        </section>

        <section class="ux-section">
          <div class="ux-section-head"><h2>Recent activity</h2><a href="#/deployments/${dep.id}/history">View full history →</a></div>
          ${tradesPage.lots?.length ? `<div class="ux-recent-list">${tradesPage.lots.slice(0, 6).map((t, i) => `<div class="ux-recent-item" onclick="UXV2.openOverviewTrade(${i})" style="cursor:pointer;"><span class="ux-recent-time">${fmtDateTime(t.executed_at)}</span><b>${escapeHtml(t.action)}</b><span>${escapeHtml(t.symbol)} · ${escapeHtml(t.reason || 'execution')}</span><span>${fmtNum(t.price)}</span></div>`).join('')}</div>` : '<div class="empty">No fills recorded yet.</div>'}
        </section>`;

      this.overviewTrades = tradesPage.lots || [];
      if (window.LivePnl && openPositions.length) {
        this.detailLiveHandler = window.LivePnl.track(openPositions, ({ pnlFor, priceFor, totalPnl }) => {
          if (Detail._tab !== 'overview') return;
          const open = totalPnl();
          if (open != null) {
            this.setLiveMoney('uxDetailOpenPnl', open);
            this.setLiveMoney('uxDetailActivePnl', Number(summary.realized_pnl || 0) + open);
            // Same combined total the two KPI cards above just used --
            // keeps the Current Position/Cycle table's own totals row
            // (detailPositionsTable) live-ticking in step with them,
            // not frozen at whatever it showed on the last full render.
            const footCell = body.querySelector('.live-pnl-total');
            if (footCell) { footCell.textContent = asMoney(open); footCell.className = `live-pnl-total ${pnlClassSafe(open)}`; }
          }
          openPositions.forEach(p => {
            const row = body.querySelector(`tr[data-ux-position-id="${p.id}"]`);
            if (!row) return;
            const px = priceFor(p.instrument_token);
            const pp = pnlFor(p.id);
            if (px != null) row.querySelector('.ux-live-price').textContent = fmtNum(px);
            if (pp != null) {
              const cell = row.querySelector('.ux-live-pnl');
              cell.textContent = asMoney(pp); cell.className = `ux-live-pnl ${pnlClassSafe(pp)}`;
            }
          });
        });
      }
      this.enhanceTablesSoon();
    } catch (e) {
      body.innerHTML = emptyHtml(`Could not load the deployment overview — ${escapeHtml(e.message || String(e))}`);
    }
  };

  UXV2.detailPositionsTable = function detailPositionsTable(rows) {
    // `data-ux-col-key` on every header/footer cell (see
    // tfootCellsByKey's own comment) -- one real <td> per column in
    // the tfoot, no colspan, so this table's own column drag/resize/
    // hide (enhanceTable, wired generically to every table in
    // #detailBody) can never misalign the total the way a colspan
    // spacer would once a column gets reordered.
    const cols = ['symbol', 'side', 'qty', 'avg', 'price', 'unrealized', 'opened'];
    const labels = { symbol: 'Symbol', side: 'Side', qty: 'Qty', avg: 'Avg', price: 'Price', unrealized: 'Unrealized', opened: 'Opened' };
    // Live-tick-worthy only while a live price actually exists for
    // EVERY leg -- otherwise "total unrealized" would silently claim a
    // number for legs it has no live price for at all (see the same
    // convention Dashboard's own Open Risk card and deployments.js's
    // Unrealized column total both already follow: never fabricate a
    // 0 for "no data", show — instead).
    const known = rows.filter(p => p.unrealized_pnl != null);
    const total = known.length ? known.reduce((s, p) => s + (p.unrealized_pnl || 0), 0) : null;
    return `<div class="table-wrap"><table>
      <thead><tr>${cols.map(k => `<th data-ux-col-key="${k}">${labels[k]}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(p => `<tr data-ux-position-id="${p.id}"><td>${escapeHtml(p.symbol)}</td><td>${escapeHtml(p.side)}</td><td>${fmtNum(p.qty)}</td><td>${fmtNum(p.avg_entry_price)}</td><td class="ux-live-price">${p.current_price != null ? fmtNum(p.current_price) : '—'}</td><td class="ux-live-pnl ${pnlClassSafe(p.unrealized_pnl)}">${p.unrealized_pnl != null ? asMoney(p.unrealized_pnl) : '—'}</td><td>${fmtDateTime(p.opened_at)}</td></tr>`).join('')}</tbody>
      <tfoot><tr class="positions-total-row">
        <td data-ux-col-key="symbol"><b>Total</b></td>
        <td data-ux-col-key="side"></td>
        <td data-ux-col-key="qty"></td>
        <td data-ux-col-key="avg"></td>
        <td data-ux-col-key="price"></td>
        <td class="live-pnl-total${total != null ? ' ' + pnlClassSafe(total) : ''}" data-ux-col-key="unrealized">${total != null ? asMoney(total) : '—'}</td>
        <td data-ux-col-key="opened"></td>
      </tr></tfoot>
    </table></div>`;
  };

  UXV2.openOverviewTrade = function openOverviewTrade(index) {
    const lot = this.overviewTrades?.[index];
    if (!lot) return;
    const meta = Detail._tradeMetaHtml ? Detail._tradeMetaHtml(lot) : renderJsonBlock('metadata', lot.metadata || {});
    this.openDrawer(`${lot.action || 'Execution'} · ${lot.symbol}`, meta,
      `${fmtDateTime(lot.executed_at)} · ${lot.reason || 'No reason recorded'}`);
  };

  UXV2.computeDrawdownDetails = function computeDrawdownDetails(points, initialCapital) {
    // Keep the platform's existing drawdown definition: permanent/settled
    // capital decline, not a temporary paper dip on a still-open positional
    // leg. This mirrors api.js's computeMaxDrawdown exactly, but also keeps
    // the peak/trough dates needed by the yearly matrix.
    const sorted = points.slice().sort((a, b) => new Date(a.snapshot_at) - new Date(b.snapshot_at));
    if (sorted.length < 2) return null;
    const equity = p => Number(initialCapital || 0) + Number(p.realized_pnl_cumulative || 0);
    let peak = sorted[0];
    let peakValue = equity(peak);
    let best = { abs: 0, pct: 0, peak_at: peak.snapshot_at, trough_at: peak.snapshot_at, days: 0 };
    for (const p of sorted) {
      const value = equity(p);
      if (value > peakValue) { peak = p; peakValue = value; }
      const abs = peakValue - value;
      const pct = peakValue ? abs / peakValue * 100 : 0;
      if (abs > best.abs) {
        best = {
          abs, pct, peak_at: peak.snapshot_at, trough_at: p.snapshot_at,
          days: Math.max(0, Math.round((new Date(p.snapshot_at) - new Date(peak.snapshot_at)) / 86_400_000)),
        };
      }
    }
    return best;
  };

  UXV2.renderPerformanceMatrix = function renderPerformanceMatrix(container, monthlyRows, snapshots, dep, mode = 'absolute') {
    if (!container) return;
    const rowsByYear = new Map();
    monthlyRows.forEach(r => {
      const d = new Date(r.period_start);
      const year = Number(new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Kolkata', year: 'numeric' }).format(d));
      const month = Number(new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Kolkata', month: 'numeric' }).format(d)) - 1;
      if (!rowsByYear.has(year)) rowsByYear.set(year, Array(12).fill(null));
      rowsByYear.get(year)[month] = r;
    });
    const years = [...rowsByYear.keys()].sort((a, b) => a - b);
    if (!years.length) {
      container.innerHTML = '<div class="empty">Monthly performance appears once this deployment has settled history.</div>';
      return;
    }
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const currentMonth = istMonthKey(new Date());
    const nowYear = Number(currentMonth.slice(0, 4));
    const nowMonth = Number(currentMonth.slice(5, 7)) - 1;

    const formatValue = value => mode === 'percent'
      ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
      : asMoney(value);

    const bodyRows = years.map(year => {
      const monthRows = rowsByYear.get(year);
      const totalPnl = monthRows.reduce((s, r) => s + Number(r?.realized_pnl || 0), 0);
      const totalPct = dep.initial_capital ? totalPnl / dep.initial_capital * 100 : 0;
      const yearPoints = snapshots.filter(s => Number(istDateKey(s.snapshot_at).slice(0, 4)) === year);
      const dd = this.computeDrawdownDetails(yearPoints, dep.initial_capital);
      const ratio = dd?.pct > 0 ? totalPct / dd.pct : (totalPct > 0 && dd ? Infinity : null);
      const cells = monthRows.map((r, monthIndex) => {
        if (!r) {
          const future = year > nowYear || (year === nowYear && monthIndex > nowMonth);
          return `<td class="${future ? 'future' : 'zero'}">—</td>`;
        }
        const pnl = Number(r.realized_pnl || 0);
        const pct = dep.initial_capital ? pnl / dep.initial_capital * 100 : 0;
        const value = mode === 'percent' ? pct : pnl;
        const cls = value > 0 ? 'pos' : value < 0 ? 'neg' : 'zero';
        const current = year === nowYear && monthIndex === nowMonth ? ' ux-matrix-current-month' : '';
        const tooltip = `${months[monthIndex]} ${year}\nRealized: ${asMoney(pnl)}\nReturn: ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%\nPositions closed: ${r.positions_closed || 0}\nWins/Losses: ${r.wins || 0}/${r.losses || 0}`;
        return `<td class="ux-month-cell ${cls}${current}" title="${escapeHtml(tooltip)}" onclick="UXV2.openMatrixMonth(${year},${monthIndex})">${formatValue(value)}</td>`;
      }).join('');
      const totalValue = mode === 'percent' ? totalPct : totalPnl;
      return `<tr>
        <td>${year}</td>${cells}
        <td class="total ${pnlClassSafe(totalValue)}" onclick="UXV2.openMatrixYear(${year})" style="cursor:pointer;">${formatValue(totalValue)}</td>
        <td class="${dd?.abs ? 'neg' : ''}" title="${dd ? `${fmtDateTime(dd.peak_at)} → ${fmtDateTime(dd.trough_at)}` : ''}">${dd ? `${mode === 'percent' ? `-${dd.pct.toFixed(2)}%` : fmtMoney(-dd.abs)}` : '—'}</td>
        <td>${dd ? dd.days : '—'}</td>
        <td>${ratio == null ? '—' : ratio === Infinity ? '∞' : ratio.toFixed(2)}</td>
      </tr>`;
    }).join('');

    container.innerHTML = `
      <div class="ux-analytics-toolbar">
        <div><b>Monthly Performance</b><div class="card-sub">Settled monthly P&amp;L. Click any month to inspect the underlying history.</div></div>
        <div class="ux-segmented">
          <button class="${mode === 'absolute' ? 'active' : ''}" onclick="UXV2.setMatrixMode('absolute')">₹ Absolute</button>
          <button class="${mode === 'percent' ? 'active' : ''}" onclick="UXV2.setMatrixMode('percent')">% Return</button>
        </div>
      </div>
      <div class="ux-performance-matrix-wrap"><table class="ux-performance-matrix ux-no-enhance"><thead><tr><th>Year</th>${months.map(m => `<th>${m}</th>`).join('')}<th>Total</th><th>Max DD</th><th>MDD Days</th><th>Return / DD</th></tr></thead><tbody>${bodyRows}</tbody></table></div>
      <div class="ux-matrix-caption">Percentage view uses the deployment's fixed initial capital, matching the platform's existing total-return convention. Max drawdown uses settled/realized equity, matching the platform's permanent-capital-loss definition.</div>`;
  };

  UXV2.setMatrixMode = function setMatrixMode(mode) {
    this.matrixMode = mode;
    this.renderPerformanceMatrix(document.getElementById('uxMonthlyPerformance'), this.matrixRows || [], this.matrixSnapshots || [], Detail._dep, mode);
  };

  UXV2.openMatrixMonth = function openMatrixMonth(year, monthIndex) {
    this.detailHistoryRange = {
      start: startOfMonthIso(year, monthIndex),
      end: endOfMonthIso(year, monthIndex),
      label: `${new Date(Date.UTC(year, monthIndex, 1)).toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' })}`,
    };
    this.detailHistoryMode = 'positions';
    window.location.hash = `#/deployments/${Detail._id}/history`;
  };

  UXV2.openMatrixYear = function openMatrixYear(year) {
    this.detailHistoryRange = { start: `${year}-01-01T00:00:00+05:30`, end: `${year}-12-31T23:59:59+05:30`, label: String(year) };
    this.detailHistoryMode = 'positions';
    window.location.hash = `#/deployments/${Detail._id}/history`;
  };

  UXV2.renderPnlDistribution = function renderPnlDistribution(units) {
    const vals = units.filter(u => u.status === 'closed').map(u => Number(u.realized_pnl || 0));
    if (vals.length < 2) return '';
    const min = Math.min(...vals), max = Math.max(...vals);
    const bins = Math.min(9, Math.max(5, Math.ceil(Math.sqrt(vals.length))));
    const span = (max - min) || 1;
    const width = span / bins;
    const buckets = Array.from({ length: bins }, (_, i) => ({ lo: min + i * width, hi: i === bins - 1 ? max : min + (i + 1) * width, count: 0 }));
    vals.forEach(v => {
      const idx = Math.min(bins - 1, Math.floor((v - min) / width));
      buckets[Math.max(0, idx)].count++;
    });
    const maxCount = Math.max(...buckets.map(b => b.count), 1);
    const sorted = vals.slice().sort((a, b) => a - b);
    const median = sorted.length % 2 ? sorted[(sorted.length - 1) / 2] : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    return `<section class="ux-section">
      <div class="ux-section-head"><h2>P&amp;L distribution</h2><span class="card-sub">Per strategic position / cycle</span></div>
      <div class="ux-bar-list">${buckets.map(b => `<div class="ux-bar-row"><span>${fmtMoney(b.lo)} → ${fmtMoney(b.hi)}</span><div class="ux-bar-track"><div class="ux-bar-fill ${b.hi <= 0 ? 'neg' : ''}" style="width:${b.count / maxCount * 100}%"></div></div><b>${b.count}</b></div>`).join('')}</div>
      <div class="card-meta" style="margin-top:10px;"><span>Median <b class="${pnlClassSafe(median)}">${asMoney(median)}</b></span><span>Average <b class="${pnlClassSafe(avg)}">${asMoney(avg)}</b></span><span>Best <b class="pos">${asMoney(max)}</b></span><span>Worst <b class="neg">${asMoney(min)}</b></span></div>
    </section>`;
  };

  UXV2.filterEquitySnapshots = function filterEquitySnapshots(snapshots, range) {
    if (!snapshots?.length || range === 'all') return snapshots || [];
    const sorted = snapshots.slice().sort((a, b) => new Date(a.snapshot_at) - new Date(b.snapshot_at));
    const end = new Date(sorted[sorted.length - 1].snapshot_at);
    let start;
    if (range === 'ytd') {
      const year = Number(new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Kolkata', year: 'numeric' }).format(end));
      start = new Date(`${year}-01-01T00:00:00+05:30`);
    } else {
      const days = range === '1m' ? 31 : range === '3m' ? 93 : range === '6m' ? 186 : 365;
      start = new Date(end.getTime() - days * 86_400_000);
    }
    const filtered = sorted.filter(p => new Date(p.snapshot_at) >= start);
    // A chart with a single visible point is less useful than including the
    // immediately preceding baseline; preserve it when available.
    if (filtered.length && filtered[0] !== sorted[0]) {
      const idx = sorted.indexOf(filtered[0]);
      return [sorted[Math.max(0, idx - 1)], ...filtered];
    }
    return filtered;
  };

  UXV2.renderEnhancedEquity = function renderEnhancedEquity(container) {
    if (!container) return;
    const snapshots = this.filterEquitySnapshots(this.equitySnapshots || [], this.equityRange || 'all');
    if (snapshots.length < 2) {
      container.innerHTML = emptyHtml('Not enough equity history in this range yet.');
      return;
    }
    const mode = this.equityMode || 'absolute';
    const base = Number(snapshots[0].total_value || 0);
    const points = snapshots.map(s => ({
      ...s,
      plot: mode === 'percent' ? (base ? (Number(s.total_value) - base) / base * 100 : 0) : Number(s.total_value),
      pct: base ? (Number(s.total_value) - base) / base * 100 : 0,
    }));
    const values = points.map(p => p.plot);
    const min = Math.min(...values), max = Math.max(...values);
    const span = (max - min) || 1;
    const W = 1000, H = 230, PAD = 14;
    const coords = points.map((p, i) => {
      const x = PAD + i / Math.max(1, points.length - 1) * (W - 2 * PAD);
      const y = H - PAD - (p.plot - min) / span * (H - 2 * PAD);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' ');
    const lineClass = values[values.length - 1] >= values[0] ? 'gain' : 'loss';

    // Settled drawdown series: deliberately based on initial capital +
    // realized cumulative, matching computeMaxDrawdown and the matrix.
    let peak = Number(Detail._dep.initial_capital || 0) + Number(points[0].realized_pnl_cumulative || 0);
    const dd = points.map(p => {
      const v = Number(Detail._dep.initial_capital || 0) + Number(p.realized_pnl_cumulative || 0);
      peak = Math.max(peak, v);
      return peak ? -((peak - v) / peak) * 100 : 0;
    });
    const ddMin = Math.min(...dd, 0), ddSpan = Math.abs(ddMin) || 1;
    const ddCoords = dd.map((v, i) => {
      const x = PAD + i / Math.max(1, dd.length - 1) * (W - 2 * PAD);
      const y = 8 + (Math.abs(v) / ddSpan) * 54;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' ');

    const fmtAxis = v => mode === 'percent' ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` : fmtMoney(v);
    container.innerHTML = `
      <div class="ux-analytics-toolbar">
        <div><b>Equity &amp; drawdown</b><div class="card-sub">Hover/touch for exact values. Drawdown is settled capital loss, not open-position noise.</div></div>
        <div class="ux-equity-controls">
          <div class="ux-segmented">${['1m','3m','6m','ytd','1y','all'].map(r => `<button class="${(this.equityRange || 'all') === r ? 'active' : ''}" onclick="UXV2.setEquityRange('${r}')">${r === 'all' ? 'ALL' : r.toUpperCase()}</button>`).join('')}</div>
          <div class="ux-segmented"><button class="${mode === 'absolute' ? 'active' : ''}" onclick="UXV2.setEquityMode('absolute')">₹</button><button class="${mode === 'percent' ? 'active' : ''}" onclick="UXV2.setEquityMode('percent')">%</button></div>
        </div>
      </div>
      <div class="ux-equity-shell">
        <div class="ux-equity-y"><span>${fmtAxis(max)}</span><span>${fmtAxis((max + min) / 2)}</span><span>${fmtAxis(min)}</span></div>
        <div class="ux-equity-main" id="uxEquityPointerArea">
          <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="Equity curve"><polyline class="ux-equity-line ${lineClass}" points="${coords}" vector-effect="non-scaling-stroke"></polyline></svg>
          <div class="ux-equity-crosshair" id="uxEquityCrosshair"></div>
        </div>
      </div>
      <div class="ux-equity-x"><span>${fmtDate(points[0].snapshot_at)}</span><span>${fmtDate(points[points.length - 1].snapshot_at)}</span></div>
      <div class="ux-drawdown-strip"><div class="ux-drawdown-label"><b>Drawdown</b><span>${ddMin.toFixed(2)}%</span></div><div class="ux-drawdown-area"><svg viewBox="0 0 ${W} 70" preserveAspectRatio="none"><polyline points="${ddCoords}" vector-effect="non-scaling-stroke"></polyline></svg></div></div>`;

    const area = container.querySelector('#uxEquityPointerArea');
    const cross = container.querySelector('#uxEquityCrosshair');
    if (area) {
      const show = (clientX, clientY) => {
        const rect = area.getBoundingClientRect();
        const frac = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)));
        const idx = Math.max(0, Math.min(points.length - 1, Math.round(frac * (points.length - 1))));
        const p = points[idx];
        if (cross) { cross.style.display = 'block'; cross.style.left = `${frac * 100}%`; }
        if (typeof ChartTooltip !== 'undefined') {
          ChartTooltip.show(clientX, clientY, `<b>${fmtDate(p.snapshot_at)}</b><br>Equity ${fmtMoney(p.total_value)}<br>Range return ${p.pct >= 0 ? '+' : ''}${p.pct.toFixed(2)}%<br>Settled P&amp;L ${asMoney(p.realized_pnl_cumulative)}<br>Drawdown ${dd[idx].toFixed(2)}%`);
        }
      };
      area.addEventListener('pointermove', e => show(e.clientX, e.clientY));
      area.addEventListener('pointerleave', () => { if (cross) cross.style.display = 'none'; if (typeof ChartTooltip !== 'undefined') ChartTooltip.hide(); });
      area.addEventListener('touchmove', e => { const t = e.touches?.[0]; if (t) show(t.clientX, t.clientY); }, { passive: true });
    }
  };

  UXV2.setEquityMode = function setEquityMode(mode) {
    this.equityMode = mode;
    this.renderEnhancedEquity(document.getElementById('uxEnhancedEquity'));
  };
  UXV2.setEquityRange = function setEquityRange(range) {
    this.equityRange = range;
    this.renderEnhancedEquity(document.getElementById('uxEnhancedEquity'));
  };

  UXV2.renderExitReasonBars = function renderExitReasonBars() {
    const units = typeof groupPositionsIntoUnits === 'function' ? groupPositionsIntoUnits(Detail._statsAllPositions || [], 'position') : [];
    const lotsByPosition = Detail._statsLotsByPosition || {};
    const byReason = {};
    units.filter(u => u.status === 'closed').forEach(u => {
      const lots = u.position_ids.flatMap(id => lotsByPosition[id] || []).slice().sort((a, b) => new Date(a.executed_at) - new Date(b.executed_at));
      const reason = lots[lots.length - 1]?.reason || '(no reason recorded)';
      const entry = byReason[reason] ||= { pnl: 0, count: 0 };
      entry.pnl += Number(u.realized_pnl || 0); entry.count++;
    });
    const rows = Object.entries(byReason).sort((a, b) => Math.abs(b[1].pnl) - Math.abs(a[1].pnl));
    if (!rows.length) return '';
    const maxAbs = Math.max(...rows.map(([, v]) => Math.abs(v.pnl)), 1);
    return `<section class="ux-section"><div class="ux-section-head"><div><h2>P&amp;L contribution by exit</h2><div class="card-sub">Which exit trigger actually contributes or destroys P&amp;L. The detailed table remains below.</div></div></div><div class="ux-bar-list ux-exit-bars">${rows.map(([reason, v]) => `<div class="ux-bar-row"><span title="${escapeHtml(reason)}">${escapeHtml(reason)}</span><div class="ux-bar-track"><div class="ux-bar-fill ${v.pnl < 0 ? 'neg' : ''}" style="width:${Math.abs(v.pnl) / maxAbs * 100}%"></div></div><b class="${pnlClassSafe(v.pnl)}">${asMoney(v.pnl)} <small>· ${v.count}</small></b></div>`).join('')}</div></section>`;
  };

  UXV2.renderDetailAnalytics = async function renderDetailAnalytics(baseRenderStats) {
    const body = document.getElementById('detailBody');
    body.classList.add('ux-analytics');
    await baseRenderStats.call(Detail);
    try {
      const [monthly, snaps] = await Promise.all([
        Api.getPnlDigestForDeployment(Detail._id, 'month', 180),
        Api.getSnapshots(Detail._id),
      ]);
      this.matrixRows = monthly;
      this.matrixSnapshots = snaps;
      this.matrixMode = this.matrixMode || 'absolute';
      this.equitySnapshots = snaps;
      this.equityMode = this.equityMode || 'absolute';
      this.equityRange = this.equityRange || 'all';
      const perf = document.getElementById('detailStatsPerf');
      const matrix = document.createElement('section');
      matrix.className = 'ux-section';
      matrix.innerHTML = '<div id="uxMonthlyPerformance"></div>';
      perf?.insertAdjacentElement('afterend', matrix);
      this.renderPerformanceMatrix(document.getElementById('uxMonthlyPerformance'), monthly, snaps, Detail._dep, this.matrixMode);

      const units = typeof groupPositionsIntoUnits === 'function' ? groupPositionsIntoUnits(Detail._statsAllPositions || [], 'position') : [];
      const distributionHtml = this.renderPnlDistribution(units);
      if (distributionHtml) matrix.insertAdjacentHTML('afterend', distributionHtml);
      const exitHtml = this.renderExitReasonBars();
      const distribution = matrix.nextElementSibling;
      if (exitHtml) (distribution || matrix).insertAdjacentHTML('afterend', exitHtml);

      // Replace only the old Equity Curve section's presentation; the
      // snapshots/data and all downstream Stats sections stay untouched.
      const equitySection = [...body.querySelectorAll('section')].find(sec => sec.querySelector('h2')?.textContent.trim() === 'Equity Curve');
      if (equitySection) {
        equitySection.innerHTML = '<div id="uxEnhancedEquity"></div>';
        this.renderEnhancedEquity(document.getElementById('uxEnhancedEquity'));
      }
      this.enhanceTablesSoon();
    } catch (e) {
      console.warn('Monthly performance enhancement failed', e);
    }
  };

  UXV2.renderDetailHistory = async function renderDetailHistory() {
    window.LivePnl?.untrack(this.detailLiveHandler);
    this.detailLiveHandler = null;
    const body = document.getElementById('detailBody');
    body.innerHTML = spinnerHtml();
    try {
      const [positions, trades, events] = await Promise.all([
        Api.getPositions(Detail._id, 'all'), Api.getTrades(Detail._id, 2000), Api.getEvents(Detail._id, 1000),
      ]);
      this.historyTrades = trades.lots || [];
      this.historyEvents = events || [];
      this.historyPositions = positions || [];
      this.paintHistory();
    } catch (e) {
      body.innerHTML = emptyHtml(`Could not load history — ${escapeHtml(e.message || String(e))}`);
    }
  };

  UXV2.paintHistory = function paintHistory() {
    const body = document.getElementById('detailBody');
    const range = this.detailHistoryRange;
    body.innerHTML = `
      <div class="ux-history-toolbar">
        <div class="ux-segmented">
          <button class="${this.detailHistoryMode === 'positions' ? 'active' : ''}" onclick="UXV2.setHistoryMode('positions')">Positions / Cycles</button>
          <button class="${this.detailHistoryMode === 'executions' ? 'active' : ''}" onclick="UXV2.setHistoryMode('executions')">Executions</button>
          <button class="${this.detailHistoryMode === 'events' ? 'active' : ''}" onclick="UXV2.setHistoryMode('events')">Events</button>
        </div>
        <div>${range ? `<span class="ux-history-filter-chip">${escapeHtml(range.label)} <button class="btn btn-secondary btn-sm" style="padding:1px 5px;" onclick="UXV2.clearHistoryRange()">✕</button></span>` : '<span class="card-sub">All history</span>'}</div>
      </div>
      <div id="uxHistoryContent"></div>`;
    const content = document.getElementById('uxHistoryContent');
    if (this.detailHistoryMode === 'executions') this.paintHistoryExecutions(content);
    else if (this.detailHistoryMode === 'events') this.paintHistoryEvents(content);
    else this.paintHistoryPositions(content);
    this.enhanceTablesSoon();
  };

  UXV2.setHistoryMode = function setHistoryMode(mode) {
    this.detailHistoryMode = mode;
    this.paintHistory();
  };
  UXV2.clearHistoryRange = function clearHistoryRange() { this.detailHistoryRange = null; this.paintHistory(); };

  UXV2.paintHistoryPositions = function paintHistoryPositions(content) {
    let units = typeof groupPositionsIntoUnits === 'function' ? groupPositionsIntoUnits(this.historyPositions || [], 'position') : [];
    units = units.filter(u => {
      if (!this.detailHistoryRange) return true;
      const rangeStart = new Date(this.detailHistoryRange.start).getTime();
      const rangeEnd = new Date(this.detailHistoryRange.end).getTime();
      const opened = new Date(u.opened_at).getTime();
      const closed = u.closed_at ? new Date(u.closed_at).getTime() : Date.now();
      // A cycle belongs to the selected period whenever its lifetime
      // overlaps the period, even if it opened before the first day.
      return opened <= rangeEnd && closed >= rangeStart;
    }).slice().sort((a, b) => new Date(b.closed_at || b.opened_at) - new Date(a.closed_at || a.opened_at));
    if (!units.length) { content.innerHTML = emptyHtml('No positions/cycles in this period.'); return; }
    const byId = new Map((this.historyPositions || []).map(p => [p.id, p]));
    content.innerHTML = units.map((u, i) => {
      const ps = (u.position_ids || []).map(id => byId.get(id)).filter(Boolean);
      const unrealized = ps.filter(p => p.status === 'open').reduce((s, p) => s + Number(p.unrealized_pnl || 0), 0);
      const total = Number(u.realized_pnl || 0) + unrealized;
      return `<div class="ux-cycle-card" id="uxCycle-${i}">
        <div class="ux-cycle-head" onclick="document.getElementById('uxCycle-${i}').classList.toggle('open')">
          <div><b>${Detail._dep.mode === 'positional' ? 'Cycle' : 'Position'} · ${fmtDateTime(u.opened_at)}</b><div class="card-sub">${u.status === 'open' ? 'Open' : `Closed ${fmtDateTime(u.closed_at)}`} · ${ps.length} leg${ps.length === 1 ? '' : 's'}</div></div>
          <span class="tag tag-${u.status === 'open' ? 'active' : 'stopped'}">${u.status}</span>
          <b class="${pnlClassSafe(total)}">${asMoney(total)}</b>
        </div>
        <div class="ux-cycle-body"><div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Realized</th><th>Status</th></tr></thead><tbody>${ps.map(p => `<tr><td>${escapeHtml(p.symbol)}</td><td>${escapeHtml(p.side)}</td><td>${fmtNum(p.qty)}</td><td>${fmtNum(p.avg_entry_price)}</td><td class="${pnlClassSafe(p.realized_pnl)}">${asMoney(p.realized_pnl)}</td><td>${escapeHtml(p.status)}</td></tr>`).join('')}</tbody></table></div></div>
      </div>`;
    }).join('');
  };

  UXV2.paintHistoryExecutions = function paintHistoryExecutions(content) {
    const rows = this.historyTrades.filter(t => dateRangeContains(t.executed_at, this.detailHistoryRange));
    if (!rows.length) { content.innerHTML = emptyHtml('No executions in this period.'); return; }
    content.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Action</th><th>Symbol</th><th>Qty</th><th>Price</th><th>Reason</th></tr></thead><tbody>${rows.slice(0, 1000).map((t, i) => `<tr class="ux-row-navigate" onclick="UXV2.openHistoryExecution(${this.historyTrades.indexOf(t)})"><td>${fmtDateTime(t.executed_at)}</td><td>${escapeHtml(t.action)}</td><td>${escapeHtml(t.symbol)}</td><td>${fmtNum(t.qty)}</td><td>${fmtNum(t.price)}</td><td>${escapeHtml(t.reason || '')}${triggerBadgeHtml(t.reason)}</td></tr>`).join('')}</tbody></table></div>`;
  };

  UXV2.paintHistoryEvents = function paintHistoryEvents(content) {
    const rows = this.historyEvents.filter(e => dateRangeContains(e.created_at, this.detailHistoryRange));
    if (!rows.length) { content.innerHTML = emptyHtml('No events in this period.'); return; }
    content.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Event</th><th>Message</th></tr></thead><tbody>${rows.slice(0, 1000).map((e, i) => `<tr class="${e.metadata && Object.keys(e.metadata).length ? 'ux-row-navigate' : ''}" ${e.metadata && Object.keys(e.metadata).length ? `onclick="UXV2.openHistoryEvent(${this.historyEvents.indexOf(e)})"` : ''}><td>${fmtDateTime(e.created_at)}</td><td><span class="tag ${e.event_type === 'strategy_error' ? 'tag-error' : 'tag-info'}">${escapeHtml(e.event_type)}</span></td><td>${escapeHtml(e.message || '')}</td></tr>`).join('')}</tbody></table></div>`;
  };

  UXV2.openHistoryExecution = function openHistoryExecution(index) {
    const lot = this.historyTrades[index];
    if (!lot) return;
    const meta = Detail._tradeMetaHtml ? Detail._tradeMetaHtml(lot) : renderJsonBlock('metadata', lot.metadata || {});
    this.openDrawer(`${lot.action} · ${lot.symbol}`, meta, `${fmtDateTime(lot.executed_at)} · ${lot.reason || 'No reason recorded'}`);
  };
  UXV2.openHistoryEvent = function openHistoryEvent(index) {
    const ev = this.historyEvents[index];
    if (!ev) return;
    this.openDrawer(ev.event_type.replace(/_/g, ' '), renderJsonBlock('metadata', ev.metadata || {}), `${fmtDateTime(ev.created_at)} · ${ev.message || ''}`);
  };

  UXV2.configGroup = function configGroup(key) {
    const k = key.toLowerCase();
    if (/capital|qty|quantity|lot|size|allocation/.test(k)) return 'Position sizing';
    if (/adjust|roll|delta|hedge|rebalance/.test(k)) return 'Adjustments';
    if (/stop|loss|target|profit|exit|square|trail|max_/.test(k)) return 'Exit & risk';
    if (/time|entry|start|open|weekday|day|expiry/.test(k)) return 'Entry & timing';
    return 'Strategy';
  };

  UXV2.renderDetailConfiguration = function renderDetailConfiguration() {
    const dep = Detail._dep;
    const body = document.getElementById('detailBody');
    const cfg = dep.config || {};
    const groups = {};
    Object.keys(cfg).sort().forEach(k => { (groups[this.configGroup(k)] ||= []).push([k, cfg[k]]); });
    body.innerHTML = `
      <section class="ux-section">
        <div class="ux-section-head"><h2>Deployment details</h2><button class="btn btn-secondary btn-sm" onclick="Detail.openEditModal()">Edit details</button></div>
        <div class="ux-config-grid">
          <div class="ux-config-group"><h3>Identity</h3><div class="ux-config-kv">
            <div class="ux-config-key">Name</div><div class="ux-config-value">${escapeHtml(dep.deployment_name)}</div>
            <div class="ux-config-key">Strategy</div><div class="ux-config-value">${escapeHtml(dep.strategy_name)}</div>
            <div class="ux-config-key">Mode</div><div class="ux-config-value">${escapeHtml(dep.mode)}</div>
            <div class="ux-config-key">Initial capital</div><div class="ux-config-value">${fmtMoney(dep.initial_capital)}</div>
          </div></div>
          <div class="ux-config-group"><h3>Behavior</h3><div class="ux-config-kv">
            <div class="ux-config-key">Analytics</div><div class="ux-config-value">${dep.include_in_reports ? 'Included' : 'Excluded'}</div>
            <div class="ux-config-key">Notifications</div><div class="ux-config-value">${dep.notifications_enabled ? 'On' : 'Off'}</div>
            <div class="ux-config-key">Tags</div><div class="ux-config-value">${(dep.tags || []).map(escapeHtml).join(', ') || '—'}</div>
            <div class="ux-config-key">Status</div><div class="ux-config-value">${escapeHtml(dep.status)}</div>
          </div></div>
        </div>
      </section>
      <section class="ux-section">
        <div class="ux-section-head"><div><h2>Strategy parameters</h2><div class="card-sub">Read-only while running. Pause first so the next Resume reconstructs the strategy with the new config.</div></div>
          ${dep.status === 'paused' ? '<button class="btn btn-primary btn-sm" onclick="Detail.openEditConfigModal()">Edit configuration</button>' : dep.status === 'active' ? '<button class="btn btn-secondary btn-sm" onclick="UXV2.pauseAndEditConfig()">Pause & edit</button>' : ''}
        </div>
        <div class="ux-config-grid">${Object.entries(groups).map(([name, entries]) => `<div class="ux-config-group"><h3>${escapeHtml(name)}</h3><div class="ux-config-kv">${entries.map(([k, v]) => `<div class="ux-config-key">${escapeHtml(k)}</div><div class="ux-config-value">${typeof formatConfigValue === 'function' ? formatConfigValue(v) : escapeHtml(JSON.stringify(v))}</div>`).join('')}</div></div>`).join('')}</div>
      </section>
      <details class="ux-section"><summary style="cursor:pointer;font-weight:800;">Advanced · raw JSON</summary><div style="margin-top:10px;">${renderJsonBlock('config', cfg)}</div></details>`;
  };

  UXV2.pauseAndEditConfig = async function pauseAndEditConfig() {
    if (!confirm(`Pause "${Detail._dep.deployment_name}" so its configuration can be edited? Open positions remain open while paused.`)) return;
    const r = await Api.pauseDeployment(Detail._id);
    if (!r.ok) { alert('Could not pause deployment.'); return; }
    await Detail.load(Detail._id);
    Detail.openEditConfigModal();
  };

  // Group the existing automatic deploy/edit config fields without
  // changing any IDs or form-reading logic.
  UXV2.groupConfigFields = function groupConfigFields(container) {
    if (!container || container.querySelector(':scope > .ux-form-group')) return;
    const fields = [...container.children].filter(el => el.classList.contains('field'));
    if (fields.length < 4) return;
    const notes = [...container.children].filter(el => !fields.includes(el));
    const groups = new Map();
    fields.forEach(field => {
      const label = field.querySelector('label')?.textContent.trim() || 'Strategy';
      const name = this.configGroup(label);
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(field);
    });
    container.innerHTML = '';
    groups.forEach((items, name) => {
      const wrapper = document.createElement('div');
      wrapper.className = 'ux-form-group';
      wrapper.innerHTML = `<div class="ux-form-group-title">${escapeHtml(name)}</div><div class="ux-form-group-fields"></div>`;
      const target = wrapper.querySelector('.ux-form-group-fields');
      items.forEach(f => target.appendChild(f));
      container.appendChild(wrapper);
    });
    notes.forEach(n => container.appendChild(n));
  };

  // Reports: lead with a visual contribution read, keep the detailed
  // table immediately below for exact values/export-minded scanning.
  UXV2.enhanceReportContribution = async function enhanceReportContribution() {
    const target = document.getElementById('reportsByStrategy');
    if (!target) return;
    try {
      const report = await Api.getPnlReport(Reports._period, Reports._offset);
      const rows = (report.by_strategy || []).slice().sort((a, b) => Math.abs(Number(b.realized_pnl || 0)) - Math.abs(Number(a.realized_pnl || 0)));
      if (!rows.length) return;
      const maxAbs = Math.max(...rows.map(r => Math.abs(Number(r.realized_pnl || 0))), 1);
      const visual = `<div class="ux-report-contrib"><div class="ux-card-head"><strong>P&amp;L contribution</strong><span class="card-sub">Visual first; exact table below</span></div><div class="ux-bar-list">${rows.map(r => `<div class="ux-bar-row"><span>${escapeHtml(r.strategy_name)}</span><div class="ux-bar-track"><div class="ux-bar-fill ${Number(r.realized_pnl || 0) < 0 ? 'neg' : ''}" style="width:${Math.abs(Number(r.realized_pnl || 0)) / maxAbs * 100}%"></div></div><b class="${pnlClassSafe(r.realized_pnl)}">${asMoney(r.realized_pnl)}</b></div>`).join('')}</div></div>`;
      target.insertAdjacentHTML('afterbegin', visual);
    } catch (e) { console.warn('UXV2 report contribution chart failed', e); }
  };

  // Instruments: the backend search remains exactly the same; these are
  // instant client-side facets over the already-returned rows.
  UXV2.enhanceInstrumentResults = function enhanceInstrumentResults() {
    const root = document.getElementById('instSearchResults');
    const table = root?.querySelector('table');
    if (!root || !table || root.querySelector('.ux-instrument-filters')) return;
    const rows = [...(table.tBodies?.[0]?.rows || [])];
    if (!rows.length) return;
    const exchanges = [...new Set(rows.map(r => (r.cells[2]?.textContent || '').split('·')[0].trim()).filter(Boolean))].sort();
    const types = [...new Set(rows.map(r => (r.cells[3]?.textContent || '').trim()).filter(Boolean))].sort();
    const bar = document.createElement('div');
    bar.className = 'ux-instrument-filters';
    bar.innerHTML = `<select id="uxInstExchange"><option value="">All exchanges</option>${exchanges.map(x => `<option>${escapeHtml(x)}</option>`).join('')}</select><select id="uxInstType"><option value="">All types</option>${types.map(x => `<option>${escapeHtml(x)}</option>`).join('')}</select><label class="checkbox-row" style="margin:0;"><input type="checkbox" id="uxInstAvailable" style="width:auto;"> Available only</label><span class="card-sub" id="uxInstCount"></span>`;
    table.closest('.table-wrap')?.insertAdjacentElement('beforebegin', bar);
    const apply = () => {
      const ex = document.getElementById('uxInstExchange')?.value || '';
      const type = document.getElementById('uxInstType')?.value || '';
      const available = document.getElementById('uxInstAvailable')?.checked;
      let shown = 0;
      rows.forEach(r => {
        const rowEx = (r.cells[2]?.textContent || '').split('·')[0].trim();
        const rowType = (r.cells[3]?.textContent || '').trim();
        const subscribed = /subscribed/i.test(r.cells[r.cells.length - 1]?.textContent || '');
        const show = (!ex || rowEx === ex) && (!type || rowType === type) && (!available || !subscribed);
        r.style.display = show ? '' : 'none'; if (show) shown++;
      });
      const count = document.getElementById('uxInstCount'); if (count) count.textContent = `${shown} shown`;
    };
    bar.querySelectorAll('select,input').forEach(el => el.addEventListener('change', apply));
    apply();
  };

  // ------------------------------------------------------------------
  // Compare common-history/rolling period support
  // ------------------------------------------------------------------
  UXV2.compareStart = function compareStart(rows, period) {
    if (!rows.length || period === 'all') return null;
    const now = new Date();
    if (period === '30d') return new Date(now.getTime() - 30 * 86_400_000);
    if (period === '90d') return new Date(now.getTime() - 90 * 86_400_000);
    if (period === 'ytd') return new Date(`${new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Kolkata', year: 'numeric' }).format(now)}-01-01T00:00:00+05:30`);
    // common history: latest first snapshot among selected deployments.
    const starts = rows.map(r => r.points?.[0]?.snapshot_at).filter(Boolean).map(x => new Date(x));
    return starts.length ? new Date(Math.max(...starts.map(d => d.getTime()))) : null;
  };

  UXV2.deriveCompareRow = function deriveCompareRow(row, start) {
    if (!start) return row;
    const pointsRaw = row.points.filter(p => new Date(p.snapshot_at) >= start);
    if (!pointsRaw.length) return { ...row, points: [], units: [], stats: computeUnitStats([]), drawdown: null, returnToDrawdown: null };
    const base = Number(pointsRaw[0].total_value || 0);
    const points = pointsRaw.map(p => ({ ...p, pct: base ? (Number(p.total_value) - base) / base * 100 : 0 }));
    const units = row.units.filter(u => u.status === 'closed' && u.closed_at && new Date(u.closed_at) >= start);
    const stats = typeof computeUnitStats === 'function' ? computeUnitStats(units) : row.stats;
    const drawdown = typeof computeMaxDrawdown === 'function' ? computeMaxDrawdown(pointsRaw, row.deployment.initial_capital) : null;
    const last = points[points.length - 1];
    let ratio = null;
    if (last && drawdown?.pct > 0) ratio = last.pct / drawdown.pct;
    else if (last?.pct > 0 && drawdown?.pct === 0) ratio = Infinity;
    return { ...row, points, units, stats, drawdown, returnToDrawdown: ratio };
  };

  UXV2.compactCompareMetrics = function compactCompareMetrics(root) {
    if (!root) return;
    const tables = [...root.querySelectorAll('table')];
    const table = tables.find(t => [...(t.tBodies?.[0]?.rows || [])].some(r => /max drawdown/i.test(r.cells[0]?.textContent || '')));
    if (!table || table.dataset.uxCompareCompact) return;
    table.dataset.uxCompareCompact = '1';
    const tbody = table.tBodies[0];
    const rows = [...tbody.rows];
    const primaryOrder = [
      'Return (since tracked)', 'Return on Capital', 'Max drawdown', 'Return / Drawdown',
      'Win rate', 'Profit factor', 'Avg P&L per Position',
    ];
    const norm = x => String(x || '').trim().toLowerCase();
    const primary = [];
    primaryOrder.forEach(label => {
      const row = rows.find(r => norm(r.cells[0]?.textContent) === norm(label));
      if (row) primary.push(row);
    });
    const secondary = rows.filter(r => !primary.includes(r));
    primary.forEach(r => tbody.appendChild(r));
    secondary.forEach(r => { r.classList.add('ux-compare-secondary'); tbody.appendChild(r); });
    if (!secondary.length) return;
    const toolbar = document.createElement('div');
    toolbar.className = 'ux-compare-metric-toggle';
    toolbar.innerHTML = `<button class="btn btn-secondary btn-sm">Show all metrics · ${rows.length}</button><span class="card-sub">Decision metrics first; context stays one click away.</span>`;
    toolbar.querySelector('button').addEventListener('click', e => {
      const expanded = table.classList.toggle('ux-compare-expanded');
      e.currentTarget.textContent = expanded ? 'Show decision metrics only' : `Show all metrics · ${rows.length}`;
    });
    table.closest('.table-wrap')?.insertAdjacentElement('beforebegin', toolbar);
  };

  UXV2.setComparePeriod = function setComparePeriod(period) {
    this.comparePeriod = period;
    Compare.renderComparisonSection();
  };

  UXV2.comparePeriodToolbar = function comparePeriodToolbar(start) {
    const startText = start ? `From ${fmtDate(start.toISOString())}` : 'Each deployment’s full available history';
    return `<div class="ux-analytics-toolbar" style="margin-bottom:12px;">
      <div><b>Comparison period</b><div class="card-sub">${escapeHtml(startText)}</div></div>
      <select onchange="UXV2.setComparePeriod(this.value)">
        <option value="common" ${this.comparePeriod === 'common' ? 'selected' : ''}>Common history</option>
        <option value="30d" ${this.comparePeriod === '30d' ? 'selected' : ''}>Last 30 days</option>
        <option value="90d" ${this.comparePeriod === '90d' ? 'selected' : ''}>Last 90 days</option>
        <option value="ytd" ${this.comparePeriod === 'ytd' ? 'selected' : ''}>Year to date</option>
        <option value="all" ${this.comparePeriod === 'all' ? 'selected' : ''}>All available</option>
      </select>
    </div>`;
  };

  UXV2.renameAnalyticsSemantics = function renameAnalyticsSemantics(root = document) {
    // The database/API field remains include_in_reports for compatibility,
    // but the product meaning is now clearer: it controls performance
    // analytics only. Operational risk/activity never disappears.
    root.querySelectorAll?.('.tag.tag-warn').forEach(tag => {
      if (/excluded from reports/i.test(tag.textContent || '')) {
        tag.textContent = 'excluded from analytics';
        tag.title = 'Excluded from performance analytics; live positions/risk stay visible.';
      }
    });
    const cb = root.querySelector?.('#editDeploymentIncludeInReports');
    if (cb) {
      const label = cb.closest('label') || cb.parentElement;
      if (label) [...label.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).forEach(n => {
        if (/report/i.test(n.textContent || '')) n.textContent = ' Include in performance analytics';
      });
    }
  };

  // ------------------------------------------------------------------
  // Settings layout
  // ------------------------------------------------------------------
  UXV2.setupSettings = function setupSettings() {
    const view = document.getElementById('view-account');
    const title = view?.querySelector('.view-header h1');
    if (title) title.textContent = 'Settings';
    const tabs = document.getElementById('accountTabs');
    const body = document.getElementById('accountBody');
    if (!view || !tabs || !body || view.querySelector('.ux-settings-shell')) return;
    const shell = document.createElement('div');
    shell.className = 'ux-settings-shell';
    tabs.parentNode.insertBefore(shell, tabs);
    tabs.classList.remove('tabs');
    tabs.classList.add('ux-settings-nav');
    shell.appendChild(tabs);
    shell.appendChild(body);

    const buttons = [...tabs.querySelectorAll('button')];
    const labelFor = b => b.dataset.tab;
    const groups = [
      ['PERSONAL', ['profile']],
      ['ADMINISTRATION', ['users', 'tags', 'admin', 'audit']],
    ];
    const byTab = Object.fromEntries(buttons.map(b => [labelFor(b), b]));
    groups.forEach(([name, names]) => {
      const label = document.createElement('div'); label.className = 'ux-settings-label'; label.textContent = name; tabs.appendChild(label);
      names.forEach(n => { if (byTab[n]) tabs.appendChild(byTab[n]); });
    });
  };

  // ------------------------------------------------------------------
  // Install module patches while all external static modules are loaded
  // but before index.html's inline router starts handling DOMContentLoaded.
  // ------------------------------------------------------------------
  UXV2.installModulePatches = function installModulePatches() {
    // DEPLOYMENTS -----------------------------------------------------
    this.installDeploymentColumns();
    if (typeof Deployments !== 'undefined' && !Deployments.__uxV2Patched) {
      Deployments.__uxV2Patched = true;
      const baseLoad = Deployments.load.bind(Deployments);
      const baseLoadCols = Deployments._loadColumnPrefs.bind(Deployments);
      Deployments._loadColumnPrefs = function () {
        let saved = null;
        try { saved = JSON.parse(localStorage.getItem(this._colPrefsKey) || 'null'); } catch (_) { saved = null; }
        if (Array.isArray(saved)) {
          this._visibleCols = new Set(saved);
          DEPLOY_COLUMNS.forEach(c => { if (c.always) this._visibleCols.add(c.key); });
        } else {
          const defaults = ['ux_select', 'name', 'strategy', 'status', 'mode', 'ux_current_pnl', 'ux_open_positions', 'ux_last_action', 'actions'];
          this._visibleCols = new Set(defaults.filter(k => DEPLOY_COLUMNS.some(c => c.key === k)));
        }
      };
      Deployments.load = async function (quiet = false) {
        await baseLoad(quiet);
        await UXV2.enrichDeployments(this._all);
        this.render();
        UXV2.restoreDeploymentListState();
        UXV2.ensureDeploymentQuickFilters();
        UXV2.updateSelectionTray();
        UXV2.enhanceTablesSoon();
      };
      const baseRender = Deployments.render.bind(Deployments);
      Deployments.render = function () {
        baseRender();
        requestAnimationFrame(() => {
          UXV2.ensureDeploymentQuickFilters();
          UXV2.renameAnalyticsSemantics(document.getElementById('view-deployments') || document);
          UXV2.enhanceTablesSoon();
        });
      };
      Deployments.stop = async function (id) {
        const dep = this._all.find(d => d.id === id);
        return UXV2.openStopDialog(id, dep?.deployment_name);
      };
      // Keep a reference so future code can opt back into canonical prefs
      Deployments.__uxV2BaseLoadColumnPrefs = baseLoadCols;
    }

    // DASHBOARD -------------------------------------------------------
    if (typeof Dashboard !== 'undefined' && !Dashboard.__uxV2Patched) {
      Dashboard.__uxV2Patched = true;
      const baseLoad = Dashboard.load.bind(Dashboard);
      Dashboard.load = async function (quiet = false) {
        await baseLoad(quiet);
        UXV2.applySavedLayout('dashboardSections', 'dashboard');
        UXV2.setupSortableSections('dashboardSections', 'dashboard');
        await UXV2.renderDashboardOperational();
      };
    }

    // REPORTS ---------------------------------------------------------
    if (typeof Reports !== 'undefined' && !Reports.__uxV2Patched) {
      Reports.__uxV2Patched = true;
      const baseLoad = Reports.load.bind(Reports);
      Reports.load = async function () {
        await baseLoad();
        UXV2.applySavedLayout('reportsSections', 'reports');
        UXV2.setupSortableSections('reportsSections', 'reports');
        await UXV2.enhanceReportContribution();
        UXV2.enhanceTablesSoon();
      };
      Reports.moveSection = function (id, delta) {
        // Keyboard/accessibility fallback remains callable even though the
        // visible arrow buttons are hidden by CSS.
        const container = document.getElementById('reportsSections');
        const item = document.getElementById(id);
        if (!container || !item) return;
        const children = [...container.children];
        const index = children.indexOf(item);
        const target = index + delta;
        if (target < 0 || target >= children.length) return;
        if (delta < 0) container.insertBefore(item, children[target]);
        else container.insertBefore(children[target], item);
        localStorage.setItem(UXV2.layoutKey('reports'), JSON.stringify([...container.children].map(x => x.id)));
      };
    }

    // PORTFOLIO -------------------------------------------------------
    if (typeof Portfolio !== 'undefined' && !Portfolio.__uxV2Patched) {
      Portfolio.__uxV2Patched = true;
      const baseLoad = Portfolio.load.bind(Portfolio);
      Portfolio.load = async function (quiet = false) {
        await baseLoad(quiet);
        // Capital and exposure are operational risk and MUST include live
        // deployments excluded from performance analytics.
        try {
          const [deps, positions] = await Promise.all([Api.listDeployments(), Api.getAllPositions('open')]);
          const operationalDeps = deps.map(d => ({ ...d, include_in_reports: true }));
          Portfolio.renderCapital(operationalDeps);
          Portfolio.renderExposure(positions);
          const heading = document.querySelector('#view-portfolio #portfolioExposure')?.closest('section')?.querySelector('h2');
          if (heading) heading.innerHTML = 'Exposure by Symbol <span class="tag tag-info" title="Includes every live deployment, even if excluded from performance analytics.">all live risk</span>';
          UXV2.enhanceTablesSoon();
        } catch (e) { console.warn('UXV2 operational portfolio refresh failed', e); }
      };
    }

    // DETAIL ----------------------------------------------------------
    if (typeof Detail !== 'undefined' && !Detail.__uxV2Patched) {
      Detail.__uxV2Patched = true;
      const baseStats = Detail.renderStats.bind(Detail);
      const baseOpenEdit = Detail.openEditModal.bind(Detail);
      const baseOpenEditConfig = Detail.openEditConfigModal.bind(Detail);
      const baseRenderEditConfigFields = Detail._renderEditConfigFields?.bind(Detail);

      Detail.load = async function (id) {
        window.LivePnl?.untrack(UXV2.detailLiveHandler);
        UXV2.detailLiveHandler = null;
        this._id = id;
        this._trades = [];
        this._openTradeRows = new Set();
        this._calendarRange = this._calendarRange || 'recent';
        this._statsTrendPeriod = this._statsTrendPeriod || 'day';
        this._statsGranularity = this._statsGranularity || 'position';
        const route = UXV2.detailRoute();
        this._tab = route.section;
        document.getElementById('detailHeader').innerHTML = spinnerHtml();
        document.getElementById('detailTabs').innerHTML = '';
        document.getElementById('detailBody').innerHTML = spinnerHtml();
        try {
          this._dep = await Api.getDeployment(id);
        } catch (e) {
          document.getElementById('detailHeader').innerHTML = emptyHtml(`No such deployment (it may have been removed). <a href="#/deployments">Back to Deployed Strategies</a>`);
          document.getElementById('detailTabs').innerHTML = '';
          document.getElementById('detailBody').innerHTML = '';
          return;
        }
        UXV2.renderDetailHeader(this._dep);
        UXV2.renderDetailTabs();
        await this.renderBody();
      };

      Detail.renderTabs = () => UXV2.renderDetailTabs();
      Detail.switchTab = function (tab) {
        window.LivePnl?.untrack(UXV2.detailLiveHandler);
        UXV2.detailLiveHandler = null;
        window.location.hash = `#/deployments/${this._id}/${tab}`;
      };
      Detail.renderBody = async function () {
        const route = UXV2.detailRoute();
        this._tab = route.section;
        document.getElementById('detailBody').classList.remove('ux-analytics');
        try {
          if (this._tab === 'overview') return await UXV2.renderDetailOverview();
          if (this._tab === 'analytics') return await UXV2.renderDetailAnalytics(baseStats);
          if (this._tab === 'history') return await UXV2.renderDetailHistory();
          if (this._tab === 'configuration') return UXV2.renderDetailConfiguration();
        } catch (e) {
          console.error('UXV2 detail render failed', e);
          document.getElementById('detailBody').innerHTML = emptyHtml(`Could not load this section — ${escapeHtml(e.message || String(e))}`);
        }
      };
      Detail.stop = () => UXV2.openStopDialog(Detail._id, Detail._dep?.deployment_name);
      Detail.openEditModal = async function () {
        await baseOpenEdit();
        requestAnimationFrame(() => UXV2.renameAnalyticsSemantics(document.getElementById('editDeploymentModal') || document));
      };
      Detail.openEditConfigModal = function () {
        baseOpenEditConfig();
        requestAnimationFrame(() => UXV2.groupConfigFields(document.getElementById('editConfigFields')));
      };
      if (baseRenderEditConfigFields) {
        Detail._renderEditConfigFields = function (config) {
          baseRenderEditConfigFields(config);
          requestAnimationFrame(() => UXV2.groupConfigFields(document.getElementById('editConfigFields')));
        };
      }
    }

    // CATALOG ---------------------------------------------------------
    if (typeof Catalog !== 'undefined' && !Catalog.__uxV2Patched) {
      Catalog.__uxV2Patched = true;
      const baseRenderConfig = Catalog._renderConfigFields.bind(Catalog);
      Catalog._renderConfigFields = function (config) {
        baseRenderConfig(config);
        requestAnimationFrame(() => UXV2.groupConfigFields(document.getElementById('deployConfigFields')));
      };
      const persistDrafts = () => sessionStorage.setItem('uxV2DeployDrafts', JSON.stringify(Catalog._minimizedDrafts || []));
      const baseMinimize = Catalog.minimizeDeploy.bind(Catalog);
      const baseRestore = Catalog.restoreDraft.bind(Catalog);
      const baseDiscard = Catalog.discardDraft.bind(Catalog);
      Catalog.minimizeDeploy = function () { baseMinimize(); persistDrafts(); };
      Catalog.restoreDraft = function (id) { baseRestore(id); persistDrafts(); };
      Catalog.discardDraft = function (id, event) { baseDiscard(id, event); persistDrafts(); };
      const savedDrafts = safeJsonParse(sessionStorage.getItem('uxV2DeployDrafts') || '[]', []);
      if (Array.isArray(savedDrafts) && savedDrafts.length) {
        Catalog._minimizedDrafts = savedDrafts;
        requestAnimationFrame(() => Catalog._renderMinimizedDock());
      }
    }

    // INSTRUMENTS -----------------------------------------------------
    if (typeof Instruments !== 'undefined' && !Instruments.__uxV2Patched) {
      Instruments.__uxV2Patched = true;
      const baseRunSearch = Instruments.runSearch.bind(Instruments);
      Instruments.runSearch = async function (q) {
        await baseRunSearch(q);
        UXV2.enhanceInstrumentResults();
        UXV2.enhanceTablesSoon();
      };
    }

    // COMPARE ---------------------------------------------------------
    if (typeof Compare !== 'undefined' && !Compare.__uxV2Patched) {
      Compare.__uxV2Patched = true;
      const baseLoad = Compare.load.bind(Compare);
      const baseComparison = Compare.renderComparisonSection.bind(Compare);
      Compare.load = async function () {
        await baseLoad();
        const selected = safeJsonParse(sessionStorage.getItem('uxV2CompareSelection') || '[]', []);
        if (Array.isArray(selected) && selected.length >= 2) {
          this._selected = new Set(selected.filter(id => this._rows.some(r => r.deployment.id === id)).slice(0, 6));
          this.setActiveTab('compare');
          this.render();
          sessionStorage.removeItem('uxV2CompareSelection');
        }
      };
      Compare.renderComparisonSection = function () {
        const selectedRows = [...this._selected].map(id => this._rows.find(r => r.deployment.id === id)).filter(Boolean);
        if (selectedRows.length < 2) { baseComparison(); return; }
        const start = UXV2.compareStart(selectedRows, UXV2.comparePeriod);
        const original = this._rows;
        if (start) {
          const selectedSet = this._selected;
          this._rows = original.map(r => selectedSet.has(r.deployment.id) ? UXV2.deriveCompareRow(r, start) : r);
        }
        try { baseComparison(); }
        finally { this._rows = original; }
        const el = document.getElementById('compareComparisonSection');
        if (el && el.innerHTML) {
          el.insertAdjacentHTML('afterbegin', UXV2.comparePeriodToolbar(start));
          UXV2.compactCompareMetrics(el);
        }
      };
    }
  };

  // ------------------------------------------------------------------
  // Existing Catalog deploy success flow, but preserve its payload and
  // APIs while offering the natural next action: View deployment.
  // ------------------------------------------------------------------
  UXV2.installSubmitDeployOverride = function installSubmitDeployOverride() {
    if (window.__uxV2SubmitDeploy || typeof window.submitDeploy !== 'function') return;
    window.submitDeploy = async function () {
      const msg = document.getElementById('deployMsg');
      const advancedOn = document.getElementById('deployAdvancedToggle').checked;
      let config;
      if (advancedOn) {
        try { config = JSON.parse(document.getElementById('deployConfig').value || '{}'); }
        catch (e) { msg.innerHTML = `<span style="color:var(--loss)">Invalid config JSON: ${escapeHtml(e.message)}</span>`; return; }
      } else config = Catalog._readConfigFromFields();
      const body = {
        deployment_name: document.getElementById('deployName').value.trim(),
        strategy_name: Catalog._currentDeploy.strategyName,
        mode: document.getElementById('deployMode').value,
        initial_capital: Number(document.getElementById('deployCapital').value),
        config,
        notes: document.getElementById('deployNotes').value,
      };
      if (!body.deployment_name) { msg.innerHTML = '<span style="color:var(--loss)">Deployment name is required</span>'; return; }
      msg.innerHTML = '<span class="spinner"></span> Deploying…';
      const { ok, data } = await Api.createDeployment(body);
      if (!ok) { msg.innerHTML = `<span style="color:var(--loss)">${escapeHtml(data.detail || 'Failed')}</span>`; return; }
      UXV2.activeSummaryCache.delete(data.id);
      msg.innerHTML = `<span style="color:var(--gain)">✓ Deployed "${escapeHtml(data.deployment_name)}"</span> ${data.id ? `<button class="btn btn-primary btn-sm" style="margin-left:8px;" onclick="closeDeployModal(); location.hash='#/deployments/${data.id}/overview'">View deployment</button>` : ''}`;
      Catalog.load(true);
    };
    window.__uxV2SubmitDeploy = true;
  };

  // ------------------------------------------------------------------
  // Initialization after the existing inline DOMContentLoaded handler
  // has run its first router()/health setup. Module patches are already
  // installed earlier so that first router uses the new Detail/Dashboard.
  // ------------------------------------------------------------------
  UXV2.init = function init() {
    if (this.initialized) return;
    this.initialized = true;
    this.ensureSurfaces();
    this.groupNavigation();
    this.ensureTopbar();
    this.loadNotifications();
    this.ensureSelectionTray();
    this.startTableObserver();
    this.setupSortableSections('dashboardSections', 'dashboard');
    this.setupSortableSections('reportsSections', 'reports');
    this.setupDashboardCustomize();
    this.setupSettings();
    this.renameAnalyticsSemantics(document);
    this.installToastCapture();
    this.installSubmitDeployOverride();

    document.addEventListener('click', e => {
      if (this._openPopover && !this._openPopover.contains(e.target)) this.closePopover();
      const notif = document.getElementById('uxNotificationPanel');
      if (notif?.classList.contains('open') && !notif.contains(e.target) && !e.target.closest('.ux-icon-btn')) notif.classList.remove('open');
    });
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape') return;
      this.closePopover();
      document.getElementById('uxNotificationPanel')?.classList.remove('open');
      if (document.getElementById('uxDialog')?.classList.contains('open')) this.closeDialog();
      else if (document.getElementById('uxDrawer')?.classList.contains('open')) this.closeDrawer();
    });

    window.addEventListener('hashchange', e => {
      const oldUrl = String(e.oldURL || '');
      const newUrl = String(e.newURL || '');
      if (/#[/]?deployments(?:$|(?=\?))/.test(oldUrl) && /#\/?deployments\/[^/]+/.test(newUrl)) this.saveDeploymentListState();
      if (!newUrl.includes('#/deployments')) this.updateSelectionTray();
      requestAnimationFrame(() => {
        this.setupSettings();
        this.renameAnalyticsSemantics(document);
        this.setupSortableSections('dashboardSections', 'dashboard');
        this.setupSortableSections('reportsSections', 'reports');
      });
    });

    // Mark the dashboard widget container as a grid only once a half-size
    // preference exists. Full-width default remains visually identical.
    const sizes = safeJsonParse(localStorage.getItem(this.sizeKey('dashboard')) || '{}', {});
    if (Object.values(sizes).some(v => v === 'half')) document.getElementById('dashboardSections')?.classList.add('ux-widget-grid');
  };

  // Install patches now; the modules listed in index.html have already
  // executed by the time this script tag is reached.
  UXV2.installModulePatches();

  window.addEventListener('DOMContentLoaded', () => {
    // Run after index.html's own DOMContentLoaded handler completes its
    // initial router/health/ticker setup.
    setTimeout(() => UXV2.init(), 0);
  });
})();
