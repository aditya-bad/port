// live_deploy — Dashboard view: the cross-strategy birds-eye view.
// Nothing here is per-deployment, it's everything combined — see
// README's Step 13 for why this needed real backend aggregate
// endpoints (GET /positions, GET /trades/recent) rather than the
// frontend fetching every deployment's own data and merging it here.

const Dashboard = {
  async load() {
    document.getElementById('dashStats').innerHTML = spinnerHtml();
    document.getElementById('dashPositions').innerHTML = spinnerHtml();
    document.getElementById('dashActivity').innerHTML = spinnerHtml();
    document.getElementById('dashInstruments').innerHTML = spinnerHtml();

    const [deployments, positions, trades, instruments] = await Promise.all([
      Api.listDeployments(),
      Api.getAllPositions('open'),
      Api.getRecentTrades(20),
      Api.listInstruments(),
    ]);

    this.renderStats(deployments);
    this.renderPositions(positions);
    this.renderActivity(trades);
    this.renderInstruments(instruments);
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

    const breakdown = live
      .map(d => ({ name: d.deployment_name, pnl: (d.realized_pnl || 0) + (d.unrealized_pnl || 0) }))
      .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
      .slice(0, 6)
      .map(d => `<div class="row"><span>${escapeHtml(d.name)}</span><b class="${pnlClass(d.pnl)}">${fmtSignedMoney(d.pnl)}</b></div>`)
      .join('');

    el.innerHTML = `
      <div class="stat-card">
        <div class="stat-label">Total P&amp;L (active + paused)</div>
        <div class="stat-value ${pnlClass(total)}">${fmtSignedMoney(total)}</div>
        <div class="stat-sub">
          <div class="row"><span>Realized</span><b class="${pnlClass(totalRealized)}">${fmtSignedMoney(totalRealized)}</b></div>
          <div class="row"><span>Unrealized</span><b class="${pnlClass(totalUnrealized)}">${fmtSignedMoney(totalUnrealized)}</b></div>
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
      <tbody>${positions.map(p => `<tr>
        <td>${escapeHtml(p.symbol)}</td>
        <td><a href="#/deployments/${p.deployment_id}">${escapeHtml(p.deployment_name)}</a></td>
        <td>${escapeHtml(p.strategy_name)}</td>
        <td>${p.side}</td>
        <td>${fmtNum(p.qty)}</td>
        <td>${fmtNum(p.avg_entry_price)}</td>
        <td>${p.current_price != null ? fmtNum(p.current_price) : '—'}</td>
        <td class="${pnlClass(p.unrealized_pnl)}">${p.unrealized_pnl != null ? fmtSignedMoney(p.unrealized_pnl) : '—'}</td>
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
  if (!token) { msg.innerHTML = '<span style="color:var(--red)">Instrument token is required</span>'; return; }
  const { ok, data } = await Api.addInstrument(token, symbol);
  if (!ok) { msg.innerHTML = `<span style="color:var(--red)">${data.detail || 'Failed'}</span>`; return; }
  msg.innerHTML = '<span style="color:var(--green)">✓ Subscribed</span>';
  setTimeout(() => { closeInstrumentModal(); Dashboard.load(); }, 600);
}
