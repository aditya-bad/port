// live_deploy — Deployed Strategies view: every deployment, filterable
// by status and strategy, with running P&L pulled in directly (no
// click-through needed just to see if something's currently winning or
// losing) and Pause/Resume/Stop available right from the row. Clicking
// a row (not a button) navigates to that deployment's Strategy Detail
// page — a real drill-down, not an inline expand.

const Deployments = {
  _all: [],

  // quiet=true: event-driven background refresh -- see Dashboard.load()'s
  // own comment for why the spinner reset is skipped in that case.
  async load(quiet = false) {
    const el = document.getElementById('deploymentsTable');
    if (!quiet) el.innerHTML = spinnerHtml();
    this._all = await Api.listDeployments();
    this._populateStrategyFilter();
    this.render();
    markUpdated('deploymentsUpdatedLabel');
  },

  _populateStrategyFilter() {
    const select = document.getElementById('filterStrategy');
    const current = select.value;
    const names = [...new Set(this._all.map(d => d.strategy_name))].sort();
    select.innerHTML = '<option value="">All strategies</option>'
      + names.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');
    if (names.includes(current)) select.value = current;
  },

  render() {
    const el = document.getElementById('deploymentsTable');
    const statusFilter = document.getElementById('filterStatus').value;
    const strategyFilter = document.getElementById('filterStrategy').value;

    const rows = this._all.filter(d =>
      (!statusFilter || d.status === statusFilter) &&
      (!strategyFilter || d.strategy_name === strategyFilter)
    );

    if (!this._all.length) {
      el.innerHTML = emptyHtml('No deployments yet. Deploy a strategy from the Catalog to create one.');
      return;
    }
    if (!rows.length) {
      el.innerHTML = emptyHtml('No deployments match the current filters.');
      return;
    }

    el.innerHTML = `
      <div class="table-wrap">
      <table class="deploy-table"><thead><tr>
        <th>Name</th><th>Strategy</th><th>Status</th><th>Mode</th>
        <th>Capital</th><th>Cash</th><th>Realized</th><th>Unrealized</th><th>Actions</th>
      </tr></thead>
      <tbody>${rows.map(d => `
        <tr class="clickable-row" onclick="location.hash='#/deployments/${d.id}'">
          <td>
            ${escapeHtml(d.deployment_name)}
            ${!d.strategy_registered ? '<span class="tag tag-warn">unregistered</span>' : ''}
          </td>
          <td>${escapeHtml(d.strategy_name)}</td>
          <td><span class="tag tag-${d.status}">${d.status}</span></td>
          <td>${d.mode}</td>
          <td>${fmtMoney(d.initial_capital)}</td>
          <td>${fmtMoney(d.current_cash)}</td>
          <td class="${pnlClass(d.realized_pnl)}">${fmtSignedMoney(d.realized_pnl)}</td>
          <td class="${pnlClass(d.unrealized_pnl)}">${fmtSignedMoney(d.unrealized_pnl)}</td>
          <td onclick="event.stopPropagation()">
            ${d.status === 'active' ? `<button class="btn btn-secondary btn-sm" onclick="Deployments.pause('${d.id}')">Pause</button>` : ''}
            ${d.status === 'paused' ? `<button class="btn btn-secondary btn-sm" onclick="Deployments.resume('${d.id}')">Resume</button>` : ''}
            ${d.status !== 'stopped' ? `<button class="btn btn-danger btn-sm" onclick="Deployments.stop('${d.id}')">Stop</button>` : ''}
          </td>
        </tr>
      `).join('')}</tbody></table>
      </div>
    `;
  },

  async pause(id) {
    await Api.pauseDeployment(id);
    this.load();
  },
  async resume(id) {
    await Api.resumeDeployment(id);
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
