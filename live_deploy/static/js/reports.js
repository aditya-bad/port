// live_deploy — Reports: a single-period drill-down (stat cards vs the
// previous period, By Strategy / By Deployment breakdowns, a trend of
// recent periods), navigated day-by-day/week-by-week/month-by-month
// like a statement, rather than one long scrolling history the way
// Portfolio/Compare are. Deliberately REALIZED P&L only throughout —
// see the backend's own queries.list_pnl_digest docstring for why a
// live unrealized number has no honest place in a report of settled
// periods. See README's Step 41 for the full feature rationale.

const Reports = {
  _period: 'day',
  _offset: 0,
  _trendRows: [],   // kept around for exportCsv()
  _report: null,        // last-loaded report (r), kept for filterByStrategy's re-render
  _depModeById: {},      // deployment_id -> 'intraday'|'positional', for the By Deployment mode tag
  _strategyFilter: null, // strategy_name currently filtering By Deployment, or null

  // Calendar heatmap's own range state (Step 74) -- see dashboard.js's
  // identical field for the full reasoning; this view's calendar is
  // its own independent control, same as it's already independent of
  // the Daily/Weekly/Monthly period nav above it.
  _calendarRange: 'recent',

  // Default order / full valid-id set for the reorderable sections
  // below the period nav -- see SectionOrder (api.js).
  _sectionIds: ['reportsSectionStrategy', 'reportsSectionDeployment', 'reportsSectionTrend', 'reportsSectionCalendar'],

  async load() {
    const order = SectionOrder.getOrder('reports', this._sectionIds);
    SectionOrder.apply(document.getElementById('reportsSections'), order);
    SectionOrder.syncButtons(order);

    document.getElementById('reportsStats').innerHTML = spinnerHtml();
    document.getElementById('reportsByStrategy').innerHTML = spinnerHtml();
    document.getElementById('reportsByDeployment').innerHTML = spinnerHtml();
    document.getElementById('reportsTrend').innerHTML = spinnerHtml();
    document.getElementById('reportsCalendar').innerHTML = spinnerHtml();
    document.getElementById('reportsNextBtn').disabled = this._offset === 0;
    document.getElementById('reportsLatestBtn').disabled = this._offset === 0;

    this._restoreSectionState();

    // The calendar is portfolio-wide and always DAILY, independent of
    // the Daily/Weekly/Monthly tabs and Prev/Next nav above it -- it
    // re-fetches on every load() same as everything else here for
    // simplicity, not because its own data depends on this._period/
    // this._offset (it never does).
    const [report, trend, calendarRows, deployments] = await Promise.all([
      Api.getPnlReport(this._period, this._offset),
      Api.getPnlDigest(this._period, 14),
      this._fetchCalendarRows(),
      Api.listDeployments(),
    ]);
    this._depModeById = Object.fromEntries((deployments || []).map(d => [d.id, d.mode]));

    document.getElementById('reportsPeriodLabel').textContent = report.label;
    this._report = report;
    this.renderStats(report);
    this.renderByStrategy(report);
    this.renderByDeployment(report);
    this._trendRows = trend;
    this.renderTrend(trend);
    this.renderCalendar(calendarRows);

    // The reorderable sections below the period nav use their own
    // drag-handle layout, on top of the arrow-button SectionOrder
    // restore above -- see moveSection()'s own comment for why the
    // arrow buttons stay as a keyboard/accessibility fallback.
    UIKit.applySavedLayout('reportsSections', 'reports');
    UIKit.setupSortableSections('reportsSections', 'reports');
    await this._renderContributionChart();
    UIKit.enhanceTablesSoon();
  },

  // Lead with a visual contribution read, keep the detailed table
  // immediately below for exact values/export-minded scanning.
  async _renderContributionChart() {
    const target = document.getElementById('reportsByStrategy');
    if (!target) return;
    try {
      const report = await Api.getPnlReport(this._period, this._offset);
      const rows = (report.by_strategy || []).slice().sort((a, b) => Math.abs(Number(b.realized_pnl || 0)) - Math.abs(Number(a.realized_pnl || 0)));
      if (!rows.length) return;
      const maxAbs = Math.max(...rows.map(r => Math.abs(Number(r.realized_pnl || 0))), 1);
      const visual = `<div class="ux-report-contrib"><div class="ux-card-head"><strong>P&amp;L contribution</strong><span class="card-sub">Visual first; exact table below</span></div><div class="ux-bar-list">${rows.map(r => `<div class="ux-bar-row"><span>${escapeHtml(r.strategy_name)}</span><div class="ux-bar-track"><div class="ux-bar-fill ${Number(r.realized_pnl || 0) < 0 ? 'neg' : ''}" style="width:${Math.abs(Number(r.realized_pnl || 0)) / maxAbs * 100}%"></div></div><b class="${pnlClass(r.realized_pnl)}">${fmtSignedMoney(r.realized_pnl)}</b></div>`).join('')}</div></div>`;
      target.insertAdjacentHTML('afterbegin', visual);
    } catch (e) { console.warn('Report contribution chart failed', e); }
  },

  // ── Calendar heatmap range (Step 74) -- see dashboard.js's identical
  // trio of methods for the full reasoning, unchanged here. ──────────
  _fetchCalendarRows() {
    return this._calendarRange === 'recent'
      ? Api.getPnlDigest('day', 371)
      : Api.getPnlDigest('day', 371, this._calendarRange);
  },

  renderCalendar(rows) {
    const year = this._calendarRange === 'recent' ? null : this._calendarRange;
    document.getElementById('reportsCalendar').innerHTML = renderPnlHeatmap(rows, {
      year,
      selector: { value: this._calendarRange, onChange: 'Reports.changeCalendarRange(this.value)' },
      onDayClick: 'Reports.openCalendarDay',
    });
    scrollPnlHeatmapToEnd('reportsCalendar');
  },

  async changeCalendarRange(value) {
    this._calendarRange = value === 'recent' ? 'recent' : Number(value);
    const rows = await this._fetchCalendarRows();
    this.renderCalendar(rows);
  },

  // Jumps the Daily/Weekly/Monthly drill-down above straight to the
  // clicked calendar day -- always switches to the 'day' period (the
  // calendar itself is always daily, see this view's own _calendarRange
  // comment), computed as an offset from today the same way
  // period_bounds() on the backend does: whole IST calendar days back
  // from "now", so offset 0 really is today.
  openCalendarDay(dateIso) {
    const today = new Date(nowIstDateKey() + 'T00:00:00Z').getTime();
    const clicked = new Date(dateIso + 'T00:00:00Z').getTime();
    const offset = Math.round((today - clicked) / 86_400_000);
    if (offset < 0) return;   // padding cell past "today" -- shouldn't be clickable, but stay safe
    this._period = 'day';
    this._offset = offset;
    document.querySelectorAll('#reportsPeriodTabs button').forEach(b =>
      b.classList.toggle('active', b.dataset.period === 'day'));
    this.load();
  },

  // Keyboard/accessibility fallback -- the visible arrow buttons this
  // used to drive are hidden by CSS now that the section header has a
  // real drag handle (UIKit.setupSortableSections), but this stays
  // callable (Alt+↑/↓ on the handle wires to the same drag-handle
  // layout key, see UIKit.setupSortableSections' own keydown handler)
  // and writes to that SAME layout key rather than SectionOrder's.
  moveSection(id, delta) {
    const container = document.getElementById('reportsSections');
    const item = document.getElementById(id);
    if (!container || !item) return;
    const children = [...container.children];
    const index = children.indexOf(item);
    const target = index + delta;
    if (target < 0 || target >= children.length) return;
    if (delta < 0) container.insertBefore(item, children[target]);
    else container.insertBefore(children[target], item);
    localStorage.setItem(UIKit.layoutKey('reports'), JSON.stringify([...container.children].map(x => x.id)));
  },

  switchPeriod(period) {
    this._period = period;
    this._offset = 0;
    document.querySelectorAll('#reportsPeriodTabs button').forEach(b =>
      b.classList.toggle('active', b.dataset.period === period));
    this.load();
  },

  step(delta) {
    // delta=+1 -> Prev (further into the past); delta=-1 -> Next
    // (toward the present) -- offset can never go negative (offset=0
    // IS the present, there's no "future period" to step into).
    const next = this._offset + delta;
    if (next < 0) return;
    this._offset = next;
    this.load();
  },

  jumpToLatest() {
    if (this._offset === 0) return;
    this._offset = 0;
    this.load();
  },

  renderStats(r) {
    const el = document.getElementById('reportsStats');
    const delta = r.realized_pnl - r.prev_realized_pnl;
    const deltaPct = r.prev_realized_pnl !== 0 ? (delta / Math.abs(r.prev_realized_pnl)) * 100 : null;
    const totalDecided = r.wins + r.losses;
    const winRate = totalDecided > 0 ? (r.wins / totalDecided) * 100 : 0;

    el.innerHTML = `
      <div class="stat-card">
        <div class="stat-label">Realized P&amp;L</div>
        <div class="stat-value ${pnlClass(r.realized_pnl)}">${fmtSignedMoney(r.realized_pnl)}</div>
        <div class="report-delta ${pnlClass(delta)}">
          ${delta >= 0 ? '▲' : '▼'} ${fmtSignedMoney(delta)}${deltaPct !== null ? ` (${deltaPct >= 0 ? '+' : ''}${deltaPct.toFixed(1)}%)` : ''}
          <span style="color:var(--parchment); font-weight:500;">vs previous period</span>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Positions Closed</div>
        <div class="stat-value">${r.positions_closed}</div>
        <div class="stat-sub">
          <div class="row"><span>Wins</span><b class="pos">${r.wins}</b></div>
          <div class="row"><span>Losses</span><b class="neg">${r.losses}</b></div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Win Rate</div>
        <div class="stat-value">${totalDecided > 0 ? winRate.toFixed(1) + '%' : '—'}</div>
        <div class="report-winrate-track"><div class="report-winrate-fill" style="width:${winRate}%"></div></div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Fills (entries + exits)</div>
        <div class="stat-value">${r.fills}</div>
        <div class="stat-sub">Across every deployment this period</div>
      </div>
    `;
  },

  renderByStrategy(r) {
    const el = document.getElementById('reportsByStrategy');
    if (!r.by_strategy.length) {
      el.innerHTML = emptyHtml('No positions closed by any strategy in this period.');
      return;
    }
    const total = r.by_strategy.reduce((s, row) => s + Math.abs(row.realized_pnl), 0) || 1;
    el.innerHTML = `
      <div class="table-wrap">
      <table><thead><tr>
        <th>Strategy</th><th>Realized P&amp;L</th><th>% of total</th><th>Positions closed</th>
      </tr></thead>
      <tbody>${r.by_strategy.map(row => `<tr class="clickable-row ${this._strategyFilter === row.strategy_name ? 'active-row' : ''}" data-strategy="${escapeHtml(row.strategy_name)}" tabindex="0" onclick="Reports.filterByStrategy('${escapeHtml(row.strategy_name)}')" title="Filter By Deployment to this strategy">
        <td>${escapeHtml(row.strategy_name)}</td>
        <td class="${pnlClass(row.realized_pnl)}">${fmtSignedMoney(row.realized_pnl)}</td>
        <td>${((Math.abs(row.realized_pnl) / total) * 100).toFixed(1)}%</td>
        <td>${row.positions_closed}</td>
      </tr>`).join('')}</tbody></table>
      </div>
    `;
  },

  // Strategy rows have no detail page of their own to link to -- filter
  // the SAME report's By Deployment table down to that strategy's
  // deployments instead, entirely client-side (r.by_deployment is
  // already fully loaded, no second request). Clicking the same
  // strategy again clears the filter, same toggle feel as a lot of
  // this app's other filter chips.
  //
  // Deliberately does NOT re-render #reportsByStrategy wholesale --
  // _renderContributionChart() prepends its own chart markup into that
  // same container via insertAdjacentHTML, outside renderByStrategy()'s
  // own innerHTML, so a full re-render here would silently discard it.
  // Toggling the .active-row class directly is enough to reflect the
  // selection.
  filterByStrategy(strategyName) {
    this._strategyFilter = this._strategyFilter === strategyName ? null : strategyName;
    this._markActiveStrategyRow();
    if (this._report) this.renderByDeployment(this._report);
  },
  clearStrategyFilter() {
    this._strategyFilter = null;
    this._markActiveStrategyRow();
    if (this._report) this.renderByDeployment(this._report);
  },
  _markActiveStrategyRow() {
    document.querySelectorAll('#reportsByStrategy tr[data-strategy]').forEach(tr =>
      tr.classList.toggle('active-row', tr.dataset.strategy === this._strategyFilter));
  },

  // Reverse of openDeploymentForPeriod below -- Detail's History tab
  // (paintHistoryPositions, detail.js) calls this on a closed cycle
  // card's own settle date to jump back to the Daily report it counted
  // toward. Always a real hash change (this is only ever invoked from
  // a #/deployments/... hash), so the router's own `view === 'reports'
  // -> Reports.load()` handles the actual fetch+render -- no direct
  // load() call needed here, same as openDeploymentForPeriod/
  // Detail.openMatrixMonth's identical "set state, then navigate"
  // shape.
  openForDate(dateIso) {
    const today = new Date(nowIstDateKey() + 'T00:00:00Z').getTime();
    const target = new Date(dateIso + 'T00:00:00Z').getTime();
    this._period = 'day';
    this._offset = Math.max(0, Math.round((today - target) / 86_400_000));
    // Keep the Daily/Weekly/Monthly tab pills in sync with the period
    // just set above -- load() itself never touches them (only the
    // click handlers that change _period normally do), and the DOM
    // persists across view switches in this SPA, so a stale Weekly/
    // Monthly pill from an earlier Reports visit this session would
    // otherwise linger highlighted even once this lands on Daily data.
    document.querySelectorAll('#reportsPeriodTabs button').forEach(b =>
      b.classList.toggle('active', b.dataset.period === 'day'));
    window.location.hash = '#/reports';
  },

  // Jumps into the clicked deployment's History tab, pre-filtered to
  // the EXACT Daily/Weekly/Monthly window this report row came from --
  // same "_historyRange + navigate" mechanism Detail's own Analytics
  // matrix already uses (openMatrixMonth/openMatrixYear), so this reuses
  // Detail.paintHistoryPositions()'s existing range-filter logic rather
  // than adding a second one.
  openDeploymentForPeriod(depId, r) {
    Detail._historyRange = { start: r.period_start, end: r.period_end, label: r.label };
    Detail._historyMode = 'positions';
    window.location.hash = `#/deployments/${depId}/history`;
  },

  renderByDeployment(r) {
    const el = document.getElementById('reportsByDeployment');
    const rows = this._strategyFilter
      ? r.by_deployment.filter(row => row.strategy_name === this._strategyFilter)
      : r.by_deployment;
    const filterChip = this._strategyFilter
      ? `<span class="ux-history-filter-chip">${escapeHtml(this._strategyFilter)} <button class="btn btn-secondary btn-sm" style="padding:1px 5px;" onclick="Reports.clearStrategyFilter()">✕</button></span>`
      : '';
    if (!rows.length) {
      el.innerHTML = (filterChip ? `<div style="margin-bottom:8px;">${filterChip}</div>` : '') +
        emptyHtml(this._strategyFilter ? `No deployments running "${escapeHtml(this._strategyFilter)}" closed positions in this period.` : 'No positions closed by any deployment in this period.');
      return;
    }
    el.innerHTML = `
      ${filterChip ? `<div style="margin-bottom:8px;">${filterChip}</div>` : ''}
      <div class="table-wrap">
      <table><thead><tr>
        <th>Deployment</th><th>Mode</th><th>Strategy</th>
        <th>Realized P&amp;L <span data-tooltip="Positional cycles may span more than one period; each period shows only what settled within it.">ⓘ</span></th>
        <th>Positions closed</th>
      </tr></thead>
      <tbody>${rows.map(row => `<tr class="clickable-row" tabindex="0" onclick="Reports.openDeploymentForPeriod('${row.deployment_id}', Reports._report)">
        <td>${escapeHtml(row.deployment_name)}</td>
        <td><span class="tag tag-info">${escapeHtml(this._depModeById[row.deployment_id] || '—')}</span></td>
        <td>${escapeHtml(row.strategy_name)}</td>
        <td class="${pnlClass(row.realized_pnl)}">${fmtSignedMoney(row.realized_pnl)}</td>
        <td>${row.positions_closed}</td>
      </tr>`).join('')}</tbody></table>
      </div>
    `;
  },

  _periodLabel(iso) {
    if (this._period === 'week') return `Week of ${fmtDate(iso)}`;
    if (this._period === 'month') {
      const d = new Date(iso);
      return isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-IN', { year: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' });
    }
    return fmtDate(iso);
  },

  renderTrend(rows) {
    document.getElementById('reportsTrend').innerHTML =
      renderPnlTrendTable(rows, { periodLabel: iso => this._periodLabel(iso) });
  },

  // ── Collapsible sections — instant show/hide, persisted per-section
  // in localStorage so a section collapsed once stays that way across
  // reloads (unlike Compare's picker state, which is genuinely
  // per-session -- a collapsed report section is a standing
  // preference, not throwaway UI state). ─────────────────────────────
  _collapseKey(sectionId) {
    return `reportSectionCollapsed:${sectionId}`;
  },
  toggleSection(sectionId) {
    const el = document.getElementById(sectionId);
    const collapsed = el.classList.toggle('collapsed');
    localStorage.setItem(this._collapseKey(sectionId), collapsed ? '1' : '0');
  },
  _restoreSectionState() {
    ['reportsSectionStrategy', 'reportsSectionDeployment', 'reportsSectionTrend', 'reportsSectionCalendar'].forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.toggle('collapsed', localStorage.getItem(this._collapseKey(id)) === '1');
    });
  },

  // Exports the "Recent Periods" trend table currently on screen (up
  // to 14 periods) -- the single-period drill-down above it is
  // already fully visible on the page itself, so exporting it as a
  // one-row CSV would add a file for no real benefit; the trend is
  // the part actually worth taking out of the app.
  exportCsv() {
    if (!this._trendRows.length) return;
    const csv = toCsv(this._trendRows, [
      { key: row => this._periodLabel(row.period_start), label: 'Period' },
      { key: 'realized_pnl', label: 'Realized PnL' },
      { key: 'positions_closed', label: 'Positions Closed' },
      { key: 'wins', label: 'Wins' },
      { key: 'losses', label: 'Losses' },
      { key: 'fills', label: 'Fills' },
    ]);
    downloadCsv(`pnl_report_${this._period}.csv`, csv);
  },
};
