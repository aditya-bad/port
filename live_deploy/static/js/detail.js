// live_deploy — Strategy Detail view: the deep-dive for one deployment.
// Header + 4 tabs (Config / Positions / Trades / Stats). This is the
// tab the trade-reason logging retrofit was actually FOR — Trades
// keeps the table scannable (time/action/symbol/price/reason + a
// trigger-type badge) and reveals the full trigger_values/target_basis/
// resulting_state on click, rather than cramming structured metadata
// into visible columns or dumping raw JSON into every row by default.

const Detail = {
  _id: null,
  _tab: 'positions',   // most immediately useful thing to see on arrival
  _trades: [],
  _openTradeRows: new Set(),

  async load(id) {
    this._stopLivePositionUpdates();   // leaving whatever deployment/tab was showing before
    this._id = id;
    this._trades = [];
    this._openTradeRows = new Set();
    document.getElementById('detailHeader').innerHTML = spinnerHtml();
    document.getElementById('detailTabs').innerHTML = '';
    document.getElementById('detailBody').innerHTML = spinnerHtml();

    let dep;
    try {
      dep = await Api.getDeployment(id);
    } catch (e) {
      document.getElementById('detailHeader').innerHTML =
        emptyHtml(`No such deployment (it may have been removed). <a href="#/deployments">Back to Deployed Strategies</a>`);
      document.getElementById('detailTabs').innerHTML = '';
      document.getElementById('detailBody').innerHTML = '';
      return;
    }
    this._dep = dep;
    this.renderHeader(dep);
    this.renderTabs();
    await this.renderBody();
  },

  renderHeader(dep) {
    document.getElementById('detailHeader').innerHTML = `
      <div class="detail-header">
        <div>
          <h1>${escapeHtml(dep.deployment_name)} <span class="tag tag-${dep.status}">${dep.status}</span>
            ${!dep.strategy_registered ? '<span class="tag tag-warn">unregistered</span>' : ''}
            ${!dep.include_in_reports ? '<span class="tag tag-warn" title="Excluded from Dashboard, Portfolio, and Reports — toggle it back on from Edit">excluded from reports</span>' : ''}
          </h1>
          <div class="card-sub">${escapeHtml(dep.strategy_name)} · ${dep.mode}</div>
          <div class="card-meta" style="margin-top:10px;">
            <span>Capital: <b>${fmtMoney(dep.initial_capital)}</b></span>
            <span>Cash: <b>${fmtMoney(dep.current_cash)}</b></span>
            <span>Realized: <b class="${pnlClass(dep.realized_pnl)}">${fmtSignedMoney(dep.realized_pnl)}</b></span>
          </div>
          <!-- Unrealized deliberately NOT shown here any more -- it lived
               here as a frozen snapshot from page load with nothing to
               keep it current between reloads (same class of gap the
               Positions table itself had before Step 61's live wiring).
               Moved to that table's own live-updating Total row instead
               (see renderPositions() below) -- one live number, not two
               places that could show two different values depending on
               how stale each one happened to be, Zerodha-style. -->
          ${dep.notes ? `<div class="card-sub" style="margin-top:8px; white-space:pre-wrap;">📝 ${escapeHtml(dep.notes)}</div>` : ''}
        </div>
        <div class="card-actions">
          <button class="btn btn-secondary btn-sm" onclick="Detail.openEditModal()">Edit</button>
          ${dep.status === 'active' ? `<button class="btn btn-secondary btn-sm" onclick="Detail.pause()">Pause</button>` : ''}
          ${dep.status === 'paused' ? `<button class="btn btn-secondary btn-sm" onclick="Detail.resume()">Resume</button>` : ''}
          ${dep.status !== 'stopped' ? `<button class="btn btn-danger btn-sm" onclick="Detail.stop()">Stop</button>` : ''}
          ${dep.status === 'stopped' ? `<button class="btn btn-danger btn-sm" onclick="Detail.deleteDeployment()">Delete</button>` : ''}
        </div>
      </div>
    `;
  },

  renderTabs() {
    const tabs = [['config', 'Config'], ['positions', 'Positions'], ['trades', 'Trades'], ['stats', 'Stats'], ['calendar', 'Calendar'], ['events', 'Activity']];
    document.getElementById('detailTabs').innerHTML = tabs.map(([key, label]) =>
      `<button class="${this._tab === key ? 'active' : ''}" onclick="Detail.switchTab('${key}')">${label}</button>`
    ).join('');
  },

  async switchTab(tab) {
    if (tab !== 'positions') this._stopLivePositionUpdates();   // leaving the positions tab
    this._tab = tab;
    this.renderTabs();
    document.getElementById('detailBody').innerHTML = spinnerHtml();
    await this.renderBody();
  },

  async renderBody() {
    // A tab's own fetch can fail for real reasons (the deployment was
    // stopped/removed mid-session, a transient network/DB hiccup) — an
    // uncaught rejection here would otherwise leave the tab stuck on
    // its loading spinner forever with no visible explanation.
    try {
      if (this._tab === 'config') return this.renderConfig();
      if (this._tab === 'positions') return await this.renderPositions();
      if (this._tab === 'trades') return await this.renderTrades();
      if (this._tab === 'stats') return await this.renderStats();
      if (this._tab === 'calendar') return await this.renderCalendar();
      if (this._tab === 'events') return await this.renderEvents();
    } catch (e) {
      console.error('Detail tab render failed:', e);
      document.getElementById('detailBody').innerHTML =
        emptyHtml(`Could not load this tab's data — ${escapeHtml(e.message || String(e))}`);
    }
  },

  // ── Config ──────────────────────────────────────────────────────
  // Editable, but ONLY while paused (see DeploymentUpdate's own
  // docstring, app/deployments/schemas.py, for the full reasoning) —
  // the button only ever renders in that state; every other status
  // gets an explanatory note instead of a disabled/confusing button.
  renderConfig() {
    const cfg = this._dep.config || {};
    const keys = Object.keys(cfg).sort();
    const body = document.getElementById('detailBody');
    const paused = this._dep.status === 'paused';
    const editRow = `
      <div class="table-note" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
        <span>${paused
          ? 'This deployment is paused — safe to edit config now, applies on next Resume.'
          : `Pause this deployment to edit its config${this._dep.status === 'stopped' ? ' (a stopped deployment can\'t be resumed, so editing here would never take effect)' : ''}.`}</span>
        ${paused ? `<button class="btn btn-secondary btn-sm" onclick="Detail.openEditConfigModal()">Edit config</button>` : ''}
      </div>
    `;
    if (!keys.length) {
      body.innerHTML = editRow + emptyHtml('No config stored for this deployment.');
      return;
    }
    body.innerHTML = editRow + `
      <div class="table-wrap"><table class="kv-table"><tbody>
        ${keys.map(k => `<tr><td>${escapeHtml(k)}</td><td>${formatConfigValue(cfg[k])}</td></tr>`).join('')}
      </tbody></table></div>
    `;
  },

  // ── Positions ───────────────────────────────────────────────────
  // Price/P&L cells (plus the Total row's own combined figure) update
  // LIVE off the same /sse/ticks stream the ticker bar uses, via
  // window.LivePnl (index.html) — previously this table was a one-time
  // snapshot from page load with no live update at all (no polling, no
  // live wiring), so it went stale the instant you stopped reloading
  // the whole page. Only these specific cells are touched per tick —
  // everything else about the table (rows, sort order, other tabs) is
  // untouched, so this can't disrupt anything the way a full re-render
  // would. The header's own former "Unrealized" stat (see
  // renderHeader() above) now lives ONLY here, as the Total row below —
  // one live number instead of two places that could each show a
  // different, differently-stale value.
  _livePnlHandler: null,

  _stopLivePositionUpdates() {
    window.LivePnl.untrack(this._livePnlHandler);
    this._livePnlHandler = null;
  },

  async renderPositions() {
    this._stopLivePositionUpdates();   // never stack trackers across re-renders/tab switches
    const rows = await Api.getPositions(this._id, 'open');
    const body = document.getElementById('detailBody');
    if (!rows.length) {
      body.innerHTML = emptyHtml('No open positions');
      return;
    }
    const startingTotal = rows.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
    body.innerHTML = `
      <div class="table-wrap">
      <table><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Avg</th><th>Price</th><th>Unrealized</th></tr></thead>
      <tbody>${rows.map(p => `<tr data-position-id="${p.id}">
        <td>${escapeHtml(p.symbol)}</td><td>${p.side}</td><td>${fmtNum(p.qty)}</td>
        <td>${fmtNum(p.avg_entry_price)}</td>
        <td class="live-price">${p.current_price != null ? fmtNum(p.current_price) : '—'}</td>
        <td class="live-pnl ${pnlClass(p.unrealized_pnl)}">${p.unrealized_pnl != null ? fmtSignedMoney(p.unrealized_pnl) : '—'}</td>
      </tr>`).join('')}</tbody>
      <tfoot><tr class="positions-total-row">
        <td colspan="4"></td>
        <td><b>Total</b></td>
        <td class="live-pnl-total ${pnlClass(startingTotal)}">${fmtSignedMoney(startingTotal)}</td>
      </tr></tfoot>
      </table>
      </div>
    `;

    this._livePnlHandler = window.LivePnl.track(rows, ({ pnlFor, priceFor, totalPnl }) => {
      // Cheap guard against a stale tracker outliving its tab/page —
      // switchTab()/load() both untrack, but a tick already in flight
      // when that happened could still land here microseconds later.
      if (this._tab !== 'positions') return;
      for (const p of rows) {
        const price = priceFor(p.instrument_token);
        if (price == null) continue;
        const row = body.querySelector(`tr[data-position-id="${p.id}"]`);
        if (!row) continue;
        const pnl = pnlFor(p.id);
        row.querySelector('.live-price').textContent = fmtNum(price);
        const pnlCell = row.querySelector('.live-pnl');
        if (pnl != null) {
          pnlCell.textContent = fmtSignedMoney(pnl);
          pnlCell.className = `live-pnl ${pnlClass(pnl)}`;
        }
      }
      const combined = totalPnl();
      const totalCell = body.querySelector('.live-pnl-total');
      if (totalCell && combined != null) {
        totalCell.textContent = fmtSignedMoney(combined);
        totalCell.className = `live-pnl-total ${pnlClass(combined)}`;
      }
    });
  },

  // ── Trades — expandable rows + trigger badges ──────────────────
  async renderTrades() {
    const data = await Api.getTrades(this._id, 200);
    this._trades = data.lots;
    const body = document.getElementById('detailBody');
    if (!this._trades.length) {
      body.innerHTML = emptyHtml('No trades yet');
      return;
    }
    body.innerHTML = `
      <div class="table-note" style="display:flex; justify-content:flex-end; margin-bottom:8px;">
        <button class="btn btn-secondary btn-sm" onclick="Detail.exportTradesCsv()">⭳ Export CSV</button>
      </div>
      <div class="table-wrap">
      <table><thead><tr><th>Time</th><th>Action</th><th>Symbol</th><th>Price</th><th>Reason</th></tr></thead>
      <tbody>${this._trades.map((l, i) => this._tradeRowHtml(l, i)).join('')}</tbody></table>
      </div>
      <div class="table-note">${data.total} total${data.total > this._trades.length ? ` (showing latest ${this._trades.length})` : ''} — click a row for the full trigger metadata</div>
    `;
  },

  // Exports the FULL trade history, not just the (possibly truncated
  // to 200) rows currently rendered on screen — a records/backup export
  // should never silently leave out older fills just because the table
  // view caps what it displays. Purely client-side: no backend endpoint,
  // this is a records convenience, not a data interchange format
  // anything else in this app reads back (see toCsv/downloadCsv in
  // api.js).
  async exportTradesCsv() {
    const data = await Api.getTrades(this._id, 100000);
    if (!data.lots.length) { alert('No trades to export yet.'); return; }
    const csv = toCsv(data.lots, [
      { label: 'Time', key: r => fmtDateTime(r.executed_at) },
      { label: 'Action', key: 'action' },
      { label: 'Symbol', key: 'symbol' },
      { label: 'Quantity', key: 'qty' },
      { label: 'Price', key: 'price' },
      { label: 'Reason', key: r => r.reason || '' },
      { label: 'Metadata', key: r => (r.metadata && Object.keys(r.metadata).length) ? r.metadata : '' },
    ]);
    const safeName = (this._dep.deployment_name || this._id).replace(/[^a-z0-9_-]+/gi, '_');
    downloadCsv(`${safeName}_trades.csv`, csv);
  },

  _tradeRowHtml(lot, i) {
    const open = this._openTradeRows.has(i);
    return `
      <tr class="trade-row ${open ? 'open' : ''}" onclick="Detail.toggleTradeRow(${i})">
        <td>${fmtDateTime(lot.executed_at)}</td>
        <td>${lot.action}</td>
        <td>${escapeHtml(lot.symbol)}</td>
        <td>${fmtNum(lot.price)}</td>
        <td>${escapeHtml(lot.reason || '')}${triggerBadgeHtml(lot.reason)}</td>
      </tr>
      <tr class="trade-detail-row" id="trade-detail-${i}" style="display:${open ? 'table-row' : 'none'}">
        <td colspan="5">${this._tradeMetaHtml(lot)}</td>
      </tr>
    `;
  },

  toggleTradeRow(i) {
    const row = document.getElementById(`trade-detail-${i}`);
    const isOpen = this._openTradeRows.has(i);
    if (isOpen) { this._openTradeRows.delete(i); row.style.display = 'none'; }
    else { this._openTradeRows.add(i); row.style.display = 'table-row'; }
    // Toggle the arrow indicator on the trigger row above it without a
    // full re-render (row content itself doesn't change).
    row.previousElementSibling.classList.toggle('open', !isOpen);
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

  // ── Activity (deployment_events) — the audit trail behind pause/
  // resume/create and every fill, PLUS strategy_error: a strategy's own
  // on_tick raising an exception (a bad resolver call, a transient
  // NoKiteSession, anything) is caught at the runner level and recorded
  // here rather than crashing the deployment — which means a silently
  // failing strategy (still "active", never trading) was previously
  // invisible anywhere in the UI. This tab is that visibility. ─────────
  _openEventRows: new Set(),

  async renderEvents() {
    this._events = await Api.getEvents(this._id, 200);
    this._openEventRows = new Set();
    const body = document.getElementById('detailBody');
    if (!this._events.length) {
      body.innerHTML = emptyHtml('No activity recorded yet.');
      return;
    }
    const errorCount = this._events.filter(e => e.event_type === 'strategy_error').length;
    body.innerHTML = `
      ${errorCount > 0 ? `<div class="table-note" style="color:var(--loss); margin-bottom:10px;">
        ⚠ ${errorCount} strategy error${errorCount === 1 ? '' : 's'} recorded — click a
        <span class="tag tag-error" style="margin:0 2px;">strategy_error</span> row below for details.
      </div>` : ''}
      <div class="table-wrap">
      <table><thead><tr><th>Time</th><th>Event</th><th>Message</th></tr></thead>
      <tbody>${this._events.map((e, i) => this._eventRowHtml(e, i)).join('')}</tbody></table>
      </div>
    `;
  },

  _eventTagClass(eventType) {
    if (eventType === 'strategy_error') return 'tag-error';
    if (eventType === 'paused') return 'tag-paused';
    if (eventType === 'resumed' || eventType === 'created') return 'tag-active';
    if (eventType.startsWith('fill_')) return 'tag-info';
    return 'tag-warn';
  },

  _eventRowHtml(event, i) {
    const open = this._openEventRows.has(i);
    const hasMeta = event.metadata && Object.keys(event.metadata).length > 0;
    return `
      <tr class="trade-row ${open ? 'open' : ''}" ${hasMeta ? `onclick="Detail.toggleEventRow(${i})"` : ''}>
        <td>${fmtDateTime(event.created_at)}</td>
        <td><span class="tag ${this._eventTagClass(event.event_type)}">${escapeHtml(event.event_type)}</span></td>
        <td>${escapeHtml(event.message || '')}</td>
      </tr>
      ${hasMeta ? `<tr class="trade-detail-row" id="event-detail-${i}" style="display:${open ? 'table-row' : 'none'}">
        <td colspan="3">${renderJsonBlock('metadata', event.metadata)}</td>
      </tr>` : ''}
    `;
  },

  toggleEventRow(i) {
    const row = document.getElementById(`event-detail-${i}`);
    const isOpen = this._openEventRows.has(i);
    if (isOpen) { this._openEventRows.delete(i); row.style.display = 'none'; }
    else { this._openEventRows.add(i); row.style.display = 'table-row'; }
  },

  // ── Stats ───────────────────────────────────────────────────────
  async renderStats() {
    const [report, allTrades, closedPositions, snapshots] = await Promise.all([
      Api.getReport(this._id),
      Api.getTrades(this._id, 2000),
      Api.getPositions(this._id, 'closed'),
      Api.getSnapshots(this._id),
    ]);
    const body = document.getElementById('detailBody');

    // Trigger breakdown -- the actual point of the trade-reason logging
    // retrofit: if a strategy is expected to hit e.g. checkpoints
    // regularly and this shows zero, that's visible immediately.
    const counts = {};
    allTrades.lots.forEach(l => {
      const r = l.reason || '(no reason recorded)';
      counts[r] = (counts[r] || 0) + 1;
    });
    const breakdown = Object.entries(counts).sort((a, b) => b[1] - a[1]);

    // P&L by Exit Reason -- a DIFFERENT cut than the fill-count
    // breakdown above: how much did closing for each reason actually
    // make or lose, not just how often it fired. Every fill (entries,
    // adjustments, exits alike) counts toward the breakdown above, but
    // only a CLOSED position's own realized_pnl is real, settled money
    // — and it's attributed to the reason of whichever lot actually
    // closed it (that position's own last lot by executed_at), since a
    // multi-lot position's earlier fills (an entry, an adjustment) may
    // carry a different reason than what finally closed it out.
    const lotsByPosition = {};
    allTrades.lots.forEach(l => {
      (lotsByPosition[l.position_id] = lotsByPosition[l.position_id] || []).push(l);
    });
    const pnlByReason = {};   // reason -> { pnl, count }
    closedPositions.forEach(p => {
      const posLots = (lotsByPosition[p.id] || []).slice()
        .sort((a, b) => new Date(a.executed_at) - new Date(b.executed_at));
      const lastLot = posLots[posLots.length - 1];
      const reason = (lastLot && lastLot.reason) || '(no reason recorded)';
      if (!pnlByReason[reason]) pnlByReason[reason] = { pnl: 0, count: 0 };
      pnlByReason[reason].pnl += (p.realized_pnl || 0);
      pnlByReason[reason].count += 1;
    });
    const pnlBreakdown = Object.entries(pnlByReason).sort((a, b) => b[1].pnl - a[1].pnl);

    // Average holding period, from closed positions' own opened_at/closed_at.
    const durations = closedPositions
      .filter(p => p.opened_at && p.closed_at)
      .map(p => new Date(p.closed_at) - new Date(p.opened_at));
    const avgHoldMs = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null;

    // Profit factor + largest win/loss -- from each CLOSED position's
    // own realized_pnl (position-level, not per-lot: a position can
    // span several lots, but realized_pnl already nets the whole thing,
    // so summing per-position avoids double-counting a multi-lot close).
    const pnls = closedPositions.map(p => p.realized_pnl).filter(v => v != null);
    const grossWin = pnls.filter(v => v > 0).reduce((a, b) => a + b, 0);
    const grossLoss = pnls.filter(v => v < 0).reduce((a, b) => a + b, 0);   // negative
    const profitFactor = grossLoss < 0 ? grossWin / Math.abs(grossLoss) : (grossWin > 0 ? Infinity : null);
    const largestWin = pnls.length ? Math.max(...pnls, 0) : null;
    const largestLoss = pnls.length ? Math.min(...pnls, 0) : null;

    // Total return -- realized + unrealized against the FIXED
    // initial_capital reference (same "capital, not compounding cash"
    // basis several strategies themselves size against — see e.g.
    // strangle_monthly_v2's Section 3/4).
    const totalPnl = (this._dep.realized_pnl || 0) + (this._dep.unrealized_pnl || 0);
    const totalReturnPct = this._dep.initial_capital ? (totalPnl / this._dep.initial_capital) * 100 : null;

    // Max drawdown -- largest peak-to-trough decline in the equity
    // curve's own total_value series (the same snapshot data already
    // fetched for the chart below, no extra request). Shared with
    // Compare's own drawdown column via computeMaxDrawdown (api.js).
    const drawdown = computeMaxDrawdown(snapshots);

    // Deployed-since / last-activity — operational context: is this
    // thing actually doing anything, and how long has it been running.
    const deployedMs = Date.now() - new Date(this._dep.created_at).getTime();
    const lastFill = allTrades.lots.length ? allTrades.lots[0] : null;   // newest-first, see GET .../trades

    body.innerHTML = `
      <div class="card-meta" style="margin-bottom:16px;">
        <span>Deployed <b>${fmtDuration(deployedMs)} ago</b> (${fmtDateTime(this._dep.created_at)})</span>
        <span>Last activity <b>${lastFill ? fmtDateTime(lastFill.executed_at) : '—'}</b></span>
      </div>

      <div class="stat-grid">
        <div class="stat-card">
          <div class="stat-label">Realized P&amp;L</div>
          <div class="stat-value ${pnlClass(report.total_realized_pnl)}">${fmtSignedMoney(report.total_realized_pnl)}</div>
          <div class="stat-sub">
            <div class="row"><span>Win rate</span><b>${fmtPct(report.win_rate_pct)}</b></div>
            <div class="row"><span>Avg win</span><b class="pos">${fmtMoney(report.avg_win)}</b></div>
            <div class="row"><span>Avg loss</span><b class="neg">${fmtMoney(report.avg_loss)}</b></div>
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
          <div class="stat-label">Positions</div>
          <div class="stat-value">${report.closed_positions + report.open_positions}</div>
          <div class="stat-sub">
            <div class="row"><span>Closed</span><b>${report.closed_positions}</b></div>
            <div class="row"><span>Open</span><b>${report.open_positions}</b></div>
            <div class="row"><span>Avg holding period</span><b>${avgHoldMs != null ? fmtDuration(avgHoldMs) : '—'}</b></div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Trigger breakdown</div>
          <div class="stat-value" style="font-size:13px;">${allTrades.lots.length} fill(s)</div>
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
          Which trigger actually closed each position, and what it made or lost — not just how
          often it fired (see Trigger breakdown above for that). Sorted by total contribution.
        </div>
        <div class="table-wrap">
        <table><thead><tr><th>Reason</th><th>Positions closed</th><th>Total P&amp;L</th><th>Avg P&amp;L</th></tr></thead>
        <tbody>${pnlBreakdown.map(([reason, v]) => `<tr>
          <td>${escapeHtml(reason)}${triggerBadgeHtml(reason)}</td>
          <td>${v.count}</td>
          <td class="${pnlClass(v.pnl)}">${fmtSignedMoney(v.pnl)}</td>
          <td class="${pnlClass(v.pnl / v.count)}">${fmtSignedMoney(v.pnl / v.count)}</td>
        </tr>`).join('')}</tbody></table>
        </div>
      </section>
      ` : ''}

      <section>
        <h2>Equity Curve</h2>
        ${drawdown ? `<div class="card-meta" style="margin-bottom:10px;">
          <span>Max drawdown <b class="neg">${fmtMoney(drawdown.abs)} (${drawdown.pct.toFixed(2)}%)</b>
            — largest peak-to-trough decline across every recorded snapshot</span>
        </div>` : ''}
        ${renderEquityChart(snapshots)}
      </section>
    `;
  },

  // ── Calendar — this deployment's own daily P&L as a GitHub-style
  // heatmap (see renderPnlHeatmap, api.js), backed by GET
  // /deployments/{id}/pnl-digest (deployment-scoped twin of the
  // Reports page's portfolio-wide GET /portfolio/pnl-digest). Its own
  // tab rather than folded into Stats -- a full-year grid is a big
  // enough visual element to earn one, and Stats was already dense.
  async renderCalendar() {
    const rows = await Api.getPnlDigestForDeployment(this._id, 'day', 400);
    const body = document.getElementById('detailBody');
    body.innerHTML = `
      <section>
        <h2>P&amp;L Calendar</h2>
        ${renderPnlHeatmap(rows)}
      </section>
    `;
  },

  // ── Header actions ──────────────────────────────────────────────
  openEditModal() {
    document.getElementById('editDeploymentName').value = this._dep.deployment_name;
    document.getElementById('editDeploymentNotes').value = this._dep.notes || '';
    document.getElementById('editDeploymentIncludeInReports').checked = this._dep.include_in_reports;
    document.getElementById('editDeploymentMsg').textContent = '';
    document.getElementById('editDeploymentModal').classList.add('open');
  },

  // ── Edit config (Step 51) — only ever opened from renderConfig's own
  // paused-only button, but the paused check is enforced server-side
  // too (see the PATCH handler), not just gated by hiding the button.
  // Reuses the exact same field-widget machinery as the Deploy modal
  // (configFieldHtml family, api.js) with its own idPrefix/container
  // ids and its own _editConfigBase, entirely independent of Catalog's
  // own deploy-time state. ─────────────────────────────────────────────
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
      alert(data.detail || 'Could not resume — check its config on the Config tab.');
    }
    this.load(this._id);
  },
  async stop() {
    const forceClose = confirm(
      'Stop this deployment.\n\nOK = force-close any open position at the last known price.\nCancel = only stop if already flat.'
    );
    const r = await Api.stopDeployment(this._id, forceClose);
    if (!r.ok) {
      const data = await r.json();
      alert(data.detail || 'Could not stop — it may have open positions. Try again and confirm force-close.');
    }
    this.load(this._id);
  },
  async deleteDeployment() {
    // Only ever offered while stopped (see the header's own status
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

function fmtDuration(ms) {
  if (ms == null) return '—';
  const sec = Math.floor(ms / 1000);
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

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
  if (!name) { msg.innerHTML = '<span style="color:var(--loss)">Deployment name cannot be blank</span>'; return; }
  msg.innerHTML = '<span class="spinner"></span> Saving…';
  const { ok, data } = await Api.updateDeployment(Detail._id, {
    deployment_name: name, notes, include_in_reports: includeInReports,
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
