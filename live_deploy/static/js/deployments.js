// live_deploy — Deployed Strategies view: every deployment, filterable
// by status and strategy, with running P&L pulled in directly (no
// click-through needed just to see if something's currently winning or
// losing) and Pause/Resume/Stop available right from the row. Clicking
// a row (not a button) navigates to that deployment's Strategy Detail
// page — a real drill-down, not an inline expand.

const Deployments = {
  _all: [],
  _livePnlHandler: null,

  // quiet=true: event-driven background refresh -- see Dashboard.load()'s
  // own comment for why the spinner reset is skipped in that case.
  async load(quiet = false) {
    window.LivePnl.untrack(this._livePnlHandler);   // never stack trackers across reloads
    this._livePnlHandler = null;

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
    this._populateStrategyFilter();
    this.render();
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

  // Shared between render() and the live-tick handler below so both
  // always agree on "what's currently visible" -- the total row's live
  // updates need this SAME filtered set on every tick, not just at
  // render time, or changing a filter without a fresh tick arriving
  // yet would leave the total row summing the wrong rows.
  _filteredRows() {
    const statusFilter = document.getElementById('filterStatus').value;
    const strategyFilter = document.getElementById('filterStrategy').value;
    return this._all.filter(d =>
      (!statusFilter || d.status === statusFilter) &&
      (!strategyFilter || d.strategy_name === strategyFilter)
    );
  },

  render() {
    const el = document.getElementById('deploymentsTable');
    const rows = this._filteredRows();

    if (!this._all.length) {
      el.innerHTML = emptyHtml('No deployments yet. Deploy a strategy from the Catalog to create one.');
      return;
    }
    if (!rows.length) {
      el.innerHTML = emptyHtml('No deployments match the current filters.');
      return;
    }

    // Totals row (tfoot) -- scoped to whatever the current filters
    // actually show, not always every deployment, so it stays an
    // honest sum of what's on screen rather than a fixed portfolio-wide
    // figure that stops matching the visible rows the moment a filter
    // is applied. Capital/Cash/Realized are exact from this same fetch;
    // Unrealized starts here and then updates live below, same
    // Zerodha-style pattern as Detail's own Positions table Total row.
    //
    // Separately, ALSO excludes anything with include_in_reports=false
    // -- unlike the status/strategy filters above, this is not about
    // what's "shown" (a toggled-off deployment still gets its own row,
    // same as always) but about what counts toward the total, same
    // "total P&L ignores this strategy" contract as Dashboard/Portfolio.
    const reportRows = rows.filter(d => d.include_in_reports);
    const totalCapital = reportRows.reduce((s, d) => s + (d.initial_capital || 0), 0);
    const totalCash = reportRows.reduce((s, d) => s + (d.current_cash || 0), 0);
    const totalRealized = reportRows.reduce((s, d) => s + (d.realized_pnl || 0), 0);
    const totalUnrealized = reportRows.reduce((s, d) => s + (d.unrealized_pnl || 0), 0);
    const excludedCount = rows.length - reportRows.length;
    const totalLabel = excludedCount > 0
      ? `Total (${reportRows.length} of ${rows.length} shown — ${excludedCount} excluded)`
      : `Total (${rows.length} shown)`;

    el.innerHTML = `
      <div class="table-wrap">
      <table class="deploy-table"><thead><tr>
        <th>Name</th><th>Strategy</th><th>Status</th><th>Mode</th>
        <th>Capital</th><th>Cash</th><th>Realized</th><th>Unrealized</th><th>Actions</th>
      </tr></thead>
      <tbody>${rows.map(d => `
        <tr class="clickable-row" data-deployment-id="${d.id}" onclick="location.hash='#/deployments/${d.id}'">
          <td>
            <a href="#/deployments/${d.id}" onclick="event.stopPropagation()">${escapeHtml(d.deployment_name)}</a>
            ${!d.strategy_registered ? '<span class="tag tag-warn">unregistered</span>' : ''}
            ${deploymentTagsHtml(d)}
            ${d.notes ? `<div class="card-sub" style="margin-top:2px; max-width:260px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(d.notes)}">📝 ${escapeHtml(d.notes)}</div>` : ''}
          </td>
          <td>${escapeHtml(d.strategy_name)}</td>
          <td><span class="tag tag-${d.status}">${d.status}</span></td>
          <td>${d.mode}</td>
          <td>${fmtMoney(d.initial_capital)}</td>
          <td>${fmtMoney(d.current_cash)}</td>
          <td class="${pnlClass(d.realized_pnl)}">${fmtSignedMoney(d.realized_pnl)}</td>
          <td class="live-pnl ${pnlClass(d.unrealized_pnl)}">${fmtSignedMoney(d.unrealized_pnl)}</td>
          <td onclick="event.stopPropagation()">
            ${d.status === 'active' ? `<button class="btn btn-secondary btn-sm" onclick="Deployments.pause('${d.id}')">Pause</button>` : ''}
            ${d.status === 'paused' ? `<button class="btn btn-secondary btn-sm" onclick="Deployments.resume('${d.id}')">Resume</button>` : ''}
            ${d.status !== 'stopped' ? `<button class="btn btn-danger btn-sm" onclick="Deployments.stop('${d.id}')">Stop</button>` : ''}
            ${d.status === 'stopped' ? `<button class="btn btn-danger btn-sm" onclick="Deployments.deleteDeployment('${d.id}')">Delete</button>` : ''}
          </td>
        </tr>
      `).join('')}</tbody>
      <tfoot><tr class="positions-total-row">
        <td colspan="4"><b>${totalLabel}</b></td>
        <td>${fmtMoney(totalCapital)}</td>
        <td>${fmtMoney(totalCash)}</td>
        <td class="${pnlClass(totalRealized)}">${fmtSignedMoney(totalRealized)}</td>
        <td class="live-pnl-total ${pnlClass(totalUnrealized)}">${fmtSignedMoney(totalUnrealized)}</td>
        <td></td>
      </tr></tfoot>
      </table>
      </div>
    `;
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
    const forceClose = confirm(
      'Stop this deployment.\n\nOK = force-close any open position at the last known price.\nCancel = only stop if already flat.'
    );
    const r = await Api.stopDeployment(id, forceClose);
    if (!r.ok) {
      const data = await r.json();
      alert(data.detail || 'Could not stop — it may have open positions. Try again and confirm force-close.');
    }
    this.load();
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
};
