// live_deploy — UIKit: shared UI chrome used by every view — drawers,
// dialogs, popovers, the notification centre, the sidebar/top bar
// chrome, and a generic table framework (resizable/reorderable/
// hideable columns with a persisted totals row) plus a generic
// draggable-section framework (used by both Dashboard and Reports for
// their reorderable widget/section layout).
//
// This is a normal shared library, loaded once, right after api.js and
// before every page's own file — exactly like `Api`/`SectionOrder`/
// `ChartTooltip`/`LivePnl` already are. Page files call INTO this
// (`UIKit.openDrawer(...)`, `UIKit.enhanceTable(table)`, ...); this file
// never reaches into a page file and reassigns one of its methods.

const UIKit = {
  notifications: [],
  notificationUnread: 0,
  tableObserver: null,
  _openPopover: null,
};

function safeJsonParse(value, fallback) {
  try { return JSON.parse(value); } catch (_) { return fallback; }
}

// ── Drawer / dialog surfaces — a slide-in side panel (drawer) for
// read-only detail (a trade's full metadata, an Active P&L breakdown)
// and a centered modal (dialog) for a short interactive decision (the
// Stop confirmation below). Both lazily create their own DOM the first
// time either is opened. ─────────────────────────────────────────────
UIKit.ensureSurfaces = function ensureSurfaces() {
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
        <button class="btn btn-secondary btn-sm" onclick="UIKit.closeDrawer()" aria-label="Close">✕</button>
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

UIKit.openDrawer = function openDrawer(title, html, sub = '') {
  this.ensureSurfaces();
  document.getElementById('uxDrawerTitle').textContent = title;
  document.getElementById('uxDrawerSub').textContent = sub || '';
  document.getElementById('uxDrawerBody').innerHTML = html;
  document.getElementById('uxDrawerBackdrop').classList.add('open');
  document.getElementById('uxDrawer').classList.add('open');
  document.body.style.overflow = 'hidden';
};

UIKit.closeDrawer = function closeDrawer() {
  document.getElementById('uxDrawerBackdrop')?.classList.remove('open');
  document.getElementById('uxDrawer')?.classList.remove('open');
  if (!document.querySelector('.modal-overlay.open') && !document.getElementById('uxDialog')?.classList.contains('open')) {
    document.body.style.overflow = '';
  }
};

UIKit.openDialog = function openDialog(html) {
  this.ensureSurfaces();
  document.getElementById('uxDialog').innerHTML = html;
  document.getElementById('uxDialogBackdrop').classList.add('open');
  document.getElementById('uxDialog').classList.add('open');
  document.body.style.overflow = 'hidden';
};

UIKit.closeDialog = function closeDialog() {
  document.getElementById('uxDialogBackdrop')?.classList.remove('open');
  document.getElementById('uxDialog')?.classList.remove('open');
  if (!document.querySelector('.modal-overlay.open') && !document.getElementById('uxDrawer')?.classList.contains('open')) {
    document.body.style.overflow = '';
  }
};

// Stop-deployment confirmation — offered from the Deployments list, the
// Detail page's own header/menu, and Dashboard's "needs attention" list,
// so it lives here rather than duplicated in each of those.
UIKit.openStopDialog = async function openStopDialog(id, name) {
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
      <button class="btn btn-secondary" onclick="UIKit.closeDialog()">Cancel</button>
      <button class="btn btn-danger" id="uxStopConfirmBtn" onclick="UIKit.confirmStop('${id}', ${n ? 'true' : 'false'})">Stop deployment</button>
    </div>
    <div class="modal-msg" id="uxStopMsg"></div>`);
};

UIKit.confirmStop = async function confirmStop(id, hasPositions) {
  const msg = document.getElementById('uxStopMsg');
  const close = hasPositions && document.querySelector('input[name="uxStopMode"]:checked')?.value === 'close';
  msg.innerHTML = '<span class="spinner"></span> Stopping…';
  const r = await Api.stopDeployment(id, !!close);
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    msg.innerHTML = `<span style="color:var(--loss)">${escapeHtml(data.detail || 'Could not stop deployment')}</span>`;
    return;
  }
  Api._activeSummaryCache.delete(id);
  this.closeDialog();
  if (window.location.hash.includes(`/deployments/${id}`)) await Detail.load(id);
  else if (typeof Deployments !== 'undefined') await Deployments.load(true);
};

// ── Sidebar / top bar chrome ────────────────────────────────────────
UIKit.groupNavigation = function groupNavigation() {
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
};

UIKit.ensureTopbar = function ensureTopbar() {
  const main = document.querySelector('main.main');
  if (!main || document.getElementById('uxTopbar')) return;
  const bar = document.createElement('div');
  bar.id = 'uxTopbar';
  bar.className = 'ux-topbar';
  bar.innerHTML = `
    <div class="ux-topbar-left">
      <span class="ux-topbar-title">Trading control</span>
      <button class="ux-status-btn" id="uxKiteStatus" onclick="UIKit.openKitePopover(event)">
        <span class="ux-status-dot" id="uxKiteDot"></span><span id="uxKiteText">Checking Kite…</span>
      </button>
    </div>
    <div class="ux-topbar-right">
      <button class="ux-icon-btn" title="Notifications" onclick="UIKit.toggleNotificationPanel(event)">🔔<span class="ux-unread-badge" id="uxUnreadBadge" style="display:none;">0</span></button>
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

UIKit.syncKiteStatus = function syncKiteStatus() {
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

UIKit.openKitePopover = function openKitePopover(event) {
  event.stopPropagation();
  const statusText = document.getElementById('statusBar')?.textContent.replace(/\s+/g, ' ').trim() || 'Status unavailable';
  this.openPopover(event.currentTarget, `
    <div style="padding:7px 9px 9px;">
      <div style="font-size:9px;color:var(--parchment);text-transform:uppercase;font-weight:800;letter-spacing:.06em;">Kite connection</div>
      <div style="font-size:11px;font-weight:700;margin-top:5px;">${escapeHtml(statusText)}</div>
    </div>
    <div class="ux-menu-sep"></div>
    <button class="ux-menu-item" onclick="UIKit.closePopover(); loginWithKite()">Re-login with Kite</button>
    <button class="ux-menu-item" onclick="UIKit.closePopover(); openManualLoginModal()">Enter token manually</button>`);
};

UIKit.openPopover = function openPopover(anchor, html) {
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

UIKit.closePopover = function closePopover() {
  if (this._openPopover) this._openPopover.remove();
  this._openPopover = null;
};

// ── Notification centre — a session-persisted feed of the same events
// showToast() already displays as a toast (see index.html's own
// showToast(), which calls UIKit.captureNotification() directly). ─────
UIKit.loadNotifications = function loadNotifications() {
  const saved = safeJsonParse(sessionStorage.getItem('uxNotifications') || '[]', []);
  this.notifications = Array.isArray(saved) ? saved.slice(0, 50) : [];
  this.notificationUnread = Number(sessionStorage.getItem('uxNotificationUnread') || 0);
  this.renderUnreadBadge();
};

UIKit.saveNotifications = function saveNotifications() {
  sessionStorage.setItem('uxNotifications', JSON.stringify(this.notifications.slice(0, 50)));
  sessionStorage.setItem('uxNotificationUnread', String(this.notificationUnread));
};

UIKit.captureNotification = function captureNotification(ev) {
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

UIKit.renderUnreadBadge = function renderUnreadBadge() {
  const badge = document.getElementById('uxUnreadBadge');
  if (!badge) return;
  badge.textContent = this.notificationUnread > 99 ? '99+' : String(this.notificationUnread);
  badge.style.display = this.notificationUnread > 0 ? '' : 'none';
};

UIKit.toggleNotificationPanel = function toggleNotificationPanel(event) {
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

UIKit.renderNotificationPanel = function renderNotificationPanel() {
  const panel = document.getElementById('uxNotificationPanel');
  if (!panel) return;
  panel.innerHTML = `
    <div class="ux-notification-head"><b>Notifications</b><button class="btn btn-secondary btn-sm" onclick="UIKit.clearNotifications()">Clear</button></div>
    ${this.notifications.length ? this.notifications.map((n, i) => `
      <div class="ux-notification-item" onclick="UIKit.openNotification(${i})">
        <div class="ux-notification-title">${escapeHtml(n.deployment_name || 'live_deploy')} · ${escapeHtml(n.event_type.replace(/_/g, ' '))}</div>
        <div class="ux-notification-msg">${escapeHtml(n.message || '')}</div>
        <div class="ux-notification-time">${humanAgo(n.at)}</div>
      </div>`).join('') : '<div class="empty" style="padding:24px;">No notifications yet.</div>'}`;
};

UIKit.openNotification = function openNotification(i) {
  const n = this.notifications[i];
  if (n?.deployment_id) window.location.hash = `#/deployments/${n.deployment_id}/overview`;
  document.getElementById('uxNotificationPanel')?.classList.remove('open');
};

UIKit.clearNotifications = function clearNotifications() {
  this.notifications = [];
  this.notificationUnread = 0;
  this.saveNotifications();
  this.renderUnreadBadge();
  this.renderNotificationPanel();
};

// ── Generic tables: width resize, header reorder, persisted density,
// per-column hide/show, and a totals row that survives all of the
// above. Every page's own render() calls UIKit.enhanceTablesSoon()
// after building a table's HTML; a MutationObserver (startTableObserver
// below) also catches tables added without an explicit call, as a
// safety net. It only enhances simple one-row-header tables where every
// body row has the same cell count — complex matrix/colspan tables opt
// out. ──────────────────────────────────────────────────────────────
UIKit.tableKey = function tableKey(table) {
  if (table.dataset.uxTableKey) return table.dataset.uxTableKey;
  const parentId = table.closest('[id]')?.id || 'generic';
  const idx = [...document.querySelectorAll(`#${CSS.escape(parentId)} table`)].indexOf(table);
  const key = `${parentId}:${Math.max(0, idx)}`;
  table.dataset.uxTableKey = key;
  return key;
};

UIKit.normalizeColKey = function normalizeColKey(text, index) {
  const clean = String(text || '').replace(/[▲▼↕]/g, '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  return clean || `col_${index}`;
};

UIKit.enhanceTable = function enhanceTable(table) {
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

// Sets a money-formatted, pos/neg-colored value into an element in
// place -- shared by Dashboard's own "Right now" KPI cards and Detail's
// Overview tab, both of which live-tick a few specific elements off
// window.LivePnl rather than re-rendering their whole section per tick.
UIKit.setLiveMoney = function setLiveMoney(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = fmtSignedMoney(value);
  const preserve = [...el.classList].filter(c => !['pos', 'neg'].includes(c));
  el.className = `${preserve.join(' ')} ${pnlClass(value)}`.trim();
};

// A tfoot totals row often has one real <td> per LOGICAL column (see
// e.g. deployments.js's own render(), or detail.js's positionsTable()),
// each tagged `data-ux-col-key` to match its header th's own key.
// Deliberately never a colspan-merged label cell — a colspan cell can
// only ever "move" as one indivisible block covering a FIXED span of
// columns, so it can't stay correctly aligned once a single column
// inside that span gets dragged out on its own; one tagged cell per
// column sidesteps that entirely.
UIKit.tfootCellsByKey = function tfootCellsByKey(table) {
  if (!table.tFoot) return [];
  const cells = [];
  [...table.tFoot.rows].forEach(row => [...row.cells].forEach(cell => {
    if (cell.dataset.uxColKey) cells.push(cell);
  }));
  return cells;
};

UIKit.applyTableOrder = function applyTableOrder(table, key) {
  const saved = safeJsonParse(localStorage.getItem(`uxTableOrder:${key}`) || '[]', []);
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

UIKit.applyTableWidths = function applyTableWidths(table, key) {
  const widths = safeJsonParse(localStorage.getItem(`uxTableWidths:${key}`) || '{}', {});
  const head = table.tHead.rows[0];
  [...head.cells].forEach((th, i) => {
    const w = Number(widths[th.dataset.uxColKey]);
    if (w > 0) this.setColumnWidth(table, i, w);
  });
};

UIKit.setColumnWidth = function setColumnWidth(table, index, px) {
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

UIKit.applyTableVisibility = function applyTableVisibility(table, key) {
  const prefs = safeJsonParse(localStorage.getItem(`uxTableVisible:${key}`) || '{}', {});
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

UIKit.setTableColumnVisible = function setTableColumnVisible(table, colKey, visible) {
  const key = this.tableKey(table);
  const prefs = safeJsonParse(localStorage.getItem(`uxTableVisible:${key}`) || '{}', {});
  prefs[colKey] = visible;
  localStorage.setItem(`uxTableVisible:${key}`, JSON.stringify(prefs));
  this.applyTableVisibility(table, key);
};

UIKit.ensureTableFloatingSettings = function ensureTableFloatingSettings(table) {
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

UIKit.openTableSettingsForElement = function openTableSettingsForElement(event, table) {
  event.stopPropagation();
  const key = this.tableKey(table);
  const density = localStorage.getItem(`uxTableDensity:${key}`) || 'comfortable';
  const visibility = safeJsonParse(localStorage.getItem(`uxTableVisible:${key}`) || '{}', {});
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
    localStorage.setItem(`uxTableDensity:${key}`, btn.dataset.uxDensity);
    this.applyTableDensity(table, key); this.closePopover();
  }));
  pop.querySelector('[data-ux-reset-table]')?.addEventListener('click', () => {
    localStorage.removeItem(`uxTableOrder:${key}`);
    localStorage.removeItem(`uxTableWidths:${key}`);
    localStorage.removeItem(`uxTableVisible:${key}`);
    localStorage.removeItem(`uxTableDensity:${key}`);
    this.closePopover();
    window.location.reload();
  });
};

UIKit.applyTableDensity = function applyTableDensity(table, key) {
  const density = localStorage.getItem(`uxTableDensity:${key}`) || 'comfortable';
  table.classList.toggle('ux-table-density-compact', density === 'compact');
};

UIKit.attachTableColumnInteractions = function attachTableColumnInteractions(table, key) {
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
        const widths = safeJsonParse(localStorage.getItem(`uxTableWidths:${key}`) || '{}', {});
        widths[th.dataset.uxColKey] = Math.round(th.getBoundingClientRect().width);
        localStorage.setItem(`uxTableWidths:${key}`, JSON.stringify(widths));
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
      const widths = safeJsonParse(localStorage.getItem(`uxTableWidths:${key}`) || '{}', {});
      widths[th.dataset.uxColKey] = Math.round(Math.min(420, max));
      localStorage.setItem(`uxTableWidths:${key}`, JSON.stringify(widths));
    });

    // Native HTML5 drag-and-drop, not Pointer Events — a column drag
    // never reparents the dragged `th` itself mid-gesture (only a drop
    // target class toggles during dragover), so this isn't subject to
    // the pointer-capture-lost-on-reparent issue the section drag
    // below has to work around.
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
      localStorage.setItem(`uxTableOrder:${key}`, JSON.stringify(order));
    });
  });
};

UIKit.moveTableColumn = function moveTableColumn(table, fromIndex, toIndex) {
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
  // own comment): only cells explicitly tagged for one of the two
  // columns actually involved in this move need to move at all.
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

UIKit.enhanceTables = function enhanceTables(root = document) {
  root.querySelectorAll?.('table').forEach(t => this.enhanceTable(t));
};
UIKit.enhanceTablesSoon = debounce(function () { UIKit.enhanceTables(document); }, 20);

UIKit.startTableObserver = function startTableObserver() {
  if (this.tableObserver) return;
  this.tableObserver = new MutationObserver(() => { this.enhanceTablesSoon(); this.renameAnalyticsSemantics(document); });
  const main = document.querySelector('main.main');
  if (main) this.tableObserver.observe(main, { childList: true, subtree: true });
  this.enhanceTablesSoon();
};

UIKit.openTableSettings = function openTableSettings(event, containerId) {
  event.stopPropagation();
  const table = document.querySelector(`#${CSS.escape(containerId)} table`);
  if (!table) return;
  const key = this.tableKey(table);
  const density = localStorage.getItem(`uxTableDensity:${key}`) || 'comfortable';
  this.openPopover(event.currentTarget, `
    <button class="ux-menu-item" onclick="UIKit.setTableDensity('${containerId}','comfortable')">Comfortable density <span>${density === 'comfortable' ? '✓' : ''}</span></button>
    <button class="ux-menu-item" onclick="UIKit.setTableDensity('${containerId}','compact')">Compact density <span>${density === 'compact' ? '✓' : ''}</span></button>
    <div class="ux-menu-sep"></div>
    <button class="ux-menu-item" onclick="UIKit.resetTableLayout('${containerId}')">Reset widths &amp; column order</button>`);
};

UIKit.setTableDensity = function setTableDensity(containerId, density) {
  const table = document.querySelector(`#${CSS.escape(containerId)} table`);
  if (!table) return;
  localStorage.setItem(`uxTableDensity:${this.tableKey(table)}`, density);
  this.applyTableDensity(table, this.tableKey(table));
  this.closePopover();
};

UIKit.resetTableLayout = function resetTableLayout(containerId) {
  const table = document.querySelector(`#${CSS.escape(containerId)} table`);
  if (!table) return;
  const key = this.tableKey(table);
  localStorage.removeItem(`uxTableOrder:${key}`);
  localStorage.removeItem(`uxTableWidths:${key}`);
  localStorage.removeItem(`uxTableVisible:${key}`);
  this.closePopover();
  // The containing view's normal render restores the canonical order.
  if (containerId === 'deploymentsTable') Deployments.render();
  else window.location.reload();
};

// ── Reorderable sections: Dashboard's own widgets and Reports' own
// sections both use this same drag-handle framework (setupSortableSections
// is called once per container/name pair by each page). ────────────────
UIKit.layoutKey = function layoutKey(name) { return `uxLayout:${name}`; };
UIKit.visibilityKey = function visibilityKey(name) { return `uxLayoutVisible:${name}`; };
UIKit.sizeKey = function sizeKey(name) { return `uxLayoutSize:${name}`; };

UIKit.applySavedLayout = function applySavedLayout(containerId, name) {
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
    else if (!el.classList.contains('dash-section-superseded')) el.style.removeProperty('display');
  });
  const sizes = safeJsonParse(localStorage.getItem(this.sizeKey(name)) || '{}', {});
  [...container.children].forEach(el => { if (el.id) el.dataset.uxSize = sizes[el.id] || 'full'; });
};

UIKit.setupSortableSections = function setupSortableSections(containerId, name) {
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
    // (even a same-parent "move" via insertBefore) reliably releases
    // Chromium's pointer capture: the section would even visibly jump
    // once (the DOM move genuinely happened), but every event after
    // that point, including pointerup, silently stops reaching
    // `handle`, so finish() (and its localStorage save) never runs and
    // the drag never cleanly ends. Keeping the actual move to one
    // single insertBefore call in finish(), after the gesture is
    // already over, sidesteps the whole issue -- only the CSS
    // drop-indicator (harmless, no reparenting) updates during
    // pointermove now.
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
      localStorage.setItem(UIKit.layoutKey(name), JSON.stringify([...container.children].map(x => x.id).filter(Boolean)));
      handle.focus();
    });
  });
};

// The "include_in_reports" field remains the API/DB name for
// compatibility, but the product meaning is clearer as "performance
// analytics only" — operational risk/activity never disappears just
// because a deployment opts out of it. This retitles the couple of
// places in the DOM that still say "reports."
UIKit.renameAnalyticsSemantics = function renameAnalyticsSemantics(root = document) {
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

// Groups a config-field container's individual <div class="field">
// elements by topic (see configGroup below) — shared between Catalog's
// Deploy modal and Detail's Edit Config modal, both built from the same
// per-key field widgets (configFieldsContainerHtml, api.js).
UIKit.configGroup = function configGroup(key) {
  const k = key.toLowerCase();
  if (/capital|qty|quantity|lot|size|allocation/.test(k)) return 'Position sizing';
  if (/adjust|roll|delta|hedge|rebalance/.test(k)) return 'Adjustments';
  if (/stop|loss|target|profit|exit|square|trail|max_/.test(k)) return 'Exit & risk';
  if (/time|entry|start|open|weekday|day|expiry/.test(k)) return 'Entry & timing';
  return 'Strategy';
};

UIKit.groupConfigFields = function groupConfigFields(container) {
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

// Groups Account's own tab bar into a labeled sidebar-style nav
// (PERSONAL / ADMINISTRATION) instead of a flat row of tab buttons --
// called once per Account.load() (idempotent past the first call).
UIKit.setupSettings = function setupSettings() {
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

// ── App-shell bootstrap — called once from index.html's own
// DOMContentLoaded handler, after every page module has defined itself.
UIKit.init = function init() {
  this.ensureSurfaces();
  this.groupNavigation();
  this.ensureTopbar();
  this.loadNotifications();
  this.startTableObserver();

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

  window.addEventListener('hashchange', () => {
    requestAnimationFrame(() => this.renameAnalyticsSemantics(document));
  });
};
