// live_deploy — Portfolio view: whole-account rollups the Dashboard
// deliberately doesn't try to be (see README's Step 39). The Dashboard
// answers "what's happening right now" (live P&L, open positions,
// recent fills); Portfolio answers the slower-moving questions —
// how is combined equity trending over time, how much capital is
// actually deployed vs sitting idle, and am I unknowingly stacking
// exposure to the same underlying across multiple strategies at once.

const Portfolio = {
  // quiet=true: event-driven background refresh -- see Dashboard.load()'s
  // own comment for why the spinner reset is skipped in that case.
  async load(quiet = false) {
    if (!quiet) {
      document.getElementById('portfolioEquity').innerHTML = spinnerHtml();
      document.getElementById('portfolioCapital').innerHTML = spinnerHtml();
      document.getElementById('portfolioExposure').innerHTML = spinnerHtml();
      document.getElementById('portfolioLeaderboard').innerHTML = spinnerHtml();
    }

    const [curve, deployments, allPositions, leaderboard] = await Promise.all([
      Api.getPortfolioEquityCurve(),
      Api.listDeployments(),
      Api.getAllPositions('open'),
      Api.getStrategyLeaderboard(),
    ]);

    // Whole-account rollups (see this file's own header comment) --
    // same include_in_reports exclusion as Dashboard.load(), applied
    // here to the raw positions list before renderExposure groups it by
    // symbol. curve/leaderboard are already filtered server-side (see
    // queries.list_portfolio_equity_curve / list_strategy_leaderboard).
    const excludedIds = new Set(deployments.filter(d => !d.include_in_reports).map(d => d.id));
    const positions = allPositions.filter(p => !excludedIds.has(p.deployment_id));

    this.renderEquity(curve);
    this.renderCapital(deployments);
    this.renderExposure(positions);
    this.renderLeaderboard(leaderboard);
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
    // from utilization the same way it's excluded from live P&L. Also
    // excludes anything toggled out of reports, same as Dashboard.
    const live = deployments.filter(d => d.status !== 'stopped' && d.include_in_reports);
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

  // ALL-TIME per-strategy P&L, across every deployment that ever ran
  // it -- active, paused, AND stopped alike (unlike Capital Utilization
  // above, which is deliberately live-only). Answers "which strategy
  // has actually made the most money since I started," a standing
  // question Reports' period-by-period breakdown doesn't try to answer.
  renderLeaderboard(rows) {
    const el = document.getElementById('portfolioLeaderboard');
    if (!rows.length) {
      el.innerHTML = emptyHtml('No closed positions recorded yet — this fills in once a strategy has actually closed a trade.');
      return;
    }
    const body = rows.map(r => {
      const decided = r.wins + r.losses;
      const winRate = decided > 0 ? ((r.wins / decided) * 100).toFixed(1) + '%' : '—';
      // Same profit-factor convention Detail's own Stats tab computes
      // client-side from raw closed-position pnls (see detail.js) --
      // gross_loss is a NEGATIVE sum here, so Math.abs() before dividing.
      let profitFactor;
      if (r.gross_loss < 0) profitFactor = (r.gross_win / Math.abs(r.gross_loss)).toFixed(2);
      else profitFactor = r.gross_win > 0 ? '∞' : '—';
      return `<tr>
        <td>${escapeHtml(r.strategy_name)}</td>
        <td class="${pnlClass(r.realized_pnl)}">${fmtSignedMoney(r.realized_pnl)}</td>
        <td>${winRate}</td>
        <td>${profitFactor}</td>
        <td>${r.positions_closed}</td>
        <td>${r.deployments_count}</td>
      </tr>`;
    }).join('');
    el.innerHTML = `
      <div class="table-wrap">
      <table><thead><tr>
        <th>Strategy</th><th>Realized P&amp;L</th><th>Win rate</th><th>Profit factor</th><th>Positions closed</th><th>Deployments</th>
      </tr></thead>
      <tbody>${body}</tbody></table>
      </div>
    `;
  },
};
