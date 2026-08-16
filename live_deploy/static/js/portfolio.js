// live_deploy — Portfolio view: whole-account rollups the Dashboard
// deliberately doesn't try to be (see README's Step 39). The Dashboard
// answers "what's happening right now" (live P&L, open positions,
// recent fills); Portfolio answers the slower-moving questions —
// how is combined equity trending over time, how much capital is
// actually deployed vs sitting idle, and am I unknowingly stacking
// exposure to the same underlying across multiple strategies at once.

const Portfolio = {
  async load() {
    document.getElementById('portfolioEquity').innerHTML = spinnerHtml();
    document.getElementById('portfolioCapital').innerHTML = spinnerHtml();
    document.getElementById('portfolioExposure').innerHTML = spinnerHtml();

    const [curve, deployments, positions] = await Promise.all([
      Api.getPortfolioEquityCurve(),
      Api.listDeployments(),
      Api.getAllPositions('open'),
    ]);

    this.renderEquity(curve);
    this.renderCapital(deployments);
    this.renderExposure(positions);
    markUpdated('portfolioUpdatedLabel');
  },

  renderEquity(curve) {
    const el = document.getElementById('portfolioEquity');
    // bucket_at -> snapshot_at: renderEquityChart() is shared with
    // Detail's own per-deployment curve, which reads snapshot_at — see
    // its own comment in api.js for why this maps rather than the
    // shared function knowing two field names for the same concept.
    const points = curve.map(s => ({ ...s, snapshot_at: s.bucket_at }));
    el.innerHTML = renderEquityChart(points,
      'Not enough combined snapshot data yet — equity snapshots are recorded roughly every 5 ' +
      'minutes per active deployment. Check back once at least one deployment has been running a while.'
    );
  },

  renderCapital(deployments) {
    const el = document.getElementById('portfolioCapital');
    if (!deployments.length) {
      el.innerHTML = emptyHtml('No deployments yet — deploy a strategy from the Catalog to get started.');
      return;
    }
    // Same scoping as the Dashboard's headline P&L card: a stopped
    // deployment's capital isn't "at work" anymore, so it's excluded
    // from utilization the same way it's excluded from live P&L.
    const live = deployments.filter(d => d.status !== 'stopped');
    const totalCapital = live.reduce((s, d) => s + (d.initial_capital || 0), 0);
    const totalCash = live.reduce((s, d) => s + (d.current_cash || 0), 0);
    const deployedValue = totalCapital - totalCash;   // capital currently tied up in open positions' cost basis
    const utilizationPct = totalCapital > 0 ? (deployedValue / totalCapital) * 100 : 0;

    const byStrategy = {};
    live.forEach(d => {
      const s = byStrategy[d.strategy_name] || (byStrategy[d.strategy_name] = { capital: 0, cash: 0 });
      s.capital += d.initial_capital || 0;
      s.cash += d.current_cash || 0;
    });
    const rows = Object.entries(byStrategy)
      .sort((a, b) => b[1].capital - a[1].capital)
      .map(([name, s]) => {
        const deployed = s.capital - s.cash;
        const pct = s.capital > 0 ? (deployed / s.capital) * 100 : 0;
        return `<div class="row"><span>${escapeHtml(name)}</span><b>${fmtMoney(deployed)} / ${fmtMoney(s.capital)} (${pct.toFixed(0)}%)</b></div>`;
      })
      .join('');

    el.innerHTML = `
      <div class="stat-card">
        <div class="stat-label">Capital deployed (active + paused)</div>
        <div class="stat-value">${fmtMoney(deployedValue)}</div>
        <div class="stat-sub">
          <div class="row"><span>Total capital</span><b>${fmtMoney(totalCapital)}</b></div>
          <div class="row"><span>Idle cash</span><b>${fmtMoney(totalCash)}</b></div>
          <div class="row"><span>Utilization</span><b>${utilizationPct.toFixed(1)}%</b></div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-label">By strategy</div>
        <div class="stat-value" style="font-size:13px;">${Object.keys(byStrategy).length} strateg${Object.keys(byStrategy).length === 1 ? 'y' : 'ies'} live</div>
        <div class="stat-sub">${rows || '<div class="row"><span>—</span></div>'}</div>
      </div>
    `;
  },

  // Groups every open position (across every deployment) by symbol —
  // the thing a per-deployment view can never show: e.g. three
  // unrelated strategies each independently long NIFTY 50 look fine in
  // isolation, but that's real stacked exposure to one underlying that
  // only becomes visible once everything is combined here.
  renderExposure(positions) {
    const el = document.getElementById('portfolioExposure');
    if (!positions.length) {
      el.innerHTML = emptyHtml('No open positions across any deployment.');
      return;
    }
    const bySymbol = {};
    positions.forEach(p => {
      const g = bySymbol[p.symbol] || (bySymbol[p.symbol] = { deployments: new Set(), netQty: 0, unrealized: 0 });
      g.deployments.add(p.deployment_name);
      g.netQty += p.side === 'long' ? p.qty : -p.qty;
      g.unrealized += p.unrealized_pnl || 0;
    });
    const rows = Object.entries(bySymbol)
      .sort((a, b) => Math.abs(b[1].unrealized) - Math.abs(a[1].unrealized))
      .map(([symbol, g]) => `<tr>
        <td>${escapeHtml(symbol)}</td>
        <td>${g.deployments.size}</td>
        <td class="${g.netQty > 0 ? 'pos' : g.netQty < 0 ? 'neg' : ''}">${g.netQty > 0 ? '+' : ''}${fmtNum(g.netQty, 0)}</td>
        <td class="${pnlClass(g.unrealized)}">${fmtSignedMoney(g.unrealized)}</td>
      </tr>`)
      .join('');
    el.innerHTML = `
      <div class="table-wrap">
      <table><thead><tr>
        <th>Symbol</th><th>Deployments</th><th>Net qty</th><th>Unrealized</th>
      </tr></thead>
      <tbody>${rows}</tbody></table>
      </div>
      <div class="table-note">Net qty combines every deployment's position in that symbol — long and short legs across different strategies offset each other here, same as they would in one real account.</div>
    `;
  },
};
