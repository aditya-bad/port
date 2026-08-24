// live_deploy — Strategy Detail view: the deep-dive for one deployment.
// Header + 4 tabs (Overview / Analytics / History / Configuration).
// Overview is the operational "what's happening right now" landing tab
// (current position/cycle, live strategy state, a performance snapshot,
// recent activity). Analytics is the deep performance dive — the
// original per-trade/per-position stat cards and trigger-reason
// breakdown, PLUS a yearly Monthly Performance matrix, a P&L
// distribution histogram, a P&L-by-exit-reason chart, and an
// equity/drawdown curve with a range picker. History is every
// position/cycle, execution, and event ever recorded for this
// deployment, filterable by clicking a month/year in the Analytics
// matrix. Configuration is the read-only (while running) strategy
// config, editable only while paused — see DeploymentUpdate's own
// docstring (app/deployments/schemas.py) for why paused specifically is
// what makes editing safe at all.

const Detail = {
  _id: null,
  _dep: null,
  _tab: 'overview',
  _calendarRange: 'recent',   // Analytics tab's P&L Calendar range state (Step 74) -- reset per deployment below, persists across switching away from/back to Analytics for the SAME deployment
  _statsTrendPeriod: 'day',   // Analytics tab's Recent Periods bucketing (Step 86) -- same persistence rule as _calendarRange above
  _statsGranularity: 'position',   // Step 103 -- "per position" (episodes, every overlapping leg/adjustment/roll combined) vs "per trade" (raw positions rows); defaults to "position" per explicit user request

  _tabLabel: { overview: 'Overview', analytics: 'Analytics', history: 'History', configuration: 'Configuration' },

  // Reads the tab straight from the URL hash (#/deployments/{id}/{tab})
  // rather than tracking it purely as in-memory state -- switchTab()
  // below navigates by changing the hash, so a reload/back-button/
  // shared link all land on the right tab for free.
  _route() {
    const clean = (window.location.hash || '').replace(/^#\/?/, '');
    const parts = clean.split('/');
    if (parts[0] !== 'deployments' || !parts[1]) return { id: null, section: 'overview' };
    const section = ['overview', 'analytics', 'history', 'configuration'].includes(parts[2]) ? parts[2] : 'overview';
    return { id: parts[1], section };
  },

  async load(id) {
    this._stopLivePositionUpdates();   // leaving whatever deployment/tab was showing before
    this._id = id;
    this._calendarRange = 'recent';
    this._statsTrendPeriod = 'day';
    this._statsGranularity = 'position';
    const route = this._route();
    this._tab = route.section;
    document.getElementById('detailHeader').innerHTML = spinnerHtml();
    document.getElementById('detailTabs').innerHTML = '';
    document.getElementById('detailBody').innerHTML = spinnerHtml();

    try {
      this._dep = await Api.getDeployment(id);
    } catch (e) {
      document.getElementById('detailHeader').innerHTML =
        emptyHtml(`No such deployment (it may have been removed). <a href="#/deployments">Back to Deployed Strategies</a>`);
      document.getElementById('detailTabs').innerHTML = '';
      document.getElementById('detailBody').innerHTML = '';
      return;
    }
    this.renderHeader(this._dep);
    this.renderTabs();
    await this.renderBody();
  },

  renderHeader(dep) {
    const actions = [];
    if (dep.status === 'active') actions.push(`<button class="btn btn-primary btn-sm" onclick="Detail.pause()">Pause</button>`);
    if (dep.status === 'paused') actions.push(`<button class="btn btn-primary btn-sm" onclick="Detail.resume()">Resume</button>`);
    actions.push(`<button class="btn btn-secondary btn-sm" onclick="Detail.toggleMenu(event)">⋯ More</button>`);
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
  },

  toggleMenu(event) {
    event.stopPropagation();
    const dep = this._dep;
    UIKit.openPopover(event.currentTarget, `
      <button class="ux-menu-item" onclick="UIKit.closePopover(); Detail.openEditModal()">Edit details</button>
      ${dep.status === 'paused' ? '<button class="ux-menu-item" onclick="UIKit.closePopover(); Detail.openEditConfigModal()">Edit configuration</button>' : ''}
      <button class="ux-menu-item" onclick="UIKit.closePopover(); location.hash='#/deployments/${dep.id}/configuration'">View configuration</button>
      <div class="ux-menu-sep"></div>
      ${dep.status !== 'stopped' ? `<button class="ux-menu-item" style="color:var(--loss)" onclick="UIKit.closePopover(); UIKit.openStopDialog('${dep.id}', ${JSON.stringify(dep.deployment_name)})">Stop deployment</button>` : ''}
      ${dep.status === 'stopped' ? `<button class="ux-menu-item" style="color:var(--loss)" onclick="UIKit.closePopover(); Detail.deleteDeployment()">Delete deployment</button>` : ''}`);
  },

  renderTabs() {
    const tabs = ['overview', 'analytics', 'history', 'configuration'];
    const route = this._route();
    this._tab = route.section;
    const el = document.getElementById('detailTabs');
    el.className = 'tabs ux-detail-nav';
    el.innerHTML = tabs.map(t => `<button class="${route.section === t ? 'active' : ''}" onclick="Detail.switchTab('${t}')">${this._tabLabel[t]}</button>`).join('');
  },

  switchTab(tab) {
    this._stopLivePositionUpdates();
    window.location.hash = `#/deployments/${this._id}/${tab}`;
  },

  async renderBody() {
    const route = this._route();
    this._tab = route.section;
    document.getElementById('detailBody').classList.remove('ux-analytics');
    // A tab's own fetch can fail for real reasons (the deployment was
    // stopped/removed mid-session, a transient network/DB hiccup) — an
    // uncaught rejection here would otherwise leave the tab stuck on
    // its loading spinner forever with no visible explanation.
    try {
      if (this._tab === 'overview') return await this.renderOverview();
      if (this._tab === 'analytics') return await this.renderAnalytics();
      if (this._tab === 'history') return await this.renderHistory();
      if (this._tab === 'configuration') return this.renderConfiguration();
    } catch (e) {
      console.error('Detail tab render failed:', e);
      document.getElementById('detailBody').innerHTML =
        emptyHtml(`Could not load this tab's data — ${escapeHtml(e.message || String(e))}`);
    }
  },

  // ── Overview — current position/cycle, live strategy state, a
  // performance snapshot, and recent activity. Price/P&L cells (plus
  // the positions table's own Total row) update LIVE off the same
  // /sse/ticks stream the ticker bar uses, via window.LivePnl
  // (index.html). ─────────────────────────────────────────────────────
  _livePnlHandler: null,

  _stopLivePositionUpdates() {
    window.LivePnl.untrack(this._livePnlHandler);
    this._livePnlHandler = null;
  },

  async renderOverview() {
    this._stopLivePositionUpdates();   // never stack trackers across re-renders/tab switches
    const dep = this._dep;
    const body = document.getElementById('detailBody');
    body.innerHTML = spinnerHtml();
    try {
      const [summary, openPositions, allPositions, status, snapshots, tradesPage] = await Promise.all([
        Api.getActiveSummary(dep, true),
        Api.getPositions(dep.id, 'open'),
        Api.getPositions(dep.id, 'all'),
        Api.getStrategyStatus(dep.id).catch(() => null),
        Api.getSnapshots(dep.id).catch(() => []),
        Api.getTrades(dep.id, 8).catch(() => ({ lots: [] })),
      ]);
      dep._uxActive = summary;
      const totalPnl = Number(dep.realized_pnl || 0) + Number(dep.unrealized_pnl || 0);
      const totalReturn = dep.initial_capital ? (totalPnl / dep.initial_capital) * 100 : null;
      const units = groupPositionsIntoUnits(allPositions, 'position');
      const closed = units.filter(u => u.status === 'closed');
      const pnls = closed.map(u => Number(u.realized_pnl || 0));
      const wins = pnls.filter(v => v > 0);
      const losses = pnls.filter(v => v <= 0);
      const winRate = pnls.length ? wins.length / pnls.length * 100 : null;
      const grossWin = wins.reduce((a, b) => a + b, 0);
      const grossLoss = losses.filter(v => v < 0).reduce((a, b) => a + b, 0);
      const profitFactor = grossLoss < 0 ? grossWin / Math.abs(grossLoss) : grossWin > 0 ? Infinity : null;
      const avgPosition = pnls.length ? pnls.reduce((a, b) => a + b, 0) / pnls.length : null;
      const dd = computeMaxDrawdown(snapshots, dep.initial_capital);

      body.innerHTML = `
        <div class="ux-detail-summary-grid">
          <div class="ux-detail-summary-card">
            <div class="label">${dep.mode === 'positional' ? 'Current cycle' : "Today's P&L"}</div>
            <div class="value ${pnlClass(summary.total_pnl)}" id="uxDetailActivePnl">${dep.mode === 'positional' && !summary.active ? 'Flat' : fmtSignedMoney(summary.total_pnl)}</div>
            ${dep.mode === 'positional' && !summary.active
              ? `<div class="row"><span>Last cycle</span><b class="${pnlClass(summary.last_cycle_pnl)}">${summary.last_cycle_pnl == null ? '—' : fmtSignedMoney(summary.last_cycle_pnl)}</b></div>`
              : `<div class="row"><span>Realized</span><b class="${pnlClass(summary.realized_pnl)}">${fmtSignedMoney(summary.realized_pnl)}</b></div><div class="row"><span>Open</span><b class="${pnlClass(summary.unrealized_pnl)}" id="uxDetailOpenPnl">${fmtSignedMoney(summary.unrealized_pnl)}</b></div>`}
            <div class="row"><span>${summary.started_at ? `Started ${humanAgo(summary.started_at)}` : 'Open positions'}</span><b>${summary.open_positions}</b></div>
          </div>
          <div class="ux-detail-summary-card">
            <div class="label">Total return</div>
            <div class="value ${pnlClass(totalPnl)}">${fmtSignedMoney(totalPnl)}</div>
            <div class="row"><span>Return</span><b class="${pnlClass(totalReturn)}">${totalReturn == null ? '—' : `${totalReturn >= 0 ? '+' : ''}${totalReturn.toFixed(2)}%`}</b></div>
            <div class="row"><span>Realized all-time</span><b class="${pnlClass(dep.realized_pnl)}">${fmtSignedMoney(dep.realized_pnl)}</b></div>
            <div class="row"><span>Initial capital</span><b>${fmtMoney(dep.initial_capital)}</b></div>
          </div>
          <div class="ux-detail-summary-card">
            <div class="label">Open positions</div>
            <div class="value">${openPositions.length}</div>
            <div class="row"><span>Live unrealized</span><b class="${pnlClass(summary.unrealized_pnl)}">${fmtSignedMoney(summary.unrealized_pnl)}</b></div>
            <div class="row"><span>Cash</span><b>${fmtMoney(dep.current_cash)}</b></div>
            <div class="row"><span>Analytics</span><b>${dep.include_in_reports ? 'Included' : 'Excluded'}</b></div>
          </div>
        </div>

        ${openPositions.length ? `<section class="ux-section">
          <div class="ux-section-head"><h2>${dep.mode === 'positional' ? 'Current position / cycle' : "Today's open positions"}</h2><span class="card-sub">Live from existing tick SSE</span></div>
          ${this.positionsTable(openPositions)}
        </section>` : `<section class="ux-section"><div class="ux-section-head"><h2>Current position</h2></div><div class="empty">Flat right now${summary.last_cycle_pnl != null ? ` · last cycle ${fmtSignedMoney(summary.last_cycle_pnl)}` : ''}</div></section>`}

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
            <div class="stat-card"><div class="stat-label">Avg position P&amp;L</div><div class="stat-value ${avgPosition == null ? '' : pnlClass(avgPosition)}">${avgPosition == null ? '—' : fmtSignedMoney(avgPosition)}</div><div class="stat-sub">Whole strategic cycles, not individual legs</div></div>
          </div>
        </section>

        <section class="ux-section">
          <div class="ux-section-head"><h2>Recent activity</h2><a href="#/deployments/${dep.id}/history">View full history →</a></div>
          ${tradesPage.lots?.length ? `<div class="ux-recent-list">${tradesPage.lots.slice(0, 6).map((t, i) => `<div class="ux-recent-item" onclick="Detail.openOverviewTrade(${i})" style="cursor:pointer;"><span class="ux-recent-time">${fmtDateTime(t.executed_at)}</span><b>${escapeHtml(t.action)}</b><span>${escapeHtml(t.symbol)} · ${escapeHtml(t.reason || 'execution')}</span><span>${fmtNum(t.price)}</span></div>`).join('')}</div>` : '<div class="empty">No fills recorded yet.</div>'}
        </section>`;

      this._overviewTrades = tradesPage.lots || [];
      if (window.LivePnl && openPositions.length) {
        this._livePnlHandler = window.LivePnl.track(openPositions, ({ pnlFor, priceFor, totalPnl }) => {
          if (this._tab !== 'overview') return;
          const open = totalPnl();
          if (open != null) {
            UIKit.setLiveMoney('uxDetailOpenPnl', open);
            UIKit.setLiveMoney('uxDetailActivePnl', Number(summary.realized_pnl || 0) + open);
            // Same combined total the two KPI cards above just used --
            // keeps the positions table's own totals row live-ticking
            // in step with them, not frozen at whatever it showed on
            // the last full render.
            const footCell = body.querySelector('.live-pnl-total');
            if (footCell) { footCell.textContent = fmtSignedMoney(open); footCell.className = `live-pnl-total ${pnlClass(open)}`; }
          }
          openPositions.forEach(p => {
            const row = body.querySelector(`tr[data-ux-position-id="${p.id}"]`);
            if (!row) return;
            const px = priceFor(p.instrument_token);
            const pp = pnlFor(p.id);
            if (px != null) row.querySelector('.ux-live-price').textContent = fmtNum(px);
            if (pp != null) {
              const cell = row.querySelector('.ux-live-pnl');
              cell.textContent = fmtSignedMoney(pp); cell.className = `ux-live-pnl ${pnlClass(pp)}`;
            }
          });
        });
      }
      UIKit.enhanceTablesSoon();
    } catch (e) {
      body.innerHTML = emptyHtml(`Could not load the deployment overview — ${escapeHtml(e.message || String(e))}`);
    }
  },

  // `data-ux-col-key` on every header/footer cell (see UIKit.tfootCellsByKey's
  // own comment) -- one real <td> per column in the tfoot, no colspan,
  // so this table's own column drag/resize/hide (enhanceTable, wired
  // generically to every table in #detailBody) can never misalign the
  // total the way a colspan spacer would once a column gets reordered.
  positionsTable(rows) {
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
      <tbody>${rows.map(p => `<tr data-ux-position-id="${p.id}"><td>${escapeHtml(p.symbol)}</td><td>${escapeHtml(p.side)}</td><td>${fmtNum(p.qty)}</td><td>${fmtNum(p.avg_entry_price)}</td><td class="ux-live-price">${p.current_price != null ? fmtNum(p.current_price) : '—'}</td><td class="ux-live-pnl ${pnlClass(p.unrealized_pnl)}">${p.unrealized_pnl != null ? fmtSignedMoney(p.unrealized_pnl) : '—'}</td><td>${fmtDateTime(p.opened_at)}</td></tr>`).join('')}</tbody>
      <tfoot><tr class="positions-total-row">
        <td data-ux-col-key="symbol"><b>Total</b></td>
        <td data-ux-col-key="side"></td>
        <td data-ux-col-key="qty"></td>
        <td data-ux-col-key="avg"></td>
        <td data-ux-col-key="price"></td>
        <td class="live-pnl-total${total != null ? ' ' + pnlClass(total) : ''}" data-ux-col-key="unrealized">${total != null ? fmtSignedMoney(total) : '—'}</td>
        <td data-ux-col-key="opened"></td>
      </tr></tfoot>
    </table></div>`;
  },

  openOverviewTrade(index) {
    const lot = this._overviewTrades?.[index];
    if (!lot) return;
    UIKit.openDrawer(`${lot.action || 'Execution'} · ${lot.symbol}`, this._tradeMetaHtml(lot),
      `${fmtDateTime(lot.executed_at)} · ${lot.reason || 'No reason recorded'}`);
  },

  // Renders the EXACT metadata stored for this fill -- trigger_values/
  // target_basis/resulting_state get their own labeled blocks when
  // present (strangle_monthly_v2's Section 12 schema); anything else in
  // the metadata dict (every other, simpler strategy's own ad-hoc keys,
  // AND any keys alongside the three above) is shown verbatim in an
  // "other metadata" block -- nothing is ever silently dropped or
  // renamed, whatever shape a given strategy's metadata happens to be.
  _tradeMetaHtml(lot) {
    const meta = lot.metadata || {};
    const known = ['trigger_values', 'target_basis', 'resulting_state'];
    let html = '';
    known.forEach(k => {
      if (meta[k] !== undefined) html += renderJsonBlock(k.replace(/_/g, ' '), meta[k]);
    });
    const rest = {};
    Object.keys(meta).forEach(k => { if (!known.includes(k)) rest[k] = meta[k]; });
    if (Object.keys(rest).length) html += renderJsonBlock('other metadata', rest);
    if (!html) html = '<div class="trade-json" style="color:var(--parchment)">No structured metadata recorded for this fill.</div>';
    return html;
  },

  // ── Analytics — the per-trade/per-position stat cards and trigger-
  // reason breakdown, plus a yearly Monthly Performance matrix, a P&L
  // distribution histogram, a P&L-by-exit-reason chart, and an
  // equity/drawdown curve with its own range picker. ──────────────────
  _buildStatUnits(allPositions) {
    return groupPositionsIntoUnits(allPositions, this._statsGranularity);
  },

  _statsGranularityNote() {
    return this._statsGranularity === 'position'
      ? "One row per whole strategic bet — a straddle's legs, plus every adjustment and roll on top of them, combine into one win or loss."
      : "One row per individual leg — a straddle's CE and PE count as two separate trades, and a roll's old and new leg each count too.";
  },

  async changeStatsGranularity(value) {
    this._statsGranularity = value;
    document.querySelectorAll('#detailStatsGranularityTabs button').forEach(b =>
      b.classList.toggle('active', b.dataset.granularity === value));
    document.getElementById('detailStatsGranularityNote').textContent = this._statsGranularityNote();
    document.getElementById('detailStatsPerf').innerHTML = this._renderStatCardsAndReasonTable();
  },

  // Stat-grid + P&L by Exit Reason -- everything that depends on the
  // "per trade"/"per position" toggle, factored out so
  // changeStatsGranularity can redraw just this much on toggle instead
  // of refetching the whole Analytics tab. Reads the data
  // _renderAnalyticsBody already cached on `this` (_statsAllPositions/
  // _statsLotsByPosition/_statsAllTradeLots) rather than taking
  // parameters, same "cached on `this`, not threaded through call
  // args" pattern _overviewTrades already uses for the Overview tab.
  _renderStatCardsAndReasonTable() {
    const allPositions = this._statsAllPositions;
    const lotsByPosition = this._statsLotsByPosition;

    // Trigger breakdown -- the actual point of the trade-reason logging
    // retrofit: if a strategy is expected to hit e.g. checkpoints
    // regularly and this shows zero, that's visible immediately. Every
    // fill counts here regardless of granularity -- "how often did this
    // reason fire" doesn't change depending on how legs get grouped.
    const counts = {};
    this._statsAllTradeLots.forEach(l => {
      const r = l.reason || '(no reason recorded)';
      counts[r] = (counts[r] || 0) + 1;
    });
    const breakdown = Object.entries(counts).sort((a, b) => b[1] - a[1]);

    const units = this._buildStatUnits(allPositions);
    const closedUnits = units.filter(u => u.status === 'closed');
    const openUnits = units.filter(u => u.status === 'open');

    // P&L by Exit Reason -- a DIFFERENT cut than the fill-count
    // breakdown above: how much did closing each unit actually make or
    // lose, not just how often a reason fired. Attributed to the
    // reason of whichever lot, across EVERY position in the unit,
    // executed LAST -- for a "trade" unit (one position) that's the
    // same "this position's own last lot" rule as before; for a
    // "position" (episode) unit spanning several legs, it's whichever
    // lot actually closed the LAST remaining leg of the whole episode.
    const pnlByReason = {};
    closedUnits.forEach(u => {
      const lots = u.position_ids.flatMap(id => lotsByPosition[id] || [])
        .slice().sort((a, b) => new Date(a.executed_at) - new Date(b.executed_at));
      const lastLot = lots[lots.length - 1];
      const reason = (lastLot && lastLot.reason) || '(no reason recorded)';
      if (!pnlByReason[reason]) pnlByReason[reason] = { pnl: 0, count: 0 };
      pnlByReason[reason].pnl += u.realized_pnl;
      pnlByReason[reason].count += 1;
    });
    const pnlBreakdown = Object.entries(pnlByReason).sort((a, b) => b[1].pnl - a[1].pnl);

    // Average holding period, from each closed unit's own opened_at/closed_at.
    const durations = closedUnits
      .filter(u => u.opened_at && u.closed_at)
      .map(u => new Date(u.closed_at) - new Date(u.opened_at));
    const avgHoldMs = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null;

    // Win rate/avg win/avg loss/profit factor/largest win/largest loss
    // -- from each CLOSED unit's own realized_pnl (already netted,
    // whether that unit is one leg or several combined). Same
    // <=0-counts-as-a-loss convention the old backend build_report used.
    const pnls = closedUnits.map(u => u.realized_pnl).filter(v => v != null);
    const wins = pnls.filter(v => v > 0);
    const losses = pnls.filter(v => v <= 0);
    const winRatePct = pnls.length ? (wins.length / pnls.length) * 100 : 0;
    const avgWin = wins.length ? wins.reduce((a, b) => a + b, 0) / wins.length : 0;
    const avgLoss = losses.length ? losses.reduce((a, b) => a + b, 0) / losses.length : 0;
    const totalRealizedPnl = pnls.reduce((a, b) => a + b, 0);   // same total regardless of grouping -- grouping only changes the bucket count, not the sum
    const grossWin = pnls.filter(v => v > 0).reduce((a, b) => a + b, 0);
    const grossLoss = pnls.filter(v => v < 0).reduce((a, b) => a + b, 0);   // negative
    const profitFactor = grossLoss < 0 ? grossWin / Math.abs(grossLoss) : (grossWin > 0 ? Infinity : null);
    const largestWin = pnls.length ? Math.max(...pnls, 0) : null;
    const largestLoss = pnls.length ? Math.min(...pnls, 0) : null;

    // Total return -- realized + unrealized against the FIXED
    // initial_capital reference (same "capital, not compounding cash"
    // basis several strategies themselves size against — see e.g.
    // strangle_monthly_v2's Section 3/4). Deployment-wide, unaffected
    // by the toggle either way.
    const totalPnl = (this._dep.realized_pnl || 0) + (this._dep.unrealized_pnl || 0);
    const totalReturnPct = this._dep.initial_capital ? (totalPnl / this._dep.initial_capital) * 100 : null;

    const unitWord = this._statsGranularity === 'trade' ? 'trade' : 'position';
    const unitWordPlural = this._statsGranularity === 'trade' ? 'Trades' : 'Positions';

    return `
      <div class="stat-grid">
        <div class="stat-card">
          <div class="stat-label">Realized P&amp;L</div>
          <div class="stat-value ${pnlClass(totalRealizedPnl)}">${fmtSignedMoney(totalRealizedPnl)}</div>
          <div class="stat-sub">
            <div class="row"><span>Win rate</span><b>${fmtPct(winRatePct)}</b></div>
            <div class="row"><span>Avg win</span><b class="pos">${fmtMoney(avgWin)}</b></div>
            <div class="row"><span>Avg loss</span><b class="neg">${fmtMoney(avgLoss)}</b></div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Total return</div>
          <div class="stat-value ${totalReturnPct != null ? pnlClass(totalReturnPct) : ''}">${totalReturnPct != null ? fmtSignedMoney(totalPnl) + ` (${totalReturnPct >= 0 ? '+' : ''}${totalReturnPct.toFixed(2)}%)` : '—'}</div>
          <div class="stat-sub">
            <div class="row"><span>Profit factor</span><b>${profitFactor == null ? '—' : profitFactor === Infinity ? '∞' : profitFactor.toFixed(2)}</b></div>
            <div class="row"><span>Largest win</span><b class="pos">${largestWin != null ? fmtMoney(largestWin) : '—'}</b></div>
            <div class="row"><span>Largest loss</span><b class="neg">${largestLoss != null ? fmtMoney(largestLoss) : '—'}</b></div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-label">${unitWordPlural}</div>
          <div class="stat-value">${closedUnits.length + openUnits.length}</div>
          <div class="stat-sub">
            <div class="row"><span>Closed</span><b>${closedUnits.length}</b></div>
            <div class="row"><span>Open</span><b>${openUnits.length}</b></div>
            <div class="row"><span>Avg holding period</span><b>${avgHoldMs != null ? fmtDuration(avgHoldMs) : '—'}</b></div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Trigger breakdown</div>
          <div class="stat-value" style="font-size:13px;">${this._statsAllTradeLots.length} fill(s)</div>
          <div class="stat-sub">
            ${breakdown.length ? breakdown.map(([reason, n]) =>
              `<div class="row"><span>${escapeHtml(reason)}${triggerBadgeHtml(reason)}</span><b>${n}×</b></div>`
            ).join('') : '<div class="row"><span>—</span></div>'}
          </div>
        </div>
      </div>

      ${pnlBreakdown.length ? `
      <section>
        <h2>P&amp;L by Exit Reason</h2>
        <div class="table-note" style="margin-bottom:8px;">
          Which trigger actually closed each ${unitWord}, and what it made or lost — not just how
          often it fired (see Trigger breakdown above for that). Sorted by total contribution.
        </div>
        <div class="table-wrap">
        <table><thead><tr><th>Reason</th><th>${unitWordPlural} closed</th><th>Total P&amp;L</th><th>Avg P&amp;L</th></tr></thead>
        <tbody>${pnlBreakdown.map(([reason, v]) => `<tr>
          <td>${escapeHtml(reason)}${triggerBadgeHtml(reason)}</td>
          <td>${v.count}</td>
          <td class="${pnlClass(v.pnl)}">${fmtSignedMoney(v.pnl)}</td>
          <td class="${pnlClass(v.pnl / v.count)}">${fmtSignedMoney(v.pnl / v.count)}</td>
        </tr>`).join('')}</tbody></table>
        </div>
      </section>
      ` : ''}
    `;
  },

  // The original per-trade/per-position stat cards, trigger breakdown,
  // adjustment histogram, equity curve, Recent Periods trend table, and
  // P&L Calendar -- the foundation renderAnalytics() below augments
  // with the Monthly Performance matrix / P&L distribution / P&L-by-
  // exit-reason / enhanced equity curve.
  async _renderAnalyticsBody() {
    const [allTrades, allPositions, snapshots, trendRows, calendarRows, strategyStatus, adjustmentHistogram] = await Promise.all([
      Api.getTrades(this._id, 2000),
      Api.getPositions(this._id, 'all'),
      Api.getSnapshots(this._id),
      Api.getPnlDigestForDeployment(this._id, this._statsTrendPeriod, 14),
      this._fetchStatsCalendarRows(),
      Api.getStrategyStatus(this._id),
      Api.getAdjustmentHistogram(this._id),
    ]);
    const body = document.getElementById('detailBody');

    // Cached for _renderStatCardsAndReasonTable/changeStatsGranularity's
    // own targeted re-render -- toggling "per trade"/"per position"
    // recomputes from these, it doesn't refetch anything.
    this._statsAllPositions = allPositions;
    this._statsAllTradeLots = allTrades.lots;
    this._statsLotsByPosition = {};
    allTrades.lots.forEach(l => {
      (this._statsLotsByPosition[l.position_id] = this._statsLotsByPosition[l.position_id] || []).push(l);
    });

    // Max drawdown -- largest peak-to-trough decline in this
    // deployment's REALIZED equity (Step 105 -- "capital lost forever,"
    // not a live paper dip on an open position; see computeMaxDrawdown's
    // own docstring), off the same snapshot data already fetched for
    // the chart below, no extra request. Shared with Compare's own
    // drawdown column via computeMaxDrawdown (api.js).
    const drawdown = computeMaxDrawdown(snapshots, this._dep.initial_capital);

    // Deployed-since / last-activity — operational context: is this
    // thing actually doing anything, and how long has it been running.
    const deployedMs = Date.now() - new Date(this._dep.created_at).getTime();
    const lastFill = allTrades.lots.length ? allTrades.lots[0] : null;   // newest-first, see GET .../trades

    body.innerHTML = `
      <div class="card-meta" style="margin-bottom:16px;">
        <span>Deployed <b>${fmtDuration(deployedMs)} ago</b> (${fmtDateTime(this._dep.created_at)})</span>
        <span>Last activity <b>${lastFill ? fmtDateTime(lastFill.executed_at) : '—'}</b></span>
      </div>

      ${this._strategyIndicatorsHtml(strategyStatus)}

      <div class="report-section-header" style="cursor:default; padding:0; margin-bottom:10px; justify-content:space-between; flex-wrap:wrap;">
        <h2 style="margin:0;">Performance</h2>
        <div class="tabs" id="detailStatsGranularityTabs" style="margin:0;">
          <button class="${this._statsGranularity === 'position' ? 'active' : ''}" data-granularity="position" onclick="Detail.changeStatsGranularity('position')">Per position</button>
          <button class="${this._statsGranularity === 'trade' ? 'active' : ''}" data-granularity="trade" onclick="Detail.changeStatsGranularity('trade')">Per trade</button>
        </div>
      </div>
      <div class="table-note" style="margin-bottom:14px;" id="detailStatsGranularityNote">${this._statsGranularityNote()}</div>

      <div id="detailStatsPerf">${this._renderStatCardsAndReasonTable()}</div>

      ${this._adjustmentHistogramHtml(adjustmentHistogram)}

      <section>
        <h2>Equity Curve</h2>
        ${drawdown ? `<div class="card-meta" style="margin-bottom:10px;">
          <span>Max drawdown <b class="neg">${fmtMoney(drawdown.abs)} (${drawdown.pct.toFixed(2)}%)</b>
            — largest peak-to-trough decline across every recorded snapshot</span>
        </div>` : ''}
        ${renderEquityChart(snapshots, undefined, 'equity-detail')}
      </section>

      <section>
        <div class="report-section-header" style="cursor:default; padding:0; margin-bottom:10px; justify-content:space-between; flex-wrap:wrap;">
          <h2 style="margin:0;">Recent Periods</h2>
          <div class="tabs" id="detailStatsTrendTabs" style="margin:0;">
            <button class="${this._statsTrendPeriod === 'day' ? 'active' : ''}" data-period="day" onclick="Detail.changeStatsTrendPeriod('day')">Daily</button>
            <button class="${this._statsTrendPeriod === 'week' ? 'active' : ''}" data-period="week" onclick="Detail.changeStatsTrendPeriod('week')">Weekly</button>
            <button class="${this._statsTrendPeriod === 'month' ? 'active' : ''}" data-period="month" onclick="Detail.changeStatsTrendPeriod('month')">Monthly</button>
          </div>
        </div>
        <div id="detailStatsTrend">${renderPnlTrendTable(trendRows, { periodLabel: iso => this._statsPeriodLabel(iso) })}</div>
      </section>

      <section>
        <h2>P&amp;L Calendar</h2>
        <div id="detailStatsCalendar">${renderPnlHeatmap(calendarRows, {
          year: this._calendarRange === 'recent' ? null : this._calendarRange,
          selector: { value: this._calendarRange, onChange: 'Detail.changeCalendarRange(this.value)' },
        })}</div>
      </section>
    `;
    scrollPnlHeatmapToEnd('detailStatsCalendar');
  },

  async renderAnalytics() {
    const body = document.getElementById('detailBody');
    body.classList.add('ux-analytics');
    await this._renderAnalyticsBody();
    try {
      const [monthly, snaps] = await Promise.all([
        Api.getPnlDigestForDeployment(this._id, 'month', 180),
        Api.getSnapshots(this._id),
      ]);
      this._matrixRows = monthly;
      this._matrixSnapshots = snaps;
      this._matrixMode = this._matrixMode || 'absolute';
      this._equitySnapshots = snaps;
      this._equityMode = this._equityMode || 'absolute';
      this._equityRange = this._equityRange || 'all';
      const perf = document.getElementById('detailStatsPerf');
      const matrix = document.createElement('section');
      matrix.className = 'ux-section';
      matrix.innerHTML = '<div id="uxMonthlyPerformance"></div>';
      perf?.insertAdjacentElement('afterend', matrix);
      this.renderPerformanceMatrix(document.getElementById('uxMonthlyPerformance'), monthly, snaps, this._dep, this._matrixMode);

      const units = groupPositionsIntoUnits(this._statsAllPositions || [], 'position');
      const distributionHtml = this.renderPnlDistribution(units);
      if (distributionHtml) matrix.insertAdjacentHTML('afterend', distributionHtml);
      const exitHtml = this.renderExitReasonBars();
      const distribution = matrix.nextElementSibling;
      if (exitHtml) (distribution || matrix).insertAdjacentHTML('afterend', exitHtml);

      // Replace only the old Equity Curve section's presentation; the
      // snapshots/data and all downstream sections stay untouched.
      const equitySection = [...body.querySelectorAll('section')].find(sec => sec.querySelector('h2')?.textContent.trim() === 'Equity Curve');
      if (equitySection) {
        equitySection.innerHTML = '<div id="uxEnhancedEquity"></div>';
        this.renderEnhancedEquity(document.getElementById('uxEnhancedEquity'));
      }
      UIKit.enhanceTablesSoon();
    } catch (e) {
      console.warn('Monthly performance enhancement failed', e);
    }
  },

  // ── Strategy-specific data (Step 87) — GET /deployments/{id}/
  // strategy-status and .../adjustment-histogram, both opt-in per
  // strategy (see StrategyBase.get_status_fields/ADJUSTMENT_GROUP_BY's
  // own docstrings). Neither renders anything at all for a strategy
  // that doesn't override its half of the contract — no empty section,
  // no "not available" placeholder cluttering every other strategy's
  // Analytics tab. ─────────────────────────────────────────────────────
  _strategyIndicatorsHtml(status) {
    if (!status || !status.fields || !status.fields.length) return '';
    const staleness = status.source === 'persisted'
      ? ' <span class="tag tag-warn" title="This deployment isn\'t currently running -- these are from its last pause/stop/daily checkpoint, not real-time.">as of last checkpoint</span>'
      : '';
    return `
      <section>
        <h2 style="display:flex; align-items:center; gap:8px;">Live Strategy Indicators${staleness}</h2>
        <div class="card-meta">
          ${status.fields.map(f => `<span>${escapeHtml(f.label)} <b>${escapeHtml(String(f.value))}</b></span>`).join('')}
        </div>
      </section>
    `;
  },

  // Adjustment-count histogram (e.g. intraday_dtt_adjusted: "how many
  // days had 0/1/2/3+ adjustments") -- reuses the single-direction
  // win-rate bar CSS (report-winrate-track/-fill), the correct shape
  // here too: a bucket's own count has no "negative" side, unlike the
  // center-zero P&L bars elsewhere on this page.
  _adjustmentHistogramHtml(histogram) {
    if (!histogram || !histogram.supported || !histogram.buckets.length) return '';
    const unitLabel = histogram.group_by === 'cycle_id' ? 'cycle' : 'day';
    const totalUnits = histogram.buckets.reduce((sum, b) => sum + b.units, 0);
    const maxUnits = Math.max(...histogram.buckets.map(b => b.units), 1);
    return `
      <section>
        <h2>Adjustment Frequency</h2>
        <div class="table-note" style="margin-bottom:8px;">
          How many ${unitLabel}s (${totalUnits} total) needed how many adjustments, from
          every ${unitLabel} this deployment has ever traded.
        </div>
        <div class="table-wrap">
        <table><thead><tr><th>Adjustments</th><th>${unitLabel[0].toUpperCase()}${unitLabel.slice(1)}s</th><th></th></tr></thead>
        <tbody>${histogram.buckets.map(b => `<tr>
          <td>${escapeHtml(b.label)}</td>
          <td>${b.units}</td>
          <td>
            <div class="report-winrate-track" style="max-width:220px;">
              <div class="report-winrate-fill" style="width:${(b.units / maxUnits) * 100}%;"></div>
            </div>
          </td>
        </tr>`).join('')}</tbody></table>
        </div>
      </section>
    `;
  },

  // This deployment's own daily P&L as a GitHub-style heatmap (see
  // renderPnlHeatmap, api.js), backed by GET /deployments/{id}/pnl-digest
  // (deployment-scoped twin of the Reports page's portfolio-wide
  // GET /portfolio/pnl-digest).
  _fetchStatsCalendarRows() {
    const year = this._calendarRange === 'recent' ? null : this._calendarRange;
    return year
      ? Api.getPnlDigestForDeployment(this._id, 'day', 400, year)
      : Api.getPnlDigestForDeployment(this._id, 'day', 400);
  },

  async changeCalendarRange(value) {
    this._calendarRange = value === 'recent' ? 'recent' : Number(value);
    const rows = await this._fetchStatsCalendarRows();
    const year = this._calendarRange === 'recent' ? null : this._calendarRange;
    document.getElementById('detailStatsCalendar').innerHTML = renderPnlHeatmap(rows, {
      year,
      selector: { value: this._calendarRange, onChange: 'Detail.changeCalendarRange(this.value)' },
    });
    scrollPnlHeatmapToEnd('detailStatsCalendar');
  },

  _statsPeriodLabel(iso) {
    if (this._statsTrendPeriod === 'week') return `Week of ${fmtDate(iso)}`;
    if (this._statsTrendPeriod === 'month') {
      const d = new Date(iso);
      return isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-IN', { year: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' });
    }
    return fmtDate(iso);
  },

  async changeStatsTrendPeriod(period) {
    this._statsTrendPeriod = period;
    document.querySelectorAll('#detailStatsTrendTabs button').forEach(b =>
      b.classList.toggle('active', b.dataset.period === period));
    const rows = await Api.getPnlDigestForDeployment(this._id, period, 14);
    document.getElementById('detailStatsTrend').innerHTML =
      renderPnlTrendTable(rows, { periodLabel: iso => this._statsPeriodLabel(iso) });
  },

  // Keeps the platform's existing drawdown definition: permanent/settled
  // capital decline, not a temporary paper dip on a still-open positional
  // leg. Mirrors api.js's computeMaxDrawdown exactly, but also keeps
  // the peak/trough dates needed by the yearly matrix below.
  computeDrawdownDetails(points, initialCapital) {
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
  },

  renderPerformanceMatrix(container, monthlyRows, snapshots, dep, mode = 'absolute') {
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
      : fmtSignedMoney(value);

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
        const tooltip = `${months[monthIndex]} ${year}\nRealized: ${fmtSignedMoney(pnl)}\nReturn: ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%\nPositions closed: ${r.positions_closed || 0}\nWins/Losses: ${r.wins || 0}/${r.losses || 0}`;
        return `<td class="ux-month-cell ${cls}${current}" title="${escapeHtml(tooltip)}" onclick="Detail.openMatrixMonth(${year},${monthIndex})">${formatValue(value)}</td>`;
      }).join('');
      const totalValue = mode === 'percent' ? totalPct : totalPnl;
      return `<tr>
        <td>${year}</td>${cells}
        <td class="total ${pnlClass(totalValue)}" onclick="Detail.openMatrixYear(${year})" style="cursor:pointer;">${formatValue(totalValue)}</td>
        <td class="${dd?.abs ? 'neg' : ''}" title="${dd ? `${fmtDateTime(dd.peak_at)} → ${fmtDateTime(dd.trough_at)}` : ''}">${dd ? `${mode === 'percent' ? `-${dd.pct.toFixed(2)}%` : fmtMoney(-dd.abs)}` : '—'}</td>
        <td>${dd ? dd.days : '—'}</td>
        <td>${ratio == null ? '—' : ratio === Infinity ? '∞' : ratio.toFixed(2)}</td>
      </tr>`;
    }).join('');

    container.innerHTML = `
      <div class="ux-analytics-toolbar">
        <div><b>Monthly Performance</b><div class="card-sub">Settled monthly P&amp;L. Click any month to inspect the underlying history.</div></div>
        <div class="ux-segmented">
          <button class="${mode === 'absolute' ? 'active' : ''}" onclick="Detail.setMatrixMode('absolute')">₹ Absolute</button>
          <button class="${mode === 'percent' ? 'active' : ''}" onclick="Detail.setMatrixMode('percent')">% Return</button>
        </div>
      </div>
      <div class="ux-performance-matrix-wrap"><table class="ux-performance-matrix ux-no-enhance"><thead><tr><th>Year</th>${months.map(m => `<th>${m}</th>`).join('')}<th>Total</th><th>Max DD</th><th>MDD Days</th><th>Return / DD</th></tr></thead><tbody>${bodyRows}</tbody></table></div>
      <div class="ux-matrix-caption">Percentage view uses the deployment's fixed initial capital, matching the platform's existing total-return convention. Max drawdown uses settled/realized equity, matching the platform's permanent-capital-loss definition.</div>`;
  },

  setMatrixMode(mode) {
    this._matrixMode = mode;
    this.renderPerformanceMatrix(document.getElementById('uxMonthlyPerformance'), this._matrixRows || [], this._matrixSnapshots || [], this._dep, mode);
  },

  openMatrixMonth(year, monthIndex) {
    this._historyRange = {
      start: _startOfMonthIso(year, monthIndex),
      end: _endOfMonthIso(year, monthIndex),
      label: `${new Date(Date.UTC(year, monthIndex, 1)).toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' })}`,
    };
    this._historyMode = 'positions';
    window.location.hash = `#/deployments/${this._id}/history`;
  },

  openMatrixYear(year) {
    this._historyRange = { start: `${year}-01-01T00:00:00+05:30`, end: `${year}-12-31T23:59:59+05:30`, label: String(year) };
    this._historyMode = 'positions';
    window.location.hash = `#/deployments/${this._id}/history`;
  },

  renderPnlDistribution(units) {
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
      <div class="card-meta" style="margin-top:10px;"><span>Median <b class="${pnlClass(median)}">${fmtSignedMoney(median)}</b></span><span>Average <b class="${pnlClass(avg)}">${fmtSignedMoney(avg)}</b></span><span>Best <b class="pos">${fmtSignedMoney(max)}</b></span><span>Worst <b class="neg">${fmtSignedMoney(min)}</b></span></div>
    </section>`;
  },

  filterEquitySnapshots(snapshots, range) {
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
  },

  renderEnhancedEquity(container) {
    if (!container) return;
    const snapshots = this.filterEquitySnapshots(this._equitySnapshots || [], this._equityRange || 'all');
    if (snapshots.length < 2) {
      container.innerHTML = emptyHtml('Not enough equity history in this range yet.');
      return;
    }
    const mode = this._equityMode || 'absolute';
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
    let peak = Number(this._dep.initial_capital || 0) + Number(points[0].realized_pnl_cumulative || 0);
    const dd = points.map(p => {
      const v = Number(this._dep.initial_capital || 0) + Number(p.realized_pnl_cumulative || 0);
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
          <div class="ux-segmented">${['1m','3m','6m','ytd','1y','all'].map(r => `<button class="${(this._equityRange || 'all') === r ? 'active' : ''}" onclick="Detail.setEquityRange('${r}')">${r === 'all' ? 'ALL' : r.toUpperCase()}</button>`).join('')}</div>
          <div class="ux-segmented"><button class="${mode === 'absolute' ? 'active' : ''}" onclick="Detail.setEquityMode('absolute')">₹</button><button class="${mode === 'percent' ? 'active' : ''}" onclick="Detail.setEquityMode('percent')">%</button></div>
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
          ChartTooltip.show(clientX, clientY, `<b>${fmtDate(p.snapshot_at)}</b><br>Equity ${fmtMoney(p.total_value)}<br>Range return ${p.pct >= 0 ? '+' : ''}${p.pct.toFixed(2)}%<br>Settled P&amp;L ${fmtSignedMoney(p.realized_pnl_cumulative)}<br>Drawdown ${dd[idx].toFixed(2)}%`);
        }
      };
      area.addEventListener('pointermove', e => show(e.clientX, e.clientY));
      area.addEventListener('pointerleave', () => { if (cross) cross.style.display = 'none'; if (typeof ChartTooltip !== 'undefined') ChartTooltip.hide(); });
      area.addEventListener('touchmove', e => { const t = e.touches?.[0]; if (t) show(t.clientX, t.clientY); }, { passive: true });
    }
  },

  setEquityMode(mode) {
    this._equityMode = mode;
    this.renderEnhancedEquity(document.getElementById('uxEnhancedEquity'));
  },
  setEquityRange(range) {
    this._equityRange = range;
    this.renderEnhancedEquity(document.getElementById('uxEnhancedEquity'));
  },

  renderExitReasonBars() {
    const units = groupPositionsIntoUnits(this._statsAllPositions || [], 'position');
    const lotsByPosition = this._statsLotsByPosition || {};
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
    return `<section class="ux-section"><div class="ux-section-head"><div><h2>P&amp;L contribution by exit</h2><div class="card-sub">Which exit trigger actually contributes or destroys P&amp;L. The detailed table remains below.</div></div></div><div class="ux-bar-list ux-exit-bars">${rows.map(([reason, v]) => `<div class="ux-bar-row"><span title="${escapeHtml(reason)}">${escapeHtml(reason)}</span><div class="ux-bar-track"><div class="ux-bar-fill ${v.pnl < 0 ? 'neg' : ''}" style="width:${Math.abs(v.pnl) / maxAbs * 100}%"></div></div><b class="${pnlClass(v.pnl)}">${fmtSignedMoney(v.pnl)} <small>· ${v.count}</small></b></div>`).join('')}</div></section>`;
  },

  // ── History — every position/cycle, execution, and event ever
  // recorded, filterable by clicking a month/year in the Analytics
  // matrix (openMatrixMonth/openMatrixYear above set _historyRange and
  // navigate here). ────────────────────────────────────────────────────
  _historyMode: 'positions',
  _historyRange: null,
  _historyTrades: [],
  _historyEvents: [],
  _historyPositions: [],

  async renderHistory() {
    this._stopLivePositionUpdates();
    const body = document.getElementById('detailBody');
    body.innerHTML = spinnerHtml();
    try {
      const [positions, trades, events] = await Promise.all([
        Api.getPositions(this._id, 'all'), Api.getTrades(this._id, 2000), Api.getEvents(this._id, 1000),
      ]);
      this._historyTrades = trades.lots || [];
      this._historyEvents = events || [];
      this._historyPositions = positions || [];
      this.paintHistory();
    } catch (e) {
      body.innerHTML = emptyHtml(`Could not load history — ${escapeHtml(e.message || String(e))}`);
    }
  },

  paintHistory() {
    const body = document.getElementById('detailBody');
    const range = this._historyRange;
    body.innerHTML = `
      <div class="ux-history-toolbar">
        <div class="ux-segmented">
          <button class="${this._historyMode === 'positions' ? 'active' : ''}" onclick="Detail.setHistoryMode('positions')">Positions / Cycles</button>
          <button class="${this._historyMode === 'executions' ? 'active' : ''}" onclick="Detail.setHistoryMode('executions')">Executions</button>
          <button class="${this._historyMode === 'events' ? 'active' : ''}" onclick="Detail.setHistoryMode('events')">Events</button>
        </div>
        <div>${range ? `<span class="ux-history-filter-chip">${escapeHtml(range.label)} <button class="btn btn-secondary btn-sm" style="padding:1px 5px;" onclick="Detail.clearHistoryRange()">✕</button></span>` : '<span class="card-sub">All history</span>'}</div>
      </div>
      <div id="uxHistoryContent"></div>`;
    const content = document.getElementById('uxHistoryContent');
    if (this._historyMode === 'executions') this.paintHistoryExecutions(content);
    else if (this._historyMode === 'events') this.paintHistoryEvents(content);
    else this.paintHistoryPositions(content);
    UIKit.enhanceTablesSoon();
  },

  setHistoryMode(mode) {
    this._historyMode = mode;
    this.paintHistory();
  },
  clearHistoryRange() { this._historyRange = null; this.paintHistory(); },

  paintHistoryPositions(content) {
    let units = groupPositionsIntoUnits(this._historyPositions || [], 'position');
    units = units.filter(u => {
      if (!this._historyRange) return true;
      const rangeStart = new Date(this._historyRange.start).getTime();
      const rangeEnd = new Date(this._historyRange.end).getTime();
      const opened = new Date(u.opened_at).getTime();
      const closed = u.closed_at ? new Date(u.closed_at).getTime() : Date.now();
      // A cycle belongs to the selected period whenever its lifetime
      // overlaps the period, even if it opened before the first day.
      return opened <= rangeEnd && closed >= rangeStart;
    }).slice().sort((a, b) => new Date(b.closed_at || b.opened_at) - new Date(a.closed_at || a.opened_at));
    if (!units.length) { content.innerHTML = emptyHtml('No positions/cycles in this period.'); return; }
    const byId = new Map((this._historyPositions || []).map(p => [p.id, p]));
    content.innerHTML = units.map((u, i) => {
      const ps = (u.position_ids || []).map(id => byId.get(id)).filter(Boolean);
      const unrealized = ps.filter(p => p.status === 'open').reduce((s, p) => s + Number(p.unrealized_pnl || 0), 0);
      const total = Number(u.realized_pnl || 0) + unrealized;
      return `<div class="ux-cycle-card" id="uxCycle-${i}">
        <div class="ux-cycle-head" onclick="document.getElementById('uxCycle-${i}').classList.toggle('open')">
          <div><b>${this._dep.mode === 'positional' ? 'Cycle' : 'Position'} · ${fmtDateTime(u.opened_at)}</b><div class="card-sub">${u.status === 'open' ? 'Open' : `Closed ${fmtDateTime(u.closed_at)}`} · ${ps.length} leg${ps.length === 1 ? '' : 's'}</div></div>
          <span class="tag tag-${u.status === 'open' ? 'active' : 'stopped'}">${u.status}</span>
          <b class="${pnlClass(total)}">${fmtSignedMoney(total)}</b>
        </div>
        <div class="ux-cycle-body"><div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Realized</th><th>Status</th></tr></thead><tbody>${ps.map(p => `<tr><td>${escapeHtml(p.symbol)}</td><td>${escapeHtml(p.side)}</td><td>${fmtNum(p.qty)}</td><td>${fmtNum(p.avg_entry_price)}</td><td class="${pnlClass(p.realized_pnl)}">${fmtSignedMoney(p.realized_pnl)}</td><td>${escapeHtml(p.status)}</td></tr>`).join('')}</tbody></table></div></div>
      </div>`;
    }).join('');
  },

  paintHistoryExecutions(content) {
    const rows = this._historyTrades.filter(t => dateRangeContains(t.executed_at, this._historyRange));
    if (!rows.length) { content.innerHTML = emptyHtml('No executions in this period.'); return; }
    content.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Action</th><th>Symbol</th><th>Qty</th><th>Price</th><th>Reason</th></tr></thead><tbody>${rows.slice(0, 1000).map((t, i) => `<tr class="ux-row-navigate" onclick="Detail.openHistoryExecution(${this._historyTrades.indexOf(t)})"><td>${fmtDateTime(t.executed_at)}</td><td>${escapeHtml(t.action)}</td><td>${escapeHtml(t.symbol)}</td><td>${fmtNum(t.qty)}</td><td>${fmtNum(t.price)}</td><td>${escapeHtml(t.reason || '')}${triggerBadgeHtml(t.reason)}</td></tr>`).join('')}</tbody></table></div>`;
  },

  paintHistoryEvents(content) {
    const rows = this._historyEvents.filter(e => dateRangeContains(e.created_at, this._historyRange));
    if (!rows.length) { content.innerHTML = emptyHtml('No events in this period.'); return; }
    content.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Event</th><th>Message</th></tr></thead><tbody>${rows.slice(0, 1000).map((e, i) => `<tr class="${e.metadata && Object.keys(e.metadata).length ? 'ux-row-navigate' : ''}" ${e.metadata && Object.keys(e.metadata).length ? `onclick="Detail.openHistoryEvent(${this._historyEvents.indexOf(e)})"` : ''}><td>${fmtDateTime(e.created_at)}</td><td><span class="tag ${e.event_type === 'strategy_error' ? 'tag-error' : 'tag-info'}">${escapeHtml(e.event_type)}</span></td><td>${escapeHtml(e.message || '')}</td></tr>`).join('')}</tbody></table></div>`;
  },

  openHistoryExecution(index) {
    const lot = this._historyTrades[index];
    if (!lot) return;
    UIKit.openDrawer(`${lot.action} · ${lot.symbol}`, this._tradeMetaHtml(lot), `${fmtDateTime(lot.executed_at)} · ${lot.reason || 'No reason recorded'}`);
  },
  openHistoryEvent(index) {
    const ev = this._historyEvents[index];
    if (!ev) return;
    UIKit.openDrawer(ev.event_type.replace(/_/g, ' '), renderJsonBlock('metadata', ev.metadata || {}), `${fmtDateTime(ev.created_at)} · ${ev.message || ''}`);
  },

  // ── Configuration — read-only strategy config, grouped by topic
  // (UIKit.configGroup), editable only while paused. ────────────────────
  renderConfiguration() {
    const dep = this._dep;
    const body = document.getElementById('detailBody');
    const cfg = dep.config || {};
    const groups = {};
    Object.keys(cfg).sort().forEach(k => { (groups[UIKit.configGroup(k)] ||= []).push([k, cfg[k]]); });
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
          ${dep.status === 'paused' ? '<button class="btn btn-primary btn-sm" onclick="Detail.openEditConfigModal()">Edit configuration</button>' : dep.status === 'active' ? '<button class="btn btn-secondary btn-sm" onclick="Detail.pauseAndEditConfig()">Pause & edit</button>' : ''}
        </div>
        <div class="ux-config-grid">${Object.entries(groups).map(([name, entries]) => `<div class="ux-config-group"><h3>${escapeHtml(name)}</h3><div class="ux-config-kv">${entries.map(([k, v]) => `<div class="ux-config-key">${escapeHtml(k)}</div><div class="ux-config-value">${formatConfigValue(v)}</div>`).join('')}</div></div>`).join('')}</div>
      </section>
      <details class="ux-section"><summary style="cursor:pointer;font-weight:800;">Advanced · raw JSON</summary><div style="margin-top:10px;">${renderJsonBlock('config', cfg)}</div></details>`;
  },

  async pauseAndEditConfig() {
    if (!confirm(`Pause "${this._dep.deployment_name}" so its configuration can be edited? Open positions remain open while paused.`)) return;
    const r = await Api.pauseDeployment(this._id);
    if (!r.ok) { alert('Could not pause deployment.'); return; }
    await this.load(this._id);
    this.openEditConfigModal();
  },

  // ── Header actions ──────────────────────────────────────────────
  async openEditModal() {
    document.getElementById('editDeploymentName').value = this._dep.deployment_name;
    document.getElementById('editDeploymentNotes').value = this._dep.notes || '';
    document.getElementById('editDeploymentIncludeInReports').checked = this._dep.include_in_reports;
    document.getElementById('editDeploymentNotificationsEnabled').checked = this._dep.notifications_enabled;
    document.getElementById('editDeploymentMsg').textContent = '';
    document.getElementById('editDeploymentModal').classList.add('open');
    requestAnimationFrame(() => UIKit.renameAnalyticsSemantics(document.getElementById('editDeploymentModal') || document));

    // Built fresh on every open (not cached across modal sessions) --
    // the catalog itself can grow from Settings -> Tags between two
    // edits of the same deployment, and this modal is cheap enough to
    // rebuild that a stale list is never worth risking.
    const listEl = document.getElementById('editDeploymentTagsList');
    listEl.textContent = 'Loading…';
    let tags;
    try {
      tags = await Api.listTags();
    } catch (e) {
      listEl.textContent = 'Could not load tags — try again.';
      return;
    }
    const current = new Set(this._dep.tags || []);
    listEl.innerHTML = !tags.length
      ? 'No tags yet — add some from Settings → Tags.'
      : tags.map(t => `
          <label style="display:inline-flex; align-items:center; gap:5px; margin:0 14px 8px 0; cursor:pointer;">
            <input type="checkbox" class="editDeploymentTagCheckbox" value="${escapeHtml(t.name)}"
              style="width:auto;" ${current.has(t.name) ? 'checked' : ''}>
            ${escapeHtml(t.name)}
          </label>
        `).join('');
  },

  // ── Edit config (Step 51) — only ever opened from the Configuration
  // tab's own paused-only button, but the paused check is enforced
  // server-side too (see the PATCH handler), not just gated by hiding
  // the button. Reuses the exact same field-widget machinery as the
  // Deploy modal (configFieldHtml family, api.js) with its own idPrefix/
  // container ids and its own _editConfigBase, entirely independent of
  // Catalog's own deploy-time state. ─────────────────────────────────────
  _editConfigBase: {},

  openEditConfigModal() {
    document.getElementById('editConfigModalTitle').textContent = `Edit config: ${this._dep.deployment_name}`;
    document.getElementById('editConfigAdvancedToggle').checked = false;
    document.getElementById('editConfigFields').style.removeProperty('display');
    document.getElementById('editConfigJson').style.display = 'none';
    this._renderEditConfigFields(this._dep.config || {});
    document.getElementById('editConfigMsg').textContent = '';
    document.getElementById('editConfigModal').classList.add('open');
  },

  _renderEditConfigFields(config) {
    this._editConfigBase = { ...config };
    document.getElementById('editConfigFields').innerHTML = configFieldsContainerHtml(config, 'editCfgField_');
    requestAnimationFrame(() => UIKit.groupConfigFields(document.getElementById('editConfigFields')));
  },

  toggleEditConfigAdvanced() {
    const on = document.getElementById('editConfigAdvancedToggle').checked;
    const fieldsEl = document.getElementById('editConfigFields');
    const jsonEl = document.getElementById('editConfigJson');
    if (on) {
      const config = readConfigFromFields('editConfigFields', this._editConfigBase);
      jsonEl.value = JSON.stringify(config, null, 2);
      fieldsEl.style.display = 'none';
      jsonEl.style.display = 'block';
    } else {
      let parsed;
      try {
        parsed = JSON.parse(jsonEl.value || '{}');
      } catch (e) {
        document.getElementById('editConfigAdvancedToggle').checked = true;
        alert(`Invalid JSON — fix it first, or it can't be converted back to fields:\n${e.message}`);
        return;
      }
      this._renderEditConfigFields(parsed);
      fieldsEl.style.removeProperty('display');
      jsonEl.style.display = 'none';
    }
  },
  async pause() { await Api.pauseDeployment(this._id); this.load(this._id); },
  async resume() {
    // No longer a guaranteed no-op fire-and-forget: resume can now
    // genuinely fail (409) if config was edited while paused into
    // something the strategy's own on_start() rejects -- see
    // DeploymentManager.resume's own rollback-to-paused comment. Same
    // ok-check pattern stop() already used, just not previously needed
    // here.
    const r = await Api.resumeDeployment(this._id);
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      alert(data.detail || 'Could not resume — check its config on the Configuration tab.');
    }
    this.load(this._id);
  },
  async stop() {
    return UIKit.openStopDialog(this._id, this._dep?.deployment_name);
  },
  async deleteDeployment() {
    // Only ever offered while stopped (see the header menu's own status
    // check) — the backend enforces the same restriction independently
    // either way. Permanent: every position/trade/event/snapshot under
    // this deployment goes with it, via ON DELETE CASCADE.
    const name = this._dep ? this._dep.deployment_name : 'this deployment';
    const ok = confirm(
      `Permanently delete "${name}"?\n\nThis removes ALL of its positions, trades, and history — ` +
      `not just the deployment itself. This cannot be undone.`
    );
    if (!ok) return;
    const r = await Api.deleteDeployment(this._id);
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      alert(data.detail || 'Could not delete this deployment.');
      return;
    }
    // Nothing left here to show -- back to the list.
    location.hash = '#/deployments';
  },
};

// ── Shared render helpers for this view ────────────────────────────

function formatConfigValue(v) {
  if (v === null || v === undefined) return '<span style="color:var(--parchment)">null</span>';
  if (typeof v === 'object') return `<span class="trade-json">${escapeHtml(JSON.stringify(v))}</span>`;
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  return escapeHtml(String(v));
}

function renderJsonBlock(label, obj) {
  if (obj === undefined || obj === null) return '';
  if (typeof obj === 'object' && Object.keys(obj).length === 0) return '';
  return `<div class="trade-json-block"><div class="label">${escapeHtml(label)}</div><div class="trade-json">${escapeHtml(JSON.stringify(obj, null, 2))}</div></div>`;
}

// Used only by the Analytics tab's Monthly Performance matrix
// (openMatrixMonth above) to turn a clicked (year, monthIndex) cell
// into an IST month boundary for the History tab's own range filter.
function _startOfMonthIso(year, monthIndex) {
  return `${year}-${String(monthIndex + 1).padStart(2, '0')}-01T00:00:00+05:30`;
}
function _endOfMonthIso(year, monthIndex) {
  const nextYear = monthIndex === 11 ? year + 1 : year;
  const nextMonth = monthIndex === 11 ? 0 : monthIndex + 1;
  const next = new Date(`${nextYear}-${String(nextMonth + 1).padStart(2, '0')}-01T00:00:00+05:30`);
  return new Date(next.getTime() - 1).toISOString();
}

// fmtDuration() now lives in api.js (Step 111 -- shared with Compare's
// head-to-head table's own "Avg Holding Period" row).

// renderEquityChart() now lives in api.js (shared with the Portfolio
// view's combined curve, Step 39) — see there for the implementation.

// ── Edit deployment (rename + notes) modal ─────────────────────────────
function closeEditDeploymentModal() {
  document.getElementById('editDeploymentModal').classList.remove('open');
}
async function submitEditDeployment() {
  const msg = document.getElementById('editDeploymentMsg');
  const name = document.getElementById('editDeploymentName').value.trim();
  const notes = document.getElementById('editDeploymentNotes').value;
  const includeInReports = document.getElementById('editDeploymentIncludeInReports').checked;
  const notificationsEnabled = document.getElementById('editDeploymentNotificationsEnabled').checked;
  const tags = Array.from(document.querySelectorAll('.editDeploymentTagCheckbox:checked')).map(el => el.value);
  if (!name) { msg.innerHTML = '<span style="color:var(--loss)">Deployment name cannot be blank</span>'; return; }
  msg.innerHTML = '<span class="spinner"></span> Saving…';
  const { ok, data } = await Api.updateDeployment(Detail._id, {
    deployment_name: name, notes, include_in_reports: includeInReports,
    notifications_enabled: notificationsEnabled, tags,
  });
  if (!ok) { msg.innerHTML = `<span style="color:var(--loss)">${escapeHtml(data.detail || 'Failed')}</span>`; return; }
  msg.innerHTML = '<span style="color:var(--gain)">✓ Saved</span>';
  setTimeout(() => { closeEditDeploymentModal(); Detail.load(Detail._id); }, 500);
}

// ── Edit config modal (Step 51) ─────────────────────────────────────
function closeEditConfigModal() {
  document.getElementById('editConfigModal').classList.remove('open');
}
async function submitEditConfig() {
  const msg = document.getElementById('editConfigMsg');
  const advancedOn = document.getElementById('editConfigAdvancedToggle').checked;
  let config;
  if (advancedOn) {
    try {
      config = JSON.parse(document.getElementById('editConfigJson').value || '{}');
    } catch (e) {
      msg.innerHTML = `<span style="color:var(--loss)">Invalid config JSON: ${escapeHtml(e.message)}</span>`;
      return;
    }
  } else {
    config = readConfigFromFields('editConfigFields', Detail._editConfigBase);
  }
  msg.innerHTML = '<span class="spinner"></span> Saving…';
  // The server re-checks "still paused?" itself (a status change could
  // have happened in another tab while this modal was open) -- a 409
  // here reads the same as the one Resume can now return, not a new
  // error shape to handle.
  const { ok, data } = await Api.updateDeployment(Detail._id, { config });
  if (!ok) { msg.innerHTML = `<span style="color:var(--loss)">${escapeHtml(data.detail || 'Failed')}</span>`; return; }
  msg.innerHTML = '<span style="color:var(--gain)">✓ Saved — applies on next Resume</span>';
  setTimeout(() => { closeEditConfigModal(); Detail.load(Detail._id); }, 700);
}
