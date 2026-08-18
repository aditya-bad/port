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

    const [deployments, calendarRows, positions, trades, instruments] = await Promise.all([
      Api.listDeployments(),
      Api.getPnlDigest('day', 371),
      Api.getAllPositions('open'),
      Api.getRecentTrades(20),
      Api.listInstruments(),
    ]);

    this.renderStats(deployments);
    document.getElementById('dashCalendar').innerHTML = renderPnlHeatmap(calendarRows);
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
    // record (visible via its own Detail page).
    const live = deployments.filter(d => d.status !== 'stopped');
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
