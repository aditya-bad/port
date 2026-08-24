// live_deploy — Dashboard view: the cross-strategy birds-eye view.
// Nothing here is per-deployment, it's everything combined — see
// README's Step 13 for why this needed real backend aggregate
// endpoints (GET /positions, GET /trades/recent) rather than the
// frontend fetching every deployment's own data and merging it here.

const Dashboard = {
  // Default order, also the full set of valid ids -- see SectionOrder
  // (api.js) for how a saved order gets reconciled against this list
  // (kept ids stay where the user put them, new ones like a future
  // 6th section get appended rather than jumping to the top).
  _sectionIds: ['dashSectionStats', 'dashSectionCalendar', 'dashSectionPositions', 'dashSectionActivity', 'dashSectionInstruments'],

  // See window.LivePnl (index.html) -- one tracker covers both the KPI
  // card's Total/Unrealized figures AND the positions table's own
  // Price/Unrealized cells, off the SAME live tick stream, since
  // they're computed from the exact same open-positions data.
  _livePnlHandler: null,
  _liveTotals: { realized: 0, liveDeploymentIds: new Set(), breakdownList: [] },   // stashed by renderStats() for the live-update callback

  // Calendar heatmap's own range state (Step 74) -- 'recent' (the
  // default, last-365-days rolling view) or a real year number.
  // Persists across quiet background reloads (a fill/pause elsewhere
  // shouldn't reset the range someone deliberately picked), reset only
  // by a genuine fresh navigation to this view (router() calls load()
  // with no saved state to restore, same as every other Dashboard
  // control).
  _calendarRange: 'recent',

  // quiet=true is used for event-driven background refresh (see
  // connectEventSocket() near the bottom of index.html) -- skips the
  // spinner reset so already-rendered content just gets swapped for
  // fresh content directly, instead of flashing to a spinner and back
  // every time a fill/pause/whatever happens elsewhere in the app.
  // Real navigation TO this view still wants the spinner (there's
  // nothing on screen yet to hold onto), so this only ever passes
  // quiet=true from the auto-refresh path, never from router().
  async load(quiet = false) {
    window.LivePnl.untrack(this._livePnlHandler);   // never stack trackers across reloads
    this._livePnlHandler = null;

    const order = SectionOrder.getOrder('dashboard', this._sectionIds);
    SectionOrder.apply(document.getElementById('dashboardSections'), order);
    SectionOrder.syncButtons(order);

    if (!quiet) {
      document.getElementById('dashStats').innerHTML = spinnerHtml();
      document.getElementById('dashCalendar').innerHTML = spinnerHtml();
      document.getElementById('dashPositions').innerHTML = spinnerHtml();
      document.getElementById('dashActivity').innerHTML = spinnerHtml();
      document.getElementById('dashInstruments').innerHTML = spinnerHtml();
    }

    const [deployments, calendarRows, allPositions, allTrades, instruments] = await Promise.all([
      Api.listDeployments(),
      this._fetchCalendarRows(),
      Api.getAllPositions('open'),
      Api.getRecentTrades(20),
      Api.listInstruments(),
    ]);

    // Dashboard is a cross-deployment aggregate top to bottom (see this
    // file's own header comment) -- a deployment toggled out of reports
    // (include_in_reports=false, see the Detail page's own toggle)
    // is excluded here entirely, not just from the totals: its
    // positions don't show in the table, its fills don't show in the
    // activity feed, same as if it weren't deployed at all from this
    // view's point of view. Its OWN pages (Detail, its own row on the
    // Deployments list) are completely unaffected -- see queries.py's
    // per-function classification for the full reasoning.
    const excludedIds = new Set(deployments.filter(d => !d.include_in_reports).map(d => d.id));
    const positions = allPositions.filter(p => !excludedIds.has(p.deployment_id));
    const trades = allTrades.filter(t => !excludedIds.has(t.deployment_id));

    this.renderStats(deployments);
    this.renderCalendar(calendarRows);
    this.renderPositions(positions);
    this.renderActivity(trades);
    this.renderInstruments(instruments);
    markUpdated('dashUpdatedLabel');

    // Live-updates the KPI card's Total/Unrealized figures AND the
    // positions table's own Price/Unrealized cells, off the same real
    // tick stream the ticker bar uses -- see window.LivePnl (index.html).
    // Fixes both having been a frozen snapshot from whenever this load()
    // last ran (previously nothing touched them between reloads/the
    // event-driven quiet refresh, same class of gap Detail's own
    // Positions tab had before Step 61).
    const positionsTable = document.getElementById('dashPositions');
    const statsEl = document.getElementById('dashStats');
    this._livePnlHandler = window.LivePnl.track(positions, ({ pnlFor, priceFor, totalPnl }) => {
      // Same "active + paused only" scope as renderStats()'s own
      // totalRealized/totalUnrealized above -- a stopped deployment's
      // positions (an edge case; force_close normally clears them)
      // still light up in the table below, just excluded from this KPI
      // total, matching what the static figures already excluded them from.
      let combined = 0, anyPriced = false;
      for (const p of positions) {
        if (!this._liveTotals.liveDeploymentIds.has(p.deployment_id)) continue;
        const pnl = pnlFor(p.id);
        if (pnl != null) { combined += pnl; anyPriced = true; }
      }
      combined = anyPriced ? combined : null;
      if (combined != null) {
        const total = this._liveTotals.realized + combined;
        const totalEl = document.getElementById('dashTotalPnl');
        const unrealizedEl = document.getElementById('dashUnrealizedPnl');
        if (totalEl) {
          totalEl.textContent = fmtSignedMoney(total);
          totalEl.className = `stat-value ${pnlClass(total)}`;
        }
        if (unrealizedEl) {
          unrealizedEl.textContent = fmtSignedMoney(combined);
          unrealizedEl.className = pnlClass(combined);
        }
      }
      for (const p of positions) {
        const price = priceFor(p.instrument_token);
        if (price == null) continue;
        const row = positionsTable.querySelector(`tr[data-position-id="${p.id}"]`);
        if (!row) continue;
        const pnl = pnlFor(p.id);
        const priceCell = row.querySelector('.live-price');
        if (priceCell) priceCell.textContent = fmtNum(price);
        const pnlCell = row.querySelector('.live-pnl');
        if (pnlCell && pnl != null) {
          pnlCell.textContent = fmtSignedMoney(pnl);
          pnlCell.className = `live-pnl ${pnlClass(pnl)}`;
        }
      }

      // Per-deployment breakdown (the 3rd stat card) -- same "realized
      // fixed, unrealized recomputed live" shape as the Total card
      // above, per row instead of aggregated. Values update in place;
      // the ranking/which-6-are-shown does NOT re-sort per tick (see
      // renderStats()'s own comment on why).
      for (const d of this._liveTotals.breakdownList) {
        const combined = totalPnl(d.id);
        if (combined == null) continue;
        const pnl = d.realized + combined;
        const rowEl = statsEl.querySelector(`.row[data-deployment-id="${d.id}"] b`);
        if (!rowEl) continue;
        rowEl.textContent = fmtSignedMoney(pnl);
        rowEl.className = pnlClass(pnl);
      }
    });

    // The fixed "Right now" operational zone above the reorderable
    // widgets below -- see renderOperational()'s own header comment.
    UIKit.applySavedLayout('dashboardSections', 'dashboard');
    UIKit.setupSortableSections('dashboardSections', 'dashboard');
    await this.renderOperational();
  },

  // ── Calendar heatmap range (Step 74) ──────────────────────────────
  // 'recent' fetches the same rolling last-371-day window this always
  // used; a real year fetches that whole Jan-Dec grid instead (see
  // GET /portfolio/pnl-digest's own `year` param).
  _fetchCalendarRows() {
    return this._calendarRange === 'recent'
      ? Api.getPnlDigest('day', 371)
      : Api.getPnlDigest('day', 371, this._calendarRange);
  },

  renderCalendar(rows) {
    const year = this._calendarRange === 'recent' ? null : this._calendarRange;
    document.getElementById('dashCalendar').innerHTML = renderPnlHeatmap(rows, {
      year,
      selector: { value: this._calendarRange, onChange: 'Dashboard.changeCalendarRange(this.value)' },
    });
    // Always land on the newest column, not wherever scrollLeft=0
    // (the oldest data) happens to put you -- see scrollPnlHeatmapToEnd's
    // own comment for why this is a rAF, not a synchronous read.
    scrollPnlHeatmapToEnd('dashCalendar');
  },

  async changeCalendarRange(value) {
    this._calendarRange = value === 'recent' ? 'recent' : Number(value);
    const rows = await this._fetchCalendarRows();
    this.renderCalendar(rows);
  },

  moveSection(id, delta) {
    const order = SectionOrder.move('dashboard', this._sectionIds, id, delta);
    SectionOrder.apply(document.getElementById('dashboardSections'), order);
    SectionOrder.syncButtons(order);
  },

  renderStats(deployments) {
    const el = document.getElementById('dashStats');
    if (!deployments.length) {
      el.innerHTML = emptyHtml('No deployments yet — deploy a strategy from the Catalog to get started.');
      return;
    }

    // Aggregate P&L is scoped to active/paused deployments only — a
    // stopped deployment's history is done contributing to "how am I
    // doing right now," even though its rows still exist for the
    // record (visible via its own Detail page). Also excludes anything
    // toggled out of reports (include_in_reports=false) -- same
    // exclusion load() already applied to positions/trades above, kept
    // consistent here for the KPI card and breakdown list.
    const live = deployments.filter(d => d.status !== 'stopped' && d.include_in_reports);
    const totalRealized = live.reduce((s, d) => s + (d.realized_pnl || 0), 0);
    const totalUnrealized = live.reduce((s, d) => s + (d.unrealized_pnl || 0), 0);
    const total = totalRealized + totalUnrealized;

    const counts = { active: 0, paused: 0, stopped: 0 };
    deployments.forEach(d => { counts[d.status] = (counts[d.status] || 0) + 1; });

    // Ranked by starting pnl and left in that order from here on — NOT
    // re-sorted as live ticks move each one, which would mean rows
    // silently swapping position while you're reading them. Values
    // update in place (see load()'s live callback below); the ranking
    // itself only changes on the next real reload.
    const breakdownList = live
      .map(d => ({ id: d.id, name: d.deployment_name, realized: d.realized_pnl || 0,
                   pnl: (d.realized_pnl || 0) + (d.unrealized_pnl || 0) }))
      .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
      .slice(0, 6);
    const breakdown = breakdownList
      .map(d => `<div class="row" data-deployment-id="${d.id}"><span>${escapeHtml(d.name)}</span><b class="${pnlClass(d.pnl)}">${fmtSignedMoney(d.pnl)}</b></div>`)
      .join('');

    // Stashed for the live-tick callback in load() above -- Realized
    // never moves between reloads (only a fill changes it), so it's a
    // fixed input to the live Total figure, not something recomputed
    // per-tick itself. liveDeploymentIds keeps the live total scoped to
    // "active + paused" exactly like totalRealized/totalUnrealized above.
    this._liveTotals.realized = totalRealized;
    this._liveTotals.liveDeploymentIds = new Set(live.map(d => d.id));
    this._liveTotals.breakdownList = breakdownList;

    el.innerHTML = `
      <div class="stat-card">
        <div class="stat-label">Total P&amp;L (active + paused)</div>
        <div class="stat-value ${pnlClass(total)}" id="dashTotalPnl">${fmtSignedMoney(total)}</div>
        <div class="stat-sub">
          <div class="row"><span>Realized</span><b class="${pnlClass(totalRealized)}">${fmtSignedMoney(totalRealized)}</b></div>
          <div class="row"><span>Unrealized</span><b class="${pnlClass(totalUnrealized)}" id="dashUnrealizedPnl">${fmtSignedMoney(totalUnrealized)}</b></div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Deployment status</div>
        <div class="stat-value">${deployments.length} total</div>
        <div class="stat-sub">
          <div class="row"><span><span class="tag tag-active">active</span></span><b>${counts.active || 0}</b></div>
          <div class="row"><span><span class="tag tag-paused">paused</span></span><b>${counts.paused || 0}</b></div>
          <div class="row"><span><span class="tag tag-stopped">stopped</span></span><b>${counts.stopped || 0}</b></div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Per-deployment breakdown</div>
        <div class="stat-value" style="font-size:13px;">${live.length} live deployment(s)</div>
        <div class="stat-sub">${breakdown || '<div class="row"><span>—</span></div>'}</div>
      </div>
    `;
  },

  renderPositions(positions) {
    const el = document.getElementById('dashPositions');
    if (!positions.length) {
      el.innerHTML = emptyHtml('No open positions across any deployment.');
      return;
    }
    el.innerHTML = `
      <div class="table-wrap">
      <table><thead><tr>
        <th>Symbol</th><th>Deployment</th><th>Strategy</th><th>Side</th>
        <th>Qty</th><th>Avg</th><th>Price</th><th>Unrealized</th>
      </tr></thead>
      <tbody>${positions.map(p => `<tr data-position-id="${p.id}">
        <td>${escapeHtml(p.symbol)}</td>
        <td><a href="#/deployments/${p.deployment_id}">${escapeHtml(p.deployment_name)}</a></td>
        <td>${escapeHtml(p.strategy_name)}</td>
        <td>${p.side}</td>
        <td>${fmtNum(p.qty)}</td>
        <td>${fmtNum(p.avg_entry_price)}</td>
        <td class="live-price">${p.current_price != null ? fmtNum(p.current_price) : '—'}</td>
        <td class="live-pnl ${pnlClass(p.unrealized_pnl)}">${p.unrealized_pnl != null ? fmtSignedMoney(p.unrealized_pnl) : '—'}</td>
      </tr>`).join('')}</tbody></table>
      </div>
    `;
  },

  renderActivity(trades) {
    const el = document.getElementById('dashActivity');
    if (!trades.length) {
      el.innerHTML = emptyHtml('No trades recorded yet across any deployment.');
      return;
    }
    el.innerHTML = `
      <div class="table-wrap">
      <table><thead><tr>
        <th>Time</th><th>Deployment</th><th>Symbol</th><th>Action</th><th>Price</th><th>Reason</th>
      </tr></thead>
      <tbody>${trades.map(t => `<tr>
        <td>${fmtDateTime(t.executed_at)}</td>
        <td><a href="#/deployments/${t.deployment_id}">${escapeHtml(t.deployment_name)}</a></td>
        <td>${escapeHtml(t.symbol)}</td>
        <td>${t.action}</td>
        <td>${fmtNum(t.price)}</td>
        <td>${escapeHtml(t.reason || '')}${triggerBadgeHtml(t.reason)}</td>
      </tr>`).join('')}</tbody></table>
      </div>
    `;
  },

  // ── Instruments (kept off the main sidebar nav on purpose — this is
  // a small, infrequently-used control, not one of the four real
  // views; tucked at the bottom of the Dashboard instead of adding a
  // 5th nav item for it) ─────────────────────────────────────────────
  async renderInstruments(data) {
    const el = document.getElementById('dashInstruments');
    if (!data.subscribed.length) {
      el.innerHTML = emptyHtml('Nothing subscribed');
      return;
    }
    el.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Token</th><th>Source</th></tr></thead><tbody>
      ${data.subscribed.map(i => `<tr>
        <td>${escapeHtml(i.symbol)}</td><td>${i.instrument_token}</td>
        <td>${i.static ? 'tokens.json' : 'dynamic'}</td>
      </tr>`).join('')}
    </tbody></table></div>`;
  },

  // ── "Right now" operational zone — a fixed block above the
  // reorderable widgets below, deliberately NOT customizable/movable
  // like they are (see renderOperational() below): the KPI cards,
  // the "needs attention" list, and the live open-positions table are
  // operational truth, on every screen, always in the same place.
  // Unlike renderStats()/renderPositions() above (which exclude
  // anything toggled include_in_reports=false, matching the rest of
  // this cross-deployment view), this zone includes EVERY live
  // deployment regardless of that analytics opt-out — live risk should
  // never disappear from view just because a deployment is excluded
  // from performance reporting.
  _operationalLiveHandler: null,

  ensureOperationalZone() {
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
  },

  async renderOperational() {
    const zone = this.ensureOperationalZone();
    if (!zone) return;
    zone.innerHTML = '<div class="empty"><span class="spinner"></span> Building live operational view…</div>';

    window.LivePnl?.untrack(this._operationalLiveHandler);
    this._operationalLiveHandler = null;

    try {
      const [deployments, openPositions, recentTrades] = await Promise.all([
        Api.listDeployments(),
        Api.getAllPositions('open'),
        Api.getRecentTrades(20),
      ]);
      await Api.enrichDeployments(deployments, openPositions);
      this._operationalDeployments = deployments;
      const byId = new Map(deployments.map(d => [d.id, d]));
      const live = deployments.filter(d => d.status !== 'stopped');
      const activeSummaries = live.map(d => d._uxActive).filter(Boolean);

      const activePnl = activeSummaries.reduce((s, a) => s + Number(a.total_pnl || 0), 0);
      const todayRealized = deployments.reduce((s, d) => s + Number(d._uxActive?.today_realized_pnl || 0), 0);
      // How many deployments actually CONTRIBUTED to that total -- not
      // simply every deployment that exists, which would read as "N
      // deployments realized this today" when most of them may have
      // closed nothing at all today.
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
      // currently deployed."
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
      // stream. Surface the latest per deployment as persistent
      // dashboard attention instead of relying on an 8-second toast alone.
      const seenErrorDeps = new Set();
      (UIKit.notifications || []).filter(n => n.event_type === 'strategy_error').forEach(n => {
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
          <div class="ux-kpi ux-kpi-clickable" onclick="Dashboard.openActivePnlBreakdown()">
            <div class="ux-kpi-label">Active P&amp;L ⓘ</div>
            <div class="ux-kpi-value ${pnlClass(activePnl)}" id="uxActivePnlValue">${fmtSignedMoney(activePnl)}</div>
            <div class="ux-kpi-sub">
              <div class="ux-kpi-sub-row"><span>Intraday · Today</span><b class="${pnlClass(activeIntraday)}" id="uxIntradayPnl">${fmtSignedMoney(activeIntraday)}</b></div>
              <div class="ux-kpi-sub-row"><span>Positional · active cycles</span><b class="${pnlClass(activeCycles)}" id="uxCyclesPnl">${fmtSignedMoney(activeCycles)}</b></div>
            </div>
          </div>
          <div class="ux-kpi">
            <div class="ux-kpi-label">Today realized</div>
            <div class="ux-kpi-value ${pnlClass(todayRealized)}">${fmtSignedMoney(todayRealized)}</div>
            <div class="ux-kpi-sub"><div class="ux-kpi-sub-row"><span>Deployments with activity today</span><b>${todayRealizedDepCount}</b></div></div>
          </div>
          <div class="ux-kpi">
            <div class="ux-kpi-label">Open risk</div>
            <div class="ux-kpi-value ${pnlClass(openUnrealized)}" id="uxOpenRiskPnl">${fmtSignedMoney(openUnrealized)}</div>
            <div class="ux-kpi-sub">
              <div class="ux-kpi-sub-row"><span>Open positions</span><b>${openPositions.length}</b></div>
              <div class="ux-kpi-sub-row"><span>Deployments exposed</span><b>${Object.keys(positionsByDep).length}</b></div>
            </div>
          </div>
          <div class="ux-kpi">
            <div class="ux-kpi-label">Capital at work</div>
            <div class="ux-kpi-value">${fmtMoney(capitalAtWork)}</div>
            <div class="ux-kpi-sub">
              <div class="ux-kpi-sub-row"><span>Total live capital</span><b>${fmtMoney(totalCapital)}</b></div>
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
          ${this._operationalPositionsTable(openPositions, byId)}
        </div>`;

      // Recent Activity is operational, like open risk. Do not let a
      // performance-analytics opt-out hide live executions from it.
      this.renderActivity(recentTrades || []);
      document.getElementById('dashSectionPositions')?.classList.add('dash-section-superseded');
      this.applyWidgetDefaults();
      this.setupCustomize();

      if (window.LivePnl && openPositions.length) {
        const realizedByDep = new Map(activeSummaries.map(s => [s.deployment_id, Number(s.realized_pnl || 0)]));
        const activePositional = new Set(activeSummaries.filter(s => s.mode === 'positional' && s.active).map(s => s.deployment_id));
        const intradayIds = new Set(activeSummaries.filter(s => s.mode !== 'positional').map(s => s.deployment_id));
        this._operationalLiveHandler = window.LivePnl.track(openPositions, ({ pnlFor, priceFor, totalPnl }) => {
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
          UIKit.setLiveMoney('uxActivePnlValue', activeLive);
          UIKit.setLiveMoney('uxIntradayPnl', intradayLive);
          UIKit.setLiveMoney('uxCyclesPnl', cycleLive);
          if (totalOpen != null) UIKit.setLiveMoney('uxOpenRiskPnl', totalOpen);
          openPositions.forEach(p => {
            const row = zone.querySelector(`tr[data-ux-position-id="${p.id}"]`);
            if (!row) return;
            const px = priceFor(p.instrument_token);
            const pp = pnlFor(p.id);
            if (px != null) row.querySelector('.ux-live-price').textContent = fmtNum(px);
            if (pp != null) {
              const cell = row.querySelector('.ux-live-pnl');
              cell.textContent = fmtSignedMoney(pp);
              cell.className = `ux-live-pnl ${pnlClass(pp)}`;
            }
          });
        });
      }
      UIKit.enhanceTablesSoon();
    } catch (e) {
      console.error('Dashboard operational render failed', e);
      zone.innerHTML = `<div class="empty">Could not build the live operational summary — ${escapeHtml(e.message || String(e))}</div>`;
    }
  },

  _operationalPositionsTable(positions, byId) {
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
        <td class="ux-live-pnl ${pnlClass(p.unrealized_pnl)}">${p.unrealized_pnl != null ? fmtSignedMoney(p.unrealized_pnl) : '—'}</td>
      </tr>`;
    }).join('')}</tbody></table></div>`;
  },

  openActivePnlBreakdown() {
    const source = (typeof Deployments !== 'undefined' && Deployments._all?.length) ? Deployments._all : (this._operationalDeployments || []);
    if (!source.length) { window.location.hash = '#/deployments'; return; }
    const rows = source.filter(d => d.status !== 'stopped').map(d => ({ d, s: d._uxActive })).filter(x => x.s);
    UIKit.openDrawer('Active P&L', `
      <div class="table-note" style="margin-bottom:10px;">Intraday deployments reset at the IST trading date. Positional deployments use the currently-open strategic cycle and reset only after the whole cycle is flat.</div>
      <div class="table-wrap"><table><thead><tr><th>Deployment</th><th>Period</th><th>Realized</th><th>Open</th><th>Total</th></tr></thead><tbody>
      ${rows.map(({ d, s }) => `<tr class="ux-row-navigate" onclick="location.hash='#/deployments/${d.id}/overview'; UIKit.closeDrawer();">
        <td>${escapeHtml(d.deployment_name)}</td><td>${escapeHtml(s.period_label)}</td>
        <td class="${pnlClass(s.realized_pnl)}">${fmtSignedMoney(s.realized_pnl)}</td>
        <td class="${pnlClass(s.unrealized_pnl)}">${fmtSignedMoney(s.unrealized_pnl)}</td>
        <td class="${pnlClass(s.total_pnl)}"><b>${fmtSignedMoney(s.total_pnl)}</b></td>
      </tr>`).join('')}</tbody></table></div>`);
    UIKit.enhanceTablesSoon();
  },

  // ── Widget drag/drop and customize mode — the reorderable section
  // container below the fixed "Right now" zone above.
  applyWidgetDefaults() {
    const key = UIKit.visibilityKey('dashboard');
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
    Object.entries(visible).forEach(([id, isVisible]) => {
      const el = document.getElementById(id);
      if (el && !el.classList.contains('dash-section-superseded')) el.style.display = isVisible === false ? 'none' : '';
    });
    const sizes = safeJsonParse(localStorage.getItem(UIKit.sizeKey('dashboard')) || '{}', {});
    if (Object.values(sizes).some(v => v === 'half')) document.getElementById('dashboardSections')?.classList.add('ux-widget-grid');
  },

  setupCustomize() {
    const view = document.getElementById('view-dashboard');
    const actions = view?.querySelector('.view-header-actions');
    if (!view || !actions) return;
    if (!document.getElementById('uxCustomizeDashboardBtn')) {
      const btn = document.createElement('button');
      btn.id = 'uxCustomizeDashboardBtn';
      btn.className = 'btn btn-secondary btn-sm ux-customize-toggle';
      btn.textContent = 'Customize';
      btn.onclick = () => this.toggleCustomize();
      actions.appendChild(btn);
    }
    if (!document.getElementById('uxDashboardCustomizePanel')) {
      const panel = document.createElement('div');
      panel.id = 'uxDashboardCustomizePanel';
      panel.className = 'ux-customize-panel';
      document.getElementById('dashboardSections')?.parentNode.insertBefore(panel, document.getElementById('dashboardSections'));
    }
  },

  toggleCustomize() {
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
    const visible = safeJsonParse(localStorage.getItem(UIKit.visibilityKey('dashboard')) || '{}', {});
    const sizes = safeJsonParse(localStorage.getItem(UIKit.sizeKey('dashboard')) || '{}', {});
    panel.innerHTML = `<div class="table-note" style="margin-bottom:7px;">Drag widgets by the ⠿ handle on the page itself. The fixed "Right now" summary above is deliberately not customizable.</div>` + ids.map(id => `
      <div class="ux-customize-row">
        <label><input type="checkbox" style="width:auto;" ${visible[id] !== false ? 'checked' : ''} onchange="Dashboard.setWidgetVisible('${id}', this.checked)"> ${labels[id]}</label>
        <select onchange="Dashboard.setWidgetSize('${id}', this.value)"><option value="full" ${(sizes[id] || 'full') === 'full' ? 'selected' : ''}>Full width</option><option value="half" ${sizes[id] === 'half' ? 'selected' : ''}>Half width</option></select>
        <button class="btn btn-secondary btn-sm" onclick="document.getElementById('${id}').scrollIntoView({behavior:'smooth',block:'center'})">Locate</button>
      </div>`).join('') + `<div style="display:flex;justify-content:flex-end;margin-top:8px;"><button class="btn btn-secondary btn-sm" onclick="Dashboard.resetLayout()">Reset layout</button></div>`;
  },

  setWidgetVisible(id, visible) {
    const key = UIKit.visibilityKey('dashboard');
    const prefs = safeJsonParse(localStorage.getItem(key) || '{}', {});
    prefs[id] = visible;
    localStorage.setItem(key, JSON.stringify(prefs));
    const el = document.getElementById(id);
    if (el) el.style.display = visible ? '' : 'none';
  },

  setWidgetSize(id, size) {
    const key = UIKit.sizeKey('dashboard');
    const prefs = safeJsonParse(localStorage.getItem(key) || '{}', {});
    prefs[id] = size;
    localStorage.setItem(key, JSON.stringify(prefs));
    const el = document.getElementById(id);
    if (el) el.dataset.uxSize = size;
    document.getElementById('dashboardSections')?.classList.add('ux-widget-grid');
  },

  resetLayout() {
    localStorage.removeItem(UIKit.layoutKey('dashboard'));
    localStorage.removeItem(UIKit.visibilityKey('dashboard'));
    localStorage.removeItem(UIKit.sizeKey('dashboard'));
    this.applyWidgetDefaults();
    this.toggleCustomize();
    this.toggleCustomize();
  },
};

// ── Instrument subscribe modal (shared control, lives on Dashboard) ──
function openInstrumentModal() {
  document.getElementById('instToken').value = '';
  document.getElementById('instSymbol').value = '';
  document.getElementById('instrumentMsg').textContent = '';
  document.getElementById('instrumentModal').classList.add('open');
}
function closeInstrumentModal() {
  document.getElementById('instrumentModal').classList.remove('open');
}
async function submitInstrument() {
  const msg = document.getElementById('instrumentMsg');
  const token = Number(document.getElementById('instToken').value);
  const symbol = document.getElementById('instSymbol').value.trim();
  if (!token) { msg.innerHTML = '<span style="color:var(--loss)">Instrument token is required</span>'; return; }
  const { ok, data } = await Api.addInstrument(token, symbol);
  if (!ok) { msg.innerHTML = `<span style="color:var(--loss)">${data.detail || 'Failed'}</span>`; return; }
  msg.innerHTML = '<span style="color:var(--gain)">✓ Subscribed</span>';
  setTimeout(() => { closeInstrumentModal(); Dashboard.load(); }, 600);
}
