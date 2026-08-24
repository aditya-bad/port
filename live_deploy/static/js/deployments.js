// live_deploy — Deployed Strategies view: every deployment, filterable
// by status/strategy, searchable, sortable by any column, with a
// column-visibility selector so a long list stays scannable instead of
// clumsy (Step 93 — full redesign, replacing a fixed 10-column table
// with always-visible action buttons). Running P&L is still pulled in
// directly (no click-through needed just to see if something's
// currently winning or losing). Clicking a row (not a button/menu)
// navigates to that deployment's Strategy Detail page — a real
// drill-down, not an inline expand.

// Column definitions — the single source of truth this whole view is
// built from: the header row, each body cell, the column-visibility
// menu, sorting, and CSV export ALL read from this one list, so adding
// a column here is the only place a new one needs wiring in.
// `key`: stable id, used for localStorage persistence + sort state.
// `label`: header text.
// `always`: can't be hidden via the column selector (Name is the row's
//   own anchor/link; Actions is the row menu — hiding either would
//   leave a row with nothing to click or act on).
// `numeric`: right-aligned, and sorts/exports as a raw number.
// `sortValue(d)`: value to compare when sorting by this column.
// `render(d)`: cell HTML.
// `csvValue(d)`: plain value for CSV export (defaults to sortValue).
const DEPLOY_COLUMNS = [
  {
    key: 'ux_select', label: '', always: true, sortable: false,
    render: d => `<span class="ux-select-cell"><input type="checkbox" aria-label="Select ${escapeHtml(d.deployment_name)} for comparison" ${Deployments._selectedIds.has(d.id) ? 'checked' : ''} onclick="event.stopPropagation()" onchange="Deployments.toggleSelection('${d.id}', this.checked)"></span>`,
    csvValue: () => '', sortValue: () => '',
  },
  {
    key: 'name', label: 'Name', always: true,
    sortValue: d => (d.deployment_name || '').toLowerCase(),
    // "unregistered"/"excluded from reports"/custom tags all moved to
    // their own Tags column below (Step 93) -- deploymentTagsHtml()
    // already renders all three from this same `d`, so this cell no
    // longer duplicates any of it.
    render: d => `
      <a href="#/deployments/${d.id}" onclick="event.stopPropagation()">${escapeHtml(d.deployment_name)}</a>
      ${d.notes ? `<div class="card-sub" style="margin-top:2px; max-width:260px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(d.notes)}">📝 ${escapeHtml(d.notes)}</div>` : ''}
    `,
  },
  {
    key: 'strategy', label: 'Strategy',
    sortValue: d => (d.strategy_name || '').toLowerCase(),
    render: d => escapeHtml(d.strategy_name),
  },
  {
    key: 'status', label: 'Status',
    sortValue: d => d.status,
    render: d => `<span class="tag tag-${d.status}">${d.status}</span>`,
  },
  {
    key: 'mode', label: 'Mode',
    sortValue: d => d.mode,
    render: d => escapeHtml(d.mode),
  },
  {
    key: 'ux_current_pnl', label: 'Current P&L', numeric: true,
    headerTitle: 'Intraday = Today. Positional = currently active strategic cycle.',
    sortValue: d => d._uxActive?.total_pnl ?? -Infinity,
    render: d => {
      const a = d._uxActive;
      if (!a) return '<span class="card-sub">calculating…</span>';
      if (d.mode === 'positional' && !a.active) {
        return `<span class="ux-current-pnl"><span>—</span><span class="ux-pnl-period">Flat${a.last_cycle_pnl != null ? ` · last ${fmtSignedMoney(a.last_cycle_pnl)}` : ''}</span></span>`;
      }
      return `<span class="ux-current-pnl ${pnlClass(a.total_pnl)}">${fmtSignedMoney(a.total_pnl)}<span class="ux-pnl-period">${escapeHtml(a.period_label)}</span></span>`;
    },
    csvValue: d => d._uxActive?.total_pnl ?? '',
    // total(ctx) -- see the tfoot-building comment below on why every
    // numeric column owns its own total function. Deliberately sums
    // over ctx.allRows, not ctx.reportRows -- unlike the legacy
    // accounting columns below (Capital/Cash/Realized/...), which stay
    // scoped to include_in_reports=true rows for historical-performance
    // consistency, Current P&L is the SAME operational-truth number
    // Dashboard's own "Right now" zone shows (Active P&L, "includes
    // every live deployment, even if excluded from analytics") --
    // scoping it to reportRows here would silently disagree with that.
    total: ({ allRows }) => {
      const ready = allRows.filter(d => d._uxActive);
      if (!ready.length) return '';
      const t = ready.reduce((s, d) => s + (d._uxActive.total_pnl || 0), 0);
      return `<span class="ux-current-pnl ${pnlClass(t)}">${fmtSignedMoney(t)}</span>`;
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
  {
    key: 'tags', label: 'Tags',
    // Moved out of the Name cell into its own column (Step 93, on
    // request) -- "excluded from reports" + every custom tag, same
    // chip-row rendering as before, just independently sortable/
    // searchable/hideable now rather than bolted onto Name.
    sortValue: d => [
      d.include_in_reports ? '' : 'excluded from reports',
      ...(d.tags || []),
    ].join(',').toLowerCase(),
    render: d => deploymentTagsHtml(d) || '<span class="card-sub">—</span>',
    csvValue: d => [
      d.include_in_reports ? null : 'excluded from reports',
      ...(d.tags || []),
    ].filter(Boolean).join('; '),
  },
  {
    key: 'capital', label: 'Capital', numeric: true,
    sortValue: d => d.initial_capital || 0,
    render: d => fmtMoney(d.initial_capital),
    // `total(ctx)` -- the totals-row (tfoot) cell for this column, given
    // `{ reportRows, allRows }` (see the tfoot-building code below).
    // Column-owned rather than a hardcoded lookup table so any NEW
    // column added to this array (like ux_current_pnl/ux_open_positions
    // above) automatically gets a real total instead of silently
    // rendering an empty cell (Step: this is exactly the bug that made
    // the whole totals row look blank the moment those decision-
    // oriented columns replaced these accounting ones as the default
    // set -- the OLD hardcoded totalByKey object had no entry for them
    // at all).
    total: ({ reportRows }) => fmtMoney(reportRows.reduce((s, d) => s + (d.initial_capital || 0), 0)),
  },
  {
    key: 'cash', label: 'Cash', numeric: true,
    sortValue: d => d.current_cash || 0,
    render: d => fmtMoney(d.current_cash),
    total: ({ reportRows }) => fmtMoney(reportRows.reduce((s, d) => s + (d.current_cash || 0), 0)),
  },
  {
    key: 'open_cost', label: 'Open Cost',
    headerTitle: "Entry-price value of currently open positions -- a credit for a sold option's premium (not yet Realized until it's bought back), a debit for a bought one. Cash always equals Capital + Realized + this.",
    numeric: true,
    sortValue: d => d.open_cost_basis || 0,
    render: d => `<span class="${pnlClass(d.open_cost_basis)}" title="Cash = ${fmtMoney(d.initial_capital)} + ${fmtSignedMoney(d.realized_pnl)} + ${fmtSignedMoney(d.open_cost_basis)}">${fmtSignedMoney(d.open_cost_basis)}</span>`,
    total: ({ reportRows }) => {
      const t = reportRows.reduce((s, d) => s + (d.open_cost_basis || 0), 0);
      return `<span class="${pnlClass(t)}">${fmtSignedMoney(t)}</span>`;
    },
  },
  {
    key: 'realized', label: 'Realized', numeric: true,
    sortValue: d => d.realized_pnl || 0,
    render: d => `<span class="${pnlClass(d.realized_pnl)}">${fmtSignedMoney(d.realized_pnl)}</span>`,
    total: ({ reportRows }) => {
      const t = reportRows.reduce((s, d) => s + (d.realized_pnl || 0), 0);
      return `<span class="${pnlClass(t)}">${fmtSignedMoney(t)}</span>`;
    },
  },
  {
    key: 'unrealized', label: 'Unrealized', numeric: true,
    // Live-ticked in place after initial render (see the LivePnl.track
    // handler in load() below) -- td gets a `live-pnl` class + the
    // deployment id so that handler can find it again without a
    // re-render.
    sortValue: d => d.unrealized_pnl || 0,
    render: d => `<span class="live-pnl ${pnlClass(d.unrealized_pnl)}">${fmtSignedMoney(d.unrealized_pnl)}</span>`,
    total: ({ reportRows }) => {
      // NOT live-ticked itself (a per-render snapshot, same as every
      // other total here) -- unlike the individual `live-pnl` cells,
      // there's no live-total tick handler wired to this footer cell.
      // Accurate as of the last render/refresh, same staleness window
      // the rest of this row's totals already have.
      const t = reportRows.reduce((s, d) => s + (d.unrealized_pnl || 0), 0);
      return `<span class="live-pnl-total ${pnlClass(t)}">${fmtSignedMoney(t)}</span>`;
    },
  },
  {
    key: 'actions', label: 'Actions', always: true, sortable: false,
    render: d => `
      <div class="row-menu">
        <button class="row-menu-btn" onclick="Deployments.toggleRowMenu(event, '${d.id}')" aria-label="Actions">⋯</button>
        <div class="row-menu-dropdown" id="rowMenu-${d.id}">
          ${d.status === 'active' ? `<button onclick="Deployments.pause('${d.id}')">Pause</button>` : ''}
          ${d.status === 'paused' ? `<button onclick="Deployments.resume('${d.id}')">Resume</button>` : ''}
          ${d.status !== 'stopped' ? `<button class="danger" onclick="Deployments.stop('${d.id}')">Stop</button>` : ''}
          ${d.status === 'stopped' ? `<button class="danger" onclick="Deployments.deleteDeployment('${d.id}')">Delete</button>` : ''}
        </div>
      </div>
    `,
  },
];

const Deployments = {
  _all: [],
  _livePnlHandler: null,
  _sortKey: 'name',
  _sortDir: 'asc',   // 'asc' | 'desc'
  _visibleCols: null,   // Set<key> -- populated in load() from localStorage/defaults
  _openRowMenuId: null,
  _selectedIds: new Set(),   // checked via the ux_select column, feeds Compare (see compareSelection)

  // quiet=true: event-driven background refresh -- see Dashboard.load()'s
  // own comment for why the spinner reset is skipped in that case.
  async load(quiet = false) {
    window.LivePnl.untrack(this._livePnlHandler);   // never stack trackers across reloads
    this._livePnlHandler = null;
    this._loadColumnPrefs();
    this.ensureSelectionTray();

    const el = document.getElementById('deploymentsTable');
    if (!quiet) el.innerHTML = spinnerHtml();
    const [all, positions] = await Promise.all([
      Api.listDeployments(),
      // The list response only ever carries each deployment's own
      // ALREADY-COMPUTED unrealized_pnl total, not the underlying
      // positions -- fetching the same cross-deployment aggregate
      // Dashboard uses is what lets the Unrealized column update live
      // per-tick here too, instead of sitting frozen until the next
      // full reload (previously nothing refreshed it at all between
      // loads/the event-driven quiet refresh).
      Api.getAllPositions('open'),
    ]);
    this._all = all;
    await Api.enrichDeployments(this._all, positions);
    this._populateStrategyFilter();
    this.render();
    this.restoreListState();
    this.ensureQuickFilters();
    this.updateSelectionTray();
    UIKit.enhanceTablesSoon();
    markUpdated('deploymentsUpdatedLabel');

    this._livePnlHandler = window.LivePnl.track(positions, ({ totalPnl }) => {
      for (const d of this._all) {
        const combined = totalPnl(d.id);
        if (combined == null) continue;
        const cell = el.querySelector(`tr[data-deployment-id="${d.id}"] .live-pnl`);
        if (!cell) continue;
        cell.textContent = fmtSignedMoney(combined);
        cell.className = `live-pnl ${pnlClass(combined)}`;
      }

      // Total row -- summed over whatever's CURRENTLY filtered, not
      // always every deployment (see _filteredRows()'s own comment),
      // AND excluding include_in_reports=false same as render()'s own
      // static totals above.
      let visibleUnrealized = 0, anyPriced = false;
      for (const d of this._filteredRows()) {
        if (!d.include_in_reports) continue;
        const combined = totalPnl(d.id);
        visibleUnrealized += combined != null ? combined : (d.unrealized_pnl || 0);
        if (combined != null) anyPriced = true;
      }
      if (anyPriced) {
        const totalCell = el.querySelector('.live-pnl-total');
        if (totalCell) {
          totalCell.textContent = fmtSignedMoney(visibleUnrealized);
          totalCell.className = `live-pnl-total ${pnlClass(visibleUnrealized)}`;
        }
      }
    });
  },

  _populateStrategyFilter() {
    const select = document.getElementById('filterStrategy');
    const current = select.value;
    const names = [...new Set(this._all.map(d => d.strategy_name))].sort();
    select.innerHTML = '<option value="">All strategies</option>'
      + names.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');
    if (names.includes(current)) select.value = current;
  },

  // ── Search (Step 93) — plain client-side substring match, same
  // "everything's already loaded, no reason to round-trip the server
  // for this" reasoning as the status/strategy filters right next to
  // it. Debounced the same way the live-tick refresh elsewhere in this
  // app is (see _scheduleLiveRefresh, index.html) -- typing fast
  // shouldn't re-render on every keystroke. ──────────────────────────
  _searchDebounce: null,
  onSearchInput() {
    clearTimeout(this._searchDebounce);
    this._searchDebounce = setTimeout(() => this.render(), 150);
  },
  _matchesSearch(d, query) {
    if (!query) return true;
    const haystack = [
      d.deployment_name, d.strategy_name, d.mode, d.notes,
      ...(d.tags || []),
    ].filter(Boolean).join(' ').toLowerCase();
    return haystack.includes(query);
  },

  // Shared between render() and the live-tick handler above so both
  // always agree on "what's currently visible" -- the total row's live
  // updates need this SAME filtered set on every tick, not just at
  // render time, or changing a filter without a fresh tick arriving
  // yet would leave the total row summing the wrong rows. Also applies
  // the current sort -- so "what's visible, in what order" is one
  // single source both the table body and the total row already agree
  // with, no separate re-sort needed anywhere else.
  _filteredRows() {
    const statusFilter = document.getElementById('filterStatus').value;
    const strategyFilter = document.getElementById('filterStrategy').value;
    const searchEl = document.getElementById('deploymentsSearch');
    const query = (searchEl ? searchEl.value : '').trim().toLowerCase();
    const rows = this._all.filter(d =>
      (!statusFilter || d.status === statusFilter) &&
      (!strategyFilter || d.strategy_name === strategyFilter) &&
      this._matchesSearch(d, query)
    );
    return this._sortRows(rows);
  },

  // ── Sorting (Step 93) — click a header to sort by it, click again to
  // reverse. No server round-trip; everything's already loaded. ──────
  setSort(key) {
    if (this._sortKey === key) {
      this._sortDir = this._sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      this._sortKey = key;
      this._sortDir = 'asc';
    }
    this.render();
  },
  _sortRows(rows) {
    const col = DEPLOY_COLUMNS.find(c => c.key === this._sortKey);
    if (!col || !col.sortValue) return rows;
    const dir = this._sortDir === 'desc' ? -1 : 1;
    // Slice first -- Array.sort mutates in place, and rows here is
    // already a freshly-filtered array so this is belt-and-suspenders,
    // not strictly needed, but cheap insurance against ever sorting
    // this._all itself by accident.
    return rows.slice().sort((a, b) => {
      const av = col.sortValue(a), bv = col.sortValue(b);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  },

  // ── Column visibility (Step 93) — persisted per-browser in
  // localStorage, same convention as SectionOrder (api.js) for
  // Dashboard/Reports' own reorderable sections: a standing preference,
  // not throwaway session state. ──────────────────────────────────────
  _colPrefsKey: 'deploymentsVisibleColumns',
  _loadColumnPrefs() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(this._colPrefsKey) || 'null'); }
    catch (e) { saved = null; }
    if (Array.isArray(saved)) {
      this._visibleCols = new Set(saved);
      // A column added in a later version than whoever's saved
      // preference this is should still show up by default, not
      // silently stay hidden forever just because it didn't exist yet
      // when they last customized this -- same "new stuff defaults
      // on" reasoning SectionOrder's own getOrder() already documents.
      DEPLOY_COLUMNS.forEach(c => {
        if (c.always) this._visibleCols.add(c.key);
      });
    } else {
      // Default view: decision-oriented columns first, not the full
      // accounting set -- Capital/Cash/Open Cost/Realized/Unrealized
      // are still there, just opt-in via the column selector.
      const defaults = ['ux_select', 'name', 'strategy', 'status', 'mode', 'ux_current_pnl', 'ux_open_positions', 'ux_last_action', 'actions'];
      this._visibleCols = new Set(defaults.filter(k => DEPLOY_COLUMNS.some(c => c.key === k)));
    }
  },
  _saveColumnPrefs() {
    localStorage.setItem(this._colPrefsKey, JSON.stringify([...this._visibleCols]));
  },
  toggleColumnMenu(event) {
    event.stopPropagation();
    const menu = document.getElementById('columnMenu');
    const willOpen = !menu.classList.contains('open');
    this._closeAllMenus();
    if (willOpen) {
      menu.innerHTML = `
        ${DEPLOY_COLUMNS.filter(c => !c.always).map(c => `
          <label>
            <input type="checkbox" ${this._visibleCols.has(c.key) ? 'checked' : ''}
                   onchange="Deployments.toggleColumn('${c.key}', this.checked)">
            ${escapeHtml(c.label)}
          </label>
        `).join('')}
        <div class="col-selector-footer">
          <button class="btn btn-secondary btn-sm" style="width:100%;" onclick="Deployments.resetColumns()">Reset to default</button>
        </div>
      `;
      menu.classList.add('open');
    }
  },
  toggleColumn(key, visible) {
    if (visible) this._visibleCols.add(key);
    else this._visibleCols.delete(key);
    this._saveColumnPrefs();
    this.render();
  },
  resetColumns() {
    this._visibleCols = new Set(DEPLOY_COLUMNS.map(c => c.key));
    this._saveColumnPrefs();
    document.getElementById('columnMenu').classList.remove('open');
    this.render();
  },

  // ── Per-row "⋯" action menu (Step 93) — replaces a row of always-
  // visible Pause/Resume/Stop/Delete buttons with one compact trigger.
  // Only ever one open at a time, same as the column-visibility menu
  // above; both close on any outside click via the SAME document-level
  // listener (_initDeploymentsMenuDismissal, wired once below). ───────
  toggleRowMenu(event, id) {
    event.stopPropagation();
    const dropdown = document.getElementById(`rowMenu-${id}`);
    if (!dropdown) return;
    const willOpen = dropdown.id !== this._openRowMenuId || !dropdown.classList.contains('open');
    this._closeAllMenus();
    if (willOpen) {
      dropdown.classList.add('open');
      this._openRowMenuId = dropdown.id;
    }
  },
  _closeAllMenus() {
    document.querySelectorAll('.row-menu-dropdown.open').forEach(el => el.classList.remove('open'));
    const colMenu = document.getElementById('columnMenu');
    if (colMenu) colMenu.classList.remove('open');
    this._openRowMenuId = null;
  },

  // ── CSV export (Step 93) — exactly the currently filtered + sorted
  // rows, exactly the currently VISIBLE columns (Actions excluded --
  // a row-menu control has no meaningful CSV value). Same toCsv/
  // downloadCsv helpers every other export in this app already uses
  // (Detail's trades export, Reports' trend export). ──────────────────
  exportCsv() {
    const rows = this._filteredRows();
    if (!rows.length) return;
    const columns = DEPLOY_COLUMNS
      .filter(c => c.key !== 'actions' && this._visibleCols.has(c.key))
      .map(c => ({
        label: c.label,
        key: c.csvValue || c.sortValue || (d => d[c.key]),
      }));
    const csv = toCsv(rows, columns);
    downloadCsv('deployments.csv', csv);
  },

  render() {
    const el = document.getElementById('deploymentsTable');
    const rows = this._filteredRows();

    if (!this._all.length) {
      el.innerHTML = emptyHtml('No deployments yet. Deploy a strategy from the Catalog to create one.');
      return;
    }
    if (!rows.length) {
      el.innerHTML = emptyHtml('No deployments match the current filters/search.');
      return;
    }

    const cols = DEPLOY_COLUMNS.filter(c => c.always || this._visibleCols.has(c.key));

    // Totals row (tfoot) -- scoped to whatever's currently filtered,
    // not always every deployment (see _filteredRows()'s own comment),
    // AND excluding include_in_reports=false same as before.
    const reportRows = rows.filter(d => d.include_in_reports);
    const excludedCount = rows.length - reportRows.length;
    const totalLabel = excludedCount > 0
      ? `Total (${reportRows.length} of ${rows.length} shown — ${excludedCount} excluded)`
      : `Total (${rows.length} shown)`;
    // Handed to each column's own total(ctx) -- see DEPLOY_COLUMNS'
    // own comment on why this is column-owned rather than a hardcoded
    // lookup keyed by column, and why reportRows (not the full,
    // possibly analytics-excluded `rows`) is what the accounting
    // columns (capital/cash/realized/...) total against, unchanged
    // from before.
    const totalCtx = { reportRows, allRows: rows };
    // The "Total (...)" label lives in the FIRST non-numeric,
    // non-actions column's own cell (in practice "Name" -- always:true,
    // so always present) rather than a colspan-merged spacer covering
    // several columns: the generic table framework's column drag/
    // resize/hide (UIKit.enhanceTable) reorders tfoot cells by
    // `data-ux-col-key`, one real cell per
    // column below, and a colspan cell can only ever move as one
    // indivisible block -- it can't stay correctly aligned once a
    // single column inside its span gets dragged out on its own.
    const labelCol = cols.find(c => !c.numeric && c.key !== 'actions' && c.key !== 'ux_select') || cols[0];

    el.innerHTML = `
      <div class="table-wrap">
      <table class="deploy-table"><thead><tr>
        ${cols.map(c => {
          if (c.sortable === false) return `<th${c.numeric ? ' class="text-right"' : ''} data-ux-col-key="${escapeHtml(c.key)}">${escapeHtml(c.label)}</th>`;
          const isSorted = this._sortKey === c.key;
          const arrow = isSorted ? (this._sortDir === 'asc' ? '▲' : '▼') : '▲';
          return `<th class="sortable${c.numeric ? ' text-right' : ''}" data-ux-col-key="${escapeHtml(c.key)}" onclick="Deployments.setSort('${c.key}')"
                      ${c.headerTitle ? `title="${escapeHtml(c.headerTitle)}"` : ''}
                      aria-sort="${isSorted ? (this._sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}">
                    ${escapeHtml(c.label)}<span class="sort-arrow${isSorted ? ' active' : ''}">${arrow}</span>
                  </th>`;
        }).join('')}
      </tr></thead>
      <tbody>${rows.map(d => `
        <tr class="clickable-row" data-deployment-id="${d.id}" onclick="location.hash='#/deployments/${d.id}'">
          ${cols.map(c => {
            const isActions = c.key === 'actions';
            return `<td${c.numeric ? ' class="text-right"' : ''}${isActions ? ' onclick="event.stopPropagation()"' : ''}>${c.render(d)}</td>`;
          }).join('')}
        </tr>
      `).join('')}</tbody>
      <tfoot><tr class="positions-total-row">
        ${cols.map(c => {
          if (c.key === 'actions') return '<td data-ux-col-key="actions"></td>';
          if (c === labelCol) return `<td data-ux-col-key="${escapeHtml(c.key)}"><b>${totalLabel}</b></td>`;
          if (c.numeric) return `<td class="text-right" data-ux-col-key="${escapeHtml(c.key)}">${c.total ? c.total(totalCtx) : ''}</td>`;
          return `<td data-ux-col-key="${escapeHtml(c.key)}"></td>`;
        }).join('')}
      </tr></tfoot>
      </table>
      </div>
    `;
    requestAnimationFrame(() => {
      this.ensureQuickFilters();
      UIKit.renameAnalyticsSemantics(document.getElementById('view-deployments') || document);
      UIKit.enhanceTablesSoon();
    });
  },

  async pause(id) {
    await Api.pauseDeployment(id);
    this.load();
  },
  async resume(id) {
    // Can now genuinely fail (409) if config was edited while paused
    // into something the strategy's own on_start() rejects -- see
    // DeploymentManager.resume's rollback-to-paused comment. Same
    // ok-check pattern stop() already uses below.
    const r = await Api.resumeDeployment(id);
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      alert(data.detail || 'Could not resume — check its config on the Detail page.');
    }
    this.load();
  },
  async stop(id) {
    const dep = this._all.find(d => d.id === id);
    return UIKit.openStopDialog(id, dep?.deployment_name);
  },
  async deleteDeployment(id) {
    // Only ever offered while stopped (see the row's own status check)
    // -- the backend enforces the same restriction independently
    // either way. Permanent: every position/trade/event/snapshot under
    // this deployment goes with it, via ON DELETE CASCADE. Looks the
    // name up from the already-loaded list rather than threading it
    // through the onclick attribute (no string-escaping to get wrong).
    const dep = this._all.find(d => d.id === id);
    const name = dep ? dep.deployment_name : 'this deployment';
    const ok = confirm(
      `Permanently delete "${name}"?\n\nThis removes ALL of its positions, trades, and history — ` +
      `not just the deployment itself. This cannot be undone.`
    );
    if (!ok) return;
    const r = await Api.deleteDeployment(id);
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      alert(data.detail || 'Could not delete this deployment.');
      return;
    }
    this.load();
  },

  // ── Flatten all: the panic button. Closes positions only, touches no
  // history (unlike Clear All), so a plain confirm() is proportionate —
  // no password/typed-confirmation gate needed for something you can
  // recover from by just redeploying/resuming. ──────────────────────
  async submitFlattenAll() {
    if (!confirm(
      'Close every open position across every deployment at the last known price, ' +
      'then pause whichever were active.\n\nDeployments themselves are not stopped or ' +
      'deleted — you can resume any of them afterward. Continue?'
    )) return;
    const { ok, data } = await Api.flattenAll();
    if (!ok) {
      alert(data.detail || 'Could not flatten — see server logs.');
      return;
    }
    let msg = `Checked ${data.deployments_checked} deployment(s): ` +
      `${data.positions_closed} position(s) closed across ${data.deployments_flattened} deployment(s).`;
    if (data.errors && data.errors.length) {
      msg += `\n\n${data.errors.length} deployment(s) failed to flatten:\n` +
        data.errors.map(e => `- ${e.deployment_name}: ${e.error}`).join('\n');
    }
    alert(msg);
    this.load();
  },

  // ── Compare-selection tray (Step: ux_select column) — check up to 6
  // rows here, then "Compare" hands them straight to the Compare view
  // pre-selected instead of re-picking them one by one over there. ────
  toggleSelection(id, checked) {
    if (checked) {
      if (this._selectedIds.size >= 6) {
        alert('Compare supports up to 6 deployments at a time.');
        this.render();
        return;
      }
      this._selectedIds.add(id);
    } else this._selectedIds.delete(id);
    this.updateSelectionTray();
  },

  ensureSelectionTray() {
    let tray = document.getElementById('uxSelectionTray');
    if (!tray) {
      tray = document.createElement('div');
      tray.id = 'uxSelectionTray';
      tray.className = 'ux-selection-tray';
      tray.innerHTML = `<strong id="uxSelectionCount">0 selected</strong><div style="display:flex;gap:7px;"><button class="btn btn-secondary btn-sm" onclick="Deployments.clearSelection()">Clear</button><button class="btn btn-primary btn-sm" onclick="Deployments.compareSelection()">Compare</button></div>`;
      document.body.appendChild(tray);
    }
    return tray;
  },

  updateSelectionTray() {
    const tray = this.ensureSelectionTray();
    tray.classList.toggle('open', this._selectedIds.size >= 1);
    document.getElementById('uxSelectionCount').textContent = `${this._selectedIds.size} selected`;
    tray.querySelector('.btn-primary').disabled = this._selectedIds.size < 2;
  },

  clearSelection() {
    this._selectedIds.clear();
    this.updateSelectionTray();
    this.render();
  },

  compareSelection() {
    if (this._selectedIds.size < 2) return;
    sessionStorage.setItem('uxCompareSelection', JSON.stringify([...this._selectedIds]));
    window.location.hash = '#/compare';
  },

  // ── Quick status-filter chips + the "Table ▾" settings button, both
  // inserted above the existing filter row rather than replacing it. ──
  ensureQuickFilters() {
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
    const counts = { '': this._all.length, active: 0, paused: 0, stopped: 0 };
    this._all.forEach(d => { counts[d.status] = (counts[d.status] || 0) + 1; });
    const current = document.getElementById('filterStatus')?.value || '';
    chips.innerHTML = [
      ['', 'All'], ['active', 'Active'], ['paused', 'Paused'], ['stopped', 'Stopped'],
    ].map(([value, label]) => `<button class="ux-filter-chip ${current === value ? 'active' : ''}" onclick="Deployments.setStatusFilter('${value}')">${label} ${counts[value] || 0}</button>`).join('');

    if (!document.getElementById('uxDeploymentTableSettings')) {
      const colWrap = document.getElementById('colSelectorWrap');
      if (colWrap) {
        const btn = document.createElement('button');
        btn.id = 'uxDeploymentTableSettings';
        btn.className = 'btn btn-secondary btn-sm';
        btn.textContent = 'Table ▾';
        btn.onclick = e => UIKit.openTableSettings(e, 'deploymentsTable');
        colWrap.parentNode.insertBefore(btn, colWrap.nextSibling);
      }
    }
  },

  setStatusFilter(value) {
    const select = document.getElementById('filterStatus');
    if (select) select.value = value;
    this.render();
    this.ensureQuickFilters();
  },

  // ── List state (filter/sort/scroll) — captured when navigating AWAY
  // to a deployment's own Detail page, restored on the next load() so
  // "back" lands where you left off instead of a reset list. Captured
  // from index.html's own hashchange listener (deployments -> detail
  // is a cross-page transition, not something either page's own code
  // sees on its own). ──────────────────────────────────────────────────
  saveListState() {
    sessionStorage.setItem('uxDeploymentListState', JSON.stringify({
      status: document.getElementById('filterStatus')?.value || '',
      strategy: document.getElementById('filterStrategy')?.value || '',
      search: document.getElementById('deploymentsSearch')?.value || '',
      sortKey: this._sortKey,
      sortDir: this._sortDir,
      scrollY: window.scrollY,
    }));
  },

  restoreListState() {
    const state = safeJsonParse(sessionStorage.getItem('uxDeploymentListState') || 'null', null);
    if (!state) return;
    const status = document.getElementById('filterStatus');
    const strategy = document.getElementById('filterStrategy');
    const search = document.getElementById('deploymentsSearch');
    if (status) status.value = state.status || '';
    if (strategy && [...strategy.options].some(o => o.value === state.strategy)) strategy.value = state.strategy || '';
    if (search) search.value = state.search || '';
    if (state.sortKey) this._sortKey = state.sortKey;
    if (state.sortDir) this._sortDir = state.sortDir;
    this.render();
    requestAnimationFrame(() => window.scrollTo(0, Number(state.scrollY || 0)));
  },
};

// Dismiss the column-visibility menu or an open row action menu on any
// click outside them -- one delegated listener, wired once (idempotent
// against this file somehow loading twice), same "init once" idiom
// Step 88's ChartTooltip delegation already established in api.js.
function _initDeploymentsMenuDismissal() {
  if (window._deploymentsMenuDismissalInit) return;
  window._deploymentsMenuDismissalInit = true;
  document.addEventListener('click', (e) => {
    if (e.target.closest('.row-menu, .col-selector')) return;
    Deployments._closeAllMenus();
  });
}
_initDeploymentsMenuDismissal();
