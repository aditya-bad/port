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
          </h1>
          <div class="card-sub">${escapeHtml(dep.strategy_name)} · ${dep.mode}</div>
          <div class="card-meta" style="margin-top:10px;">
            <span>Capital: <b>${fmtMoney(dep.initial_capital)}</b></span>
            <span>Cash: <b>${fmtMoney(dep.current_cash)}</b></span>
            <span>Realized: <b class="${pnlClass(dep.realized_pnl)}">${fmtSignedMoney(dep.realized_pnl)}</b></span>
            <span>Unrealized: <b class="${pnlClass(dep.unrealized_pnl)}">${fmtSignedMoney(dep.unrealized_pnl)}</b></span>
          </div>
          ${dep.notes ? `<div class="card-sub" style="margin-top:8px; white-space:pre-wrap;">📝 ${escapeHtml(dep.notes)}</div>` : ''}
        </div>
        <div class="card-actions">
          <button class="btn btn-secondary btn-sm" onclick="Detail.openEditModal()">Edit</button>
          ${dep.status === 'active' ? `<button class="btn btn-secondary btn-sm" onclick="Detail.pause()">Pause</button>` : ''}
          ${dep.status === 'paused' ? `<button class="btn btn-secondary btn-sm" onclick="Detail.resume()">Resume</button>` : ''}
          ${dep.status !== 'stopped' ? `<button class="btn btn-danger btn-sm" onclick="Detail.stop()">Stop</button>` : ''}
        </div>
      </div>
    `;
  },

  renderTabs() {
    const tabs = [['config', 'Config'], ['positions', 'Positions'], ['trades', 'Trades'], ['stats', 'Stats']];
    document.getElementById('detailTabs').innerHTML = tabs.map(([key, label]) =>
      `<button class="${this._tab === key ? 'active' : ''}" onclick="Detail.switchTab('${key}')">${label}</button>`
    ).join('');
  },

  async switchTab(tab) {
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
    } catch (e) {
      console.error('Detail tab render failed:', e);
      document.getElementById('detailBody').innerHTML =
        emptyHtml(`Could not load this tab's data — ${escapeHtml(e.message || String(e))}`);
    }
  },

  // ── Config ──────────────────────────────────────────────────────
  renderConfig() {
    const cfg = this._dep.config || {};
    const keys = Object.keys(cfg).sort();
    const body = document.getElementById('detailBody');
    if (!keys.length) {
      body.innerHTML = emptyHtml('No config stored for this deployment.');
      return;
    }
    body.innerHTML = `
      <div class="table-wrap"><table class="kv-table"><tbody>
        ${keys.map(k => `<tr><td>${escapeHtml(k)}</td><td>${formatConfigValue(cfg[k])}</td></tr>`).join('')}
      </tbody></table></div>
    `;
  },

  // ── Positions ───────────────────────────────────────────────────
  async renderPositions() {
    const rows = await Api.getPositions(this._id, 'open');
    const body = document.getElementById('detailBody');
    if (!rows.length) {
      body.innerHTML = emptyHtml('No open positions');
      return;
    }
    body.innerHTML = `
      <div class="table-wrap">
      <table><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Avg</th><th>Price</th><th>Unrealized</th></tr></thead>
      <tbody>${rows.map(p => `<tr>
        <td>${escapeHtml(p.symbol)}</td><td>${p.side}</td><td>${fmtNum(p.qty)}</td>
        <td>${fmtNum(p.avg_entry_price)}</td><td>${p.current_price != null ? fmtNum(p.current_price) : '—'}</td>
        <td class="${pnlClass(p.unrealized_pnl)}">${p.unrealized_pnl != null ? fmtSignedMoney(p.unrealized_pnl) : '—'}</td>
      </tr>`).join('')}</tbody></table>
      </div>
    `;
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
      <div class="table-wrap">
      <table><thead><tr><th>Time</th><th>Action</th><th>Symbol</th><th>Price</th><th>Reason</th></tr></thead>
      <tbody>${this._trades.map((l, i) => this._tradeRowHtml(l, i)).join('')}</tbody></table>
      </div>
      <div class="table-note">${data.total} total${data.total > this._trades.length ? ` (showing latest ${this._trades.length})` : ''} — click a row for the full trigger metadata</div>
    `;
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

    // Average holding period, from closed positions' own opened_at/closed_at.
    const durations = closedPositions
      .filter(p => p.opened_at && p.closed_at)
      .map(p => new Date(p.closed_at) - new Date(p.opened_at));
    const avgHoldMs = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null;

    body.innerHTML = `
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

      <section>
        <h2>Equity Curve</h2>
        ${renderEquityChart(snapshots)}
      </section>
    `;
  },

  // ── Header actions ──────────────────────────────────────────────
  openEditModal() {
    document.getElementById('editDeploymentName').value = this._dep.deployment_name;
    document.getElementById('editDeploymentNotes').value = this._dep.notes || '';
    document.getElementById('editDeploymentMsg').textContent = '';
    document.getElementById('editDeploymentModal').classList.add('open');
  },
  async pause() { await Api.pauseDeployment(this._id); this.load(this._id); },
  async resume() { await Api.resumeDeployment(this._id); this.load(this._id); },
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

// Deliberately no charting library — a single inline <polyline>, same
// "no framework, keep it simple" spirit as the rest of this UI. Not
// meant to be a full-featured chart, just enough to see the shape of a
// deployment's equity over time.
function renderEquityChart(snapshots) {
  if (snapshots.length < 2) {
    return emptyHtml(
      'Not enough snapshot data yet — equity snapshots are recorded roughly every 5 minutes per ' +
      'active deployment. Check back once this deployment has been running a while.'
    );
  }
  const values = snapshots.map(s => s.total_value);
  const min = Math.min(...values), max = Math.max(...values);
  const range = (max - min) || 1;
  const W = 600, H = 150, PAD = 6;
  const points = snapshots.map((s, i) => {
    const x = PAD + (i / (snapshots.length - 1)) * (W - 2 * PAD);
    const y = H - PAD - ((s.total_value - min) / range) * (H - 2 * PAD);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const color = values[values.length - 1] >= values[0] ? 'var(--gain)' : 'var(--loss)';
  return `
    <div class="equity-wrap">
      <svg class="equity-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <polyline points="${points}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" />
      </svg>
      <div class="table-note">
        ${snapshots.length} snapshot(s) · ${fmtDateTime(snapshots[0].snapshot_at)} → ${fmtDateTime(snapshots[snapshots.length - 1].snapshot_at)}
        · range ${fmtMoney(min)} – ${fmtMoney(max)}
      </div>
    </div>
  `;
}

// ── Edit deployment (rename + notes) modal ─────────────────────────────
function closeEditDeploymentModal() {
  document.getElementById('editDeploymentModal').classList.remove('open');
}
async function submitEditDeployment() {
  const msg = document.getElementById('editDeploymentMsg');
  const name = document.getElementById('editDeploymentName').value.trim();
  const notes = document.getElementById('editDeploymentNotes').value;
  if (!name) { msg.innerHTML = '<span style="color:var(--loss)">Deployment name cannot be blank</span>'; return; }
  msg.innerHTML = '<span class="spinner"></span> Saving…';
  const { ok, data } = await Api.updateDeployment(Detail._id, { deployment_name: name, notes });
  if (!ok) { msg.innerHTML = `<span style="color:var(--loss)">${escapeHtml(data.detail || 'Failed')}</span>`; return; }
  msg.innerHTML = '<span style="color:var(--gain)">✓ Saved</span>';
  setTimeout(() => { closeEditDeploymentModal(); Detail.load(Detail._id); }, 500);
}
