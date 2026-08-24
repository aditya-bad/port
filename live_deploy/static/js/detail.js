// live_deploy — Strategy Detail view: the deep-dive for one deployment.
// Header + 5 tabs (Config / Positions / Trades / Stats / Activity) --
// Stats also carries the Equity Curve, Recent Periods trend table, and
// P&L Calendar (the last two folded in from a separate Calendar tab,
// Step 86). Trades is the tab the trade-reason logging retrofit was
// actually FOR — keeps the table scannable (time/action/symbol/price/
// reason + a trigger-type badge) and reveals the full trigger_values/
// target_basis/resulting_state on click, rather than cramming
// structured metadata into visible columns or dumping raw JSON into
// every row by default.

const Detail = {
  _id: null,
  _tab: 'positions',   // most immediately useful thing to see on arrival
  _trades: [],
  _openTradeRows: new Set(),
  _calendarRange: 'recent',   // Stats tab's P&L Calendar range state (Step 74) -- reset per deployment below, persists across switching away from/back to Stats for the SAME deployment
  _statsTrendPeriod: 'day',   // Stats tab's Recent Periods bucketing (Step 86) -- same persistence rule as _calendarRange above
  _statsGranularity: 'position',   // Step 103 -- "per position" (episodes, every overlapping leg/adjustment/roll combined) vs "per trade" (raw positions rows); defaults to "position" per explicit user request

  async load(id) {
    this._stopLivePositionUpdates();   // leaving whatever deployment/tab was showing before
    this._id = id;
    this._trades = [];
    this._openTradeRows = new Set();
    this._calendarRange = 'recent';
    this._statsTrendPeriod = 'day';
    this._statsGranularity = 'position';
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
          <h1>${escapeHtml(dep.deployment_name)} <span class="tag tag-${dep.status}">${dep.status}</span></h1>
          ${deploymentTagsHtml(dep)}
          <div class="card-sub">${escapeHtml(dep.strategy_name)} · ${dep.mode}</div>
          <div class="card-meta" style="margin-top:10px;">
            <span>Capital: <b>${fmtMoney(dep.initial_capital)}</b></span>
            <span>Cash: <b>${fmtMoney(dep.current_cash)}</b></span>
            ${dep.open_cost_basis ? `<span title="Entry-price value of currently open positions -- a credit for a sold option's premium (not yet Realized until it's bought back), a debit for a bought one. Cash always equals Capital + Realized + this.">Open Cost ⓘ: <b class="${pnlClass(dep.open_cost_basis)}">${fmtSignedMoney(dep.open_cost_basis)}</b></span>` : ''}
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
    const tabs = [['config', 'Config'], ['positions', 'Positions'], ['trades', 'Trades'], ['stats', 'Stats'], ['events', 'Activity']];
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
    // `data-ux-col-key` on every th/td (including the header itself,
    // not just numeric ones) -- one real <td> per column in the tfoot
    // below, deliberately NOT a colspan-merged label cell: ux-v2's
    // table-wide column drag/resize/hide reorders tfoot cells by this
    // key, and a colspan cell can only ever "move" as one indivisible
    // block covering a FIXED span of columns -- it can't stay correctly
    // aligned once a single column inside that span gets dragged
    // somewhere else on its own. One tagged cell per column sidesteps
    // that entirely; "Total" itself just lives in the first column's
    // (Symbol's) own cell rather than a dedicated spacer.
    body.innerHTML = `
      <div class="table-wrap">
      <table><thead><tr>
        <th data-ux-col-key="symbol">Symbol</th><th data-ux-col-key="side">Side</th><th data-ux-col-key="qty">Qty</th>
        <th data-ux-col-key="avg">Avg</th><th data-ux-col-key="price">Price</th><th data-ux-col-key="unrealized">Unrealized</th>
      </tr></thead>
      <tbody>${rows.map(p => `<tr data-position-id="${p.id}">
        <td>${escapeHtml(p.symbol)}</td><td>${p.side}</td><td>${fmtNum(p.qty)}</td>
        <td>${fmtNum(p.avg_entry_price)}</td>
        <td class="live-price">${p.current_price != null ? fmtNum(p.current_price) : '—'}</td>
        <td class="live-pnl ${pnlClass(p.unrealized_pnl)}">${p.unrealized_pnl != null ? fmtSignedMoney(p.unrealized_pnl) : '—'}</td>
      </tr>`).join('')}</tbody>
      <tfoot><tr class="positions-total-row">
        <td data-ux-col-key="symbol"><b>Total</b></td>
        <td data-ux-col-key="side"></td>
        <td data-ux-col-key="qty"></td>
        <td data-ux-col-key="avg"></td>
        <td data-ux-col-key="price"></td>
        <td class="live-pnl-total ${pnlClass(startingTotal)}" data-ux-col-key="unrealized">${fmtSignedMoney(startingTotal)}</td>
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
  // Also owns the "Recent Periods" trend table and the P&L Calendar
  // (Step 86) -- both used to be a separate Calendar tab, folded in
  // here on request so everything about this deployment's performance
  // lives on one tab instead of being split across two. Each still
  // fetches independently and refreshes into its OWN sub-container
  // (#detailStatsTrend / #detailStatsCalendar) when its own period/year
  // control changes, rather than re-running this whole method — no
  // reason to re-fetch trades/positions/snapshots just because someone
  // switched the calendar's year.
  // Step 103 -- one "unit" per row the Performance stat-grid/P&L-by-
  // Exit-Reason table should treat as a single win/loss, shaped
  // identically regardless of granularity so everything downstream
  // (win rate, avg win/loss, profit factor, largest win/loss, avg
  // holding period, closed/open counts) is ONE code path, not two.
  // Step 106: the actual grouping is now the shared
  // groupPositionsIntoUnits (api.js) so Compare can use the exact same
  // logic without duplicating it -- this is a thin wrapper binding
  // Detail's own `this._statsGranularity` toggle to it.
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
  // of refetching the whole Stats tab. Reads the data renderStats
  // already cached on `this` (_statsAllPositions/_statsLotsByPosition/
  // _statsAllTradeLots) rather than taking parameters, same "cached on
  // `this`, not threaded through call args" pattern _trades already
  // uses for the Trades tab.
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

  async renderStats() {
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

  // ── Strategy-specific data (Step 87) — GET /deployments/{id}/
  // strategy-status and .../adjustment-histogram, both opt-in per
  // strategy (see StrategyBase.get_status_fields/ADJUSTMENT_GROUP_BY's
  // own docstrings). Neither renders anything at all for a strategy
  // that doesn't override its half of the contract — no empty section,
  // no "not available" placeholder cluttering every other strategy's
  // Stats tab. ─────────────────────────────────────────────────────

  // Live indicator values (e.g. pivot_supertrend*'s current trend/
  // value + pivot levels) -- a flex-wrap row of label/value pairs,
  // same visual language as the "Deployed X ago / Last activity" line
  // right above it. `source` distinguishes freshest-possible ("live",
  // this deployment is currently running) from "as of its last
  // pause/stop/daily checkpoint" ("persisted") so a paused deployment's
  // numbers aren't mistaken for real-time.
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
  // GET /portfolio/pnl-digest). Used to be its own Calendar tab; folded
  // into Stats (Step 86) on request -- these two helpers now only ever
  // refresh their own sub-container, not the whole Stats tab.
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

  // ── Header actions ──────────────────────────────────────────────
  async openEditModal() {
    document.getElementById('editDeploymentName').value = this._dep.deployment_name;
    document.getElementById('editDeploymentNotes').value = this._dep.notes || '';
    document.getElementById('editDeploymentIncludeInReports').checked = this._dep.include_in_reports;
    document.getElementById('editDeploymentNotificationsEnabled').checked = this._dep.notifications_enabled;
    document.getElementById('editDeploymentMsg').textContent = '';
    document.getElementById('editDeploymentModal').classList.add('open');

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
