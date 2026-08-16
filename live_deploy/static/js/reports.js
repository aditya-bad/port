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
    const [report, trend, calendarRows] = await Promise.all([
      Api.getPnlReport(this._period, this._offset),
      Api.getPnlDigest(this._period, 14),
      Api.getPnlDigest('day', 371),
    ]);

    document.getElementById('reportsPeriodLabel').textContent = report.label;
    this.renderStats(report);
    this.renderByStrategy(report);
    this.renderByDeployment(report);
    this._trendRows = trend;
    this.renderTrend(trend);
    document.getElementById('reportsCalendar').innerHTML = renderPnlHeatmap(calendarRows);
  },

  moveSection(id, delta) {
    const order = SectionOrder.move('reports', this._sectionIds, id, delta);
    SectionOrder.apply(document.getElementById('reportsSections'), order);
    SectionOrder.syncButtons(order);
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
      <tbody>${r.by_strategy.map(row => `<tr>
        <td>${escapeHtml(row.strategy_name)}</td>
        <td class="${pnlClass(row.realized_pnl)}">${fmtSignedMoney(row.realized_pnl)}</td>
        <td>${((Math.abs(row.realized_pnl) / total) * 100).toFixed(1)}%</td>
        <td>${row.positions_closed}</td>
      </tr>`).join('')}</tbody></table>
      </div>
    `;
  },

  renderByDeployment(r) {
    const el = document.getElementById('reportsByDeployment');
    if (!r.by_deployment.length) {
      el.innerHTML = emptyHtml('No positions closed by any deployment in this period.');
      return;
    }
    el.innerHTML = `
      <div class="table-wrap">
      <table><thead><tr>
        <th>Deployment</th><th>Strategy</th><th>Realized P&amp;L</th><th>Positions closed</th>
      </tr></thead>
      <tbody>${r.by_deployment.map(row => `<tr class="clickable-row" tabindex="0" onclick="location.hash='#/deployments/${row.deployment_id}'">
        <td>${escapeHtml(row.deployment_name)}</td>
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
      return isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-IN', { year: 'numeric', month: 'short' });
    }
    return fmtDate(iso);
  },

  renderTrend(rows) {
    const el = document.getElementById('reportsTrend');
    if (!rows.length) {
      el.innerHTML = emptyHtml('No closed positions recorded yet.');
      return;
    }
    const maxAbs = Math.max(...rows.map(r => Math.abs(r.realized_pnl)), 1);
    el.innerHTML = `
      <div class="table-wrap">
      <table><thead><tr>
        <th>Period</th><th>Realized P&amp;L</th><th>Positions closed</th><th>Win rate</th><th>Fills</th>
      </tr></thead>
      <tbody>${rows.map(row => {
        const pct = (Math.abs(row.realized_pnl) / maxAbs) * 50;   // 50% = half the track, since the bar grows from a CENTER zero-line
        const decided = row.wins + row.losses;
        const winRate = decided > 0 ? ((row.wins / decided) * 100).toFixed(0) + '%' : '—';
        return `<tr>
          <td>${this._periodLabel(row.period_start)}</td>
          <td>
            <div class="report-row-value">
              <div class="report-bar-track">
                <div class="report-bar-zero"></div>
                <div class="report-bar-fill ${row.realized_pnl >= 0 ? 'gain' : 'loss'}" style="width:${pct}%"></div>
              </div>
              <span class="${pnlClass(row.realized_pnl)}">${fmtSignedMoney(row.realized_pnl)}</span>
            </div>
          </td>
          <td>${row.positions_closed}</td>
          <td>${winRate}</td>
          <td>${row.fills}</td>
        </tr>`;
      }).join('')}</tbody></table>
      </div>
    `;
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
