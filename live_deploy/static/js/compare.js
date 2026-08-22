// live_deploy — Strategy Comparison view: overlay 2-6 deployments'
// equity curves on one chart, indexed to % return so deployments with
// different initial_capital compare fairly (a ₹300 move on ₹10,000
// capital and a ₹300 move on ₹1,00,000 capital are NOT the same
// performance, and a chart of raw rupee totals would make them look
// identical or wildly different for the wrong reason). See README's
// Step 40 for the categorical --chart-1..6 palette this draws from —
// picked and validated (CVD-safe, both themes) via the dataviz skill,
// deliberately NOT the app's own semantic gain/loss/brass/info tokens,
// which carry fixed meaning everywhere else in the app.
//
// Step 106 — the table below the chart went from "return % and a
// snapshot count" to an actual decision-support scorecard, per
// explicit feedback that the page wasn't giving "meaningful insights
// to compare and take decisions": risk-adjusted return (Return /
// Drawdown), win rate and profit factor (per POSITION — every leg/
// adjustment/roll of one strategic bet combined, same default Detail's
// own Stats tab uses, Step 103), and a sortable header on every column
// so whichever dimension matters right now can drive the ranking. See
// COMPARE_COLUMNS' own comment below for why each column is there.

const COMPARE_MAX = 6;   // matches the validated --chart-1..6 palette -- see index.html's :root comment
const COMPARE_COLORS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-6)'];

// Module-level, not a Compare.* field -- read by the chart's own
// hover/touch handlers below (_compareChartPointerAt etc.), which are
// plain functions (not Compare methods) so they can be referenced by
// name straight from an inline onmousemove/ontouchstart attribute the
// same way api.js's own _equityChart* handlers are. Only ever one
// Compare chart on screen at a time, so a single module-level variable
// (rather than api.js's per-chartId registry, built for multiple
// simultaneous instances) is the right amount of machinery here.
let _compareChartSeries = [];   // withData from the most recent renderChart() -- [{deployment, points}]

// Same real-CSS-pixel-via-getBoundingClientRect approach as api.js's
// _equityChartPointerAt — see that function's own comment for why. The
// one real difference: with N overlaid series of possibly DIFFERENT
// lengths, there's no single "nearest point" — each series is looked
// up independently at the SAME x FRACTION (matching how each series'
// own polyline was plotted above), and the tooltip lists all of them.
function _compareChartPointerAt(clientX, clientY, areaEl) {
  if (!_compareChartSeries.length) return;
  const rect = areaEl.getBoundingClientRect();
  const xFrac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  const leftPx = xFrac * rect.width;

  const crosshair = document.getElementById('compareChart-crosshair');
  if (crosshair) { crosshair.style.left = `${leftPx}px`; crosshair.style.display = 'block'; }

  const rows = _compareChartSeries.map((r, i) => {
    const n = r.points.length;
    const idx = Math.max(0, Math.min(n - 1, Math.round(xFrac * (n - 1))));
    const p = r.points[idx];
    const swatch = `<span style="display:inline-block; width:8px; height:8px; border-radius:2px; background:${COMPARE_COLORS[i % COMPARE_COLORS.length]}; margin-right:5px;"></span>`;
    return `${swatch}${escapeHtml(r.deployment.deployment_name)}: <b>${p.pct >= 0 ? '+' : ''}${p.pct.toFixed(2)}%</b>`;
  }).join('<br>');
  ChartTooltip.show(clientX, clientY, rows);
}

function _compareChartTouch(event, areaEl) {
  event.preventDefault();   // same reasoning as api.js's _equityChartTouch -- don't let the page scroll while reading the chart
  const touch = event.touches[0];
  if (!touch) return;
  _compareChartPointerAt(touch.clientX, touch.clientY, areaEl);
}

function _compareChartClear() {
  const crosshair = document.getElementById('compareChart-crosshair');
  if (crosshair) crosshair.style.display = 'none';
  ChartTooltip.hide();
}

// null -> "—" (nothing to show), Infinity -> "∞" (real gain, zero
// drawdown/loss yet to divide by), otherwise 2 decimal places -- same
// display convention Detail's Stats tab already uses for profit factor.
function _fmtRatio(v) {
  if (v == null) return '—';
  if (v === Infinity) return '∞';
  return v.toFixed(2);
}

function _lastPoint(r) {
  return r.points.length ? r.points[r.points.length - 1] : null;
}

// One row per column: `sortValue` always returns a real, comparable
// number/string (never null/undefined) so sorting never has to special-
// case "no data yet" -- a deployment with nothing to show for a metric
// just sorts to one end, exactly like Deployments' own DEPLOY_COLUMNS
// (Step 93) does it. `headerTitle` carries the one-line explanation for
// anything whose meaning isn't obvious from the label alone.
const COMPARE_COLUMNS = [
  { key: 'name', label: 'Deployment', sortValue: r => r.deployment.deployment_name.toLowerCase() },
  { key: 'strategy', label: 'Strategy', sortValue: r => r.deployment.strategy_name.toLowerCase() },
  { key: 'status', label: 'Status', sortValue: r => r.deployment.status },
  {
    key: 'days', label: 'Days', numeric: true,
    headerTitle: 'One snapshot per trading day -- how much real history this comparison is actually based on.',
    sortValue: r => r.points.length,
  },
  {
    key: 'return', label: 'Return', numeric: true,
    headerTitle: 'Percent return indexed to each deployment’s own first snapshot, not annualized -- weigh this against Days: a big return over 2 days proves far less than the same return over 2 months.',
    sortValue: r => { const p = _lastPoint(r); return p ? p.pct : -Infinity; },
  },
  {
    key: 'drawdown', label: 'Max drawdown', numeric: true,
    headerTitle: 'Largest peak-to-trough decline in REALIZED equity -- capital actually, permanently lost, not a live paper dip on anything still open (Step 105).',
    sortValue: r => r.drawdown ? -r.drawdown.pct : Infinity,   // smaller drawdown ranks better -- negate so ascending sort still means "best first"
  },
  {
    key: 'ratio', label: 'Return / Drawdown', numeric: true,
    headerTitle: 'Return earned per unit of capital actually lost along the way -- the single best "which of these is working" number here. A big return built on an even bigger drawdown ranks BELOW a steadier one.',
    sortValue: r => r.returnToDrawdown == null ? -Infinity : (r.returnToDrawdown === Infinity ? Number.MAX_VALUE : r.returnToDrawdown),
  },
  {
    key: 'winrate', label: 'Win rate', numeric: true,
    headerTitle: 'Per POSITION -- every leg, adjustment, and roll of one strategic bet combined into a single win or loss, same default as each deployment’s own Stats tab (Step 103).',
    sortValue: r => r.stats.winRatePct == null ? -1 : r.stats.winRatePct,
  },
  {
    key: 'profitfactor', label: 'Profit factor', numeric: true,
    headerTitle: 'Gross wins ÷ gross losses, per position. Above 1 means winners outweigh losers in rupee terms, not just in count.',
    sortValue: r => r.stats.profitFactor == null ? -1 : (r.stats.profitFactor === Infinity ? Number.MAX_VALUE : r.stats.profitFactor),
  },
  { key: 'trades', label: 'Positions closed', numeric: true, sortValue: r => r.stats.closedCount },
  { key: 'equity', label: 'Current equity', numeric: true, sortValue: r => { const p = _lastPoint(r); return p ? p.total_value : -Infinity; } },
];

const Compare = {
  _deployments: [],
  _selected: new Set(),
  _lastResult: null,   // [{deployment, points, drawdown, stats, returnToDrawdown}] -- kept around for exportCsv()/re-sorting
  _sortKey: 'ratio',    // Return/Drawdown first -- the one column most directly answers "which is actually working"
  _sortDir: 'desc',

  async load() {
    document.getElementById('comparePicker').innerHTML = spinnerHtml();
    document.getElementById('compareResult').innerHTML = '';
    document.getElementById('compareExportBtn').style.display = 'none';
    this._lastResult = null;
    this._sortKey = 'ratio';
    this._sortDir = 'desc';

    // Every deployment, not just active/paused (unlike Portfolio's
    // combined equity curve) -- "how did my old stopped strategy do
    // against this one" is exactly the kind of question this view
    // exists to answer, so scoping it to "live" deployments only would
    // rule out the most common actual use case.
    this._deployments = await Api.listDeployments();
    this._selected = new Set();
    this.renderPicker();
    this.updateRunButton();
  },

  renderPicker() {
    const el = document.getElementById('comparePicker');
    if (!this._deployments.length) {
      el.innerHTML = emptyHtml('No deployments yet — deploy a strategy from the Catalog to get started.');
      return;
    }
    el.innerHTML = this._deployments.map(d => {
      const checked = this._selected.has(d.id);
      const atCap = this._selected.size >= COMPARE_MAX && !checked;
      return `
        <label class="compare-pick-item ${checked ? 'checked' : ''} ${atCap ? 'disabled' : ''}">
          <input type="checkbox" ${checked ? 'checked' : ''} ${atCap ? 'disabled' : ''}
                 onchange="Compare.toggle('${d.id}', this.checked)">
          <div>
            <div class="compare-pick-name">${escapeHtml(d.deployment_name)}</div>
            <div class="compare-pick-meta">${escapeHtml(d.strategy_name)} · <span class="tag tag-${d.status}">${d.status}</span></div>
          </div>
        </label>
      `;
    }).join('');
  },

  toggle(id, checked) {
    if (checked) {
      if (this._selected.size >= COMPARE_MAX) return;   // picker already disables past the cap; this is just a backstop
      this._selected.add(id);
    } else {
      this._selected.delete(id);
    }
    this.renderPicker();
    this.updateRunButton();
  },

  updateRunButton() {
    document.getElementById('compareRunBtn').disabled = this._selected.size < 2;
  },

  async run() {
    const btn = document.getElementById('compareRunBtn');
    btn.disabled = true;
    document.getElementById('compareResult').innerHTML = spinnerHtml();

    const selectedDeployments = this._deployments.filter(d => this._selected.has(d.id));
    // Positions fetched alongside snapshots (status=all, so a still-
    // open position's own episode is tagged correctly -- see
    // queries.list_positions_with_episode's own reasoning) purely for
    // the win rate/profit factor/trades-closed columns; nothing else
    // here needs the individual lots.
    const [snapshotLists, positionLists] = await Promise.all([
      Promise.all(selectedDeployments.map(d => Api.getSnapshots(d.id))),
      Promise.all(selectedDeployments.map(d => Api.getPositions(d.id, 'all'))),
    ]);

    // % return, indexed to each deployment's OWN first snapshot -- not
    // its initial_capital, since the first snapshot already reflects
    // whatever cash/position state existed by the time the snapshot
    // loop first ran, not necessarily the instant it was deployed.
    // Every curve starts at 0% by construction, so the comparison is
    // "which one grew faster from when we started watching," not
    // skewed by one deployment happening to be seeded richer.
    const result = selectedDeployments.map((d, i) => {
      const snaps = snapshotLists[i];
      const base = snaps.length ? snaps[0].total_value : null;
      const points = snaps.map(s => ({
        snapshot_at: s.snapshot_at,
        total_value: s.total_value,
        pct: base ? ((s.total_value - base) / base) * 100 : 0,
      }));
      // computeMaxDrawdown wants the RAW snaps (with their own
      // realized_pnl_cumulative), not the indexed % points -- drawdown
      // as a % is already peak-relative by definition, so computing it
      // off the already-indexed series would just double that
      // peak-relativity for no reason. Same shared helper Detail's own
      // Stats tab uses (api.js), so "max drawdown" can't mean two
      // different things in two views -- both mean capital actually,
      // permanently lost (Step 105), not a live paper dip.
      const drawdown = computeMaxDrawdown(snaps, d.initial_capital);

      // Step 106 -- per-position trade stats (win rate/profit factor/
      // trades closed), same episode grouping and default granularity
      // Detail's Stats tab uses. No per-trade toggle here on purpose:
      // Compare's whole point is ranking several deployments against
      // each other, and that only means something if every row is
      // measured the same way.
      const stats = computeUnitStats(groupPositionsIntoUnits(positionLists[i], 'position'));

      // Return/drawdown -- a simple, standard risk-adjusted read: how
      // much return this deployment is generating for the (permanent)
      // capital loss it's actually taken on along the way. Infinity
      // when there's real return and literally zero realized drawdown
      // yet; null when there's nothing meaningful to divide (no return
      // data at all, or flat/negative return with no drawdown either).
      const last = _lastPoint({ points });
      let returnToDrawdown = null;
      if (last && drawdown && drawdown.pct > 0) {
        returnToDrawdown = last.pct / drawdown.pct;
      } else if (last && last.pct > 0 && drawdown && drawdown.pct === 0) {
        returnToDrawdown = Infinity;
      }

      return { deployment: d, points, drawdown, stats, returnToDrawdown };
    });

    this._lastResult = result;
    document.getElementById('compareResult').innerHTML = `
      <div id="compareChartWrap"></div>
      <div id="compareTableWrap"></div>
    `;
    this.renderChart(result);
    this.renderTable(result);
    document.getElementById('compareExportBtn').style.display = result.some(r => r.points.length) ? '' : 'none';
    btn.disabled = this._selected.size < 2;
  },

  renderChart(result) {
    const el = document.getElementById('compareChartWrap');
    const withData = result.filter(r => r.points.length >= 2);
    if (!withData.length) {
      el.innerHTML = emptyHtml(
        'None of the selected deployments have at least 2 days of equity history yet — one point ' +
        'is recorded per trading day. Check back once they\'ve been running a couple of days.'
      );
      return;
    }

    const allPct = withData.flatMap(r => r.points.map(p => p.pct));
    const min = Math.min(...allPct, 0), max = Math.max(...allPct, 0);   // always include 0% (every curve's own start) so the baseline is never off-chart
    const mid = (min + max) / 2;
    const range = (max - min) || 1;
    const W = 600, H = 220, PAD = 6;
    // Chart's own X axis is index-based (not wall-clock time): each
    // deployment can have a different number of snapshots covering a
    // different span, so a shared time axis would either squash a
    // short-lived deployment's curve into a sliver or need
    // interpolation this simple inline-SVG renderer isn't built for.
    // Index-based keeps every curve's full shape readable; the table
    // below carries the real timestamps for anyone who needs them. The
    // hover/touch crosshair (Step 88) follows the same convention: the
    // cursor's X FRACTION (0-1 across the chart) maps independently
    // into each series' own nearest point by that same fraction, since
    // there's no shared index to look up directly across series of
    // different lengths.
    const polylines = withData.map((r, i) => {
      const n = r.points.length;
      const points = r.points.map((p, j) => {
        const x = PAD + (n === 1 ? 0 : (j / (n - 1)) * (W - 2 * PAD));
        const y = H - PAD - ((p.pct - min) / range) * (H - 2 * PAD);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      return `<polyline points="${points}" fill="none" stroke="${COMPARE_COLORS[i % COMPARE_COLORS.length]}" stroke-width="2" vector-effect="non-scaling-stroke" />`;
    }).join('');

    const legend = withData.map((r, i) => {
      const last = r.points[r.points.length - 1].pct;
      return `
        <div class="legend-item">
          <span class="legend-swatch" style="background:${COMPARE_COLORS[i % COMPARE_COLORS.length]}"></span>
          <span class="legend-name">${escapeHtml(r.deployment.deployment_name)}</span>
          <span class="legend-value ${pnlClass(last)}">${last >= 0 ? '+' : ''}${last.toFixed(2)}%</span>
        </div>
      `;
    }).join('');

    _compareChartSeries = withData;   // read by _compareChartPointerAt below

    const skipped = result.length - withData.length;
    el.innerHTML = `
      <div class="equity-wrap">
        <div class="equity-chart-row">
          <div class="equity-axis-y">
            <span>${max >= 0 ? '+' : ''}${max.toFixed(1)}%</span>
            <span>${mid >= 0 ? '+' : ''}${mid.toFixed(1)}%</span>
            <span>${min >= 0 ? '+' : ''}${min.toFixed(1)}%</span>
          </div>
          <div class="equity-chart-area"
               onmousemove="_compareChartPointerAt(event.clientX, event.clientY, this)"
               onmouseleave="_compareChartClear()"
               ontouchstart="_compareChartTouch(event, this)"
               ontouchmove="_compareChartTouch(event, this)"
               ontouchend="_compareChartClear()">
            <svg class="equity-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${polylines}</svg>
            <div class="equity-crosshair" id="compareChart-crosshair"></div>
          </div>
        </div>
        <div class="chart-legend">${legend}</div>
        <div class="table-note">
          % return indexed to each deployment's own first snapshot (0% = start).
          ${skipped ? ` ${skipped} deployment(s) skipped — not enough snapshot history yet.` : ''}
        </div>
      </div>
    `;
  },

  // Step 106 -- click a column header to sort by it, click again to
  // reverse; only re-renders the table (renderChart untouched), same
  // "no server round-trip, everything's already loaded" pattern
  // Deployments' own setSort (Step 93) uses. Metric columns default to
  // DESC on first click (biggest/best at the top -- that's what
  // "ranking by this" means); name/strategy/status default to ASC
  // (alphabetical is the natural reading order for a text column).
  setSort(key) {
    if (this._sortKey === key) {
      this._sortDir = this._sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      this._sortKey = key;
      this._sortDir = ['name', 'strategy', 'status'].includes(key) ? 'asc' : 'desc';
    }
    this.renderTable(this._lastResult);
  },

  _sortRows(result) {
    const col = COMPARE_COLUMNS.find(c => c.key === this._sortKey);
    if (!col) return result;
    const dir = this._sortDir === 'desc' ? -1 : 1;
    return result.slice().sort((a, b) => {
      const av = col.sortValue(a), bv = col.sortValue(b);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  },

  renderTable(result) {
    const wrap = document.getElementById('compareTableWrap');
    const sorted = this._sortRows(result);
    const rows = sorted.map(r => {
      // Color swatch tracks the deployment's position in the ORIGINAL
      // (unsorted) result/legend, not its row position after sorting --
      // otherwise the same deployment's color would shift every time
      // the table gets re-sorted, breaking the link to the chart legend.
      const colorIdx = result.indexOf(r);
      const swatch = `<span class="legend-swatch" style="background:${COMPARE_COLORS[colorIdx % COMPARE_COLORS.length]}"></span>`;
      const last = _lastPoint(r);
      return `<tr>
        <td>${swatch} ${escapeHtml(r.deployment.deployment_name)}</td>
        <td>${escapeHtml(r.deployment.strategy_name)}</td>
        <td><span class="tag tag-${r.deployment.status}">${r.deployment.status}</span></td>
        <td class="text-right">${r.points.length}</td>
        <td class="text-right ${last ? pnlClass(last.pct) : ''}">${last ? `${last.pct >= 0 ? '+' : ''}${last.pct.toFixed(2)}%` : '—'}</td>
        <td class="text-right ${r.drawdown ? 'neg' : ''}">${r.drawdown ? `${fmtMoney(r.drawdown.abs)} (${r.drawdown.pct.toFixed(2)}%)` : '—'}</td>
        <td class="text-right">${_fmtRatio(r.returnToDrawdown)}</td>
        <td class="text-right">${r.stats.winRatePct == null ? '—' : fmtPct(r.stats.winRatePct)}</td>
        <td class="text-right">${_fmtRatio(r.stats.profitFactor)}</td>
        <td class="text-right">${r.stats.closedCount}</td>
        <td class="text-right">${last ? fmtMoney(last.total_value) : '—'}</td>
      </tr>`;
    }).join('');

    // .deploy-table reused purely for its sortable-header CSS
    // (cursor/hover/sticky-header) -- see index.html's own rule, not
    // specific to the Deployments view despite the name.
    wrap.innerHTML = `
      <div class="table-wrap" style="margin-top:14px;">
      <table class="deploy-table"><thead><tr>
        ${COMPARE_COLUMNS.map(c => {
          const isSorted = this._sortKey === c.key;
          const arrow = isSorted ? (this._sortDir === 'asc' ? '▲' : '▼') : '▲';
          return `<th class="sortable${c.numeric ? ' text-right' : ''}" onclick="Compare.setSort('${c.key}')"
                      ${c.headerTitle ? `title="${escapeHtml(c.headerTitle)}"` : ''}
                      aria-sort="${isSorted ? (this._sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}">
                    ${escapeHtml(c.label)}<span class="sort-arrow${isSorted ? ' active' : ''}">${arrow}</span>
                  </th>`;
        }).join('')}
      </tr></thead>
      <tbody>${rows}</tbody></table>
      </div>
      <div class="table-note">
        Max drawdown / Return-Drawdown / Win rate / Profit factor all mean capital actually won or
        permanently lost — never a live paper swing on anything still open. Win rate and profit factor
        combine every leg, adjustment, and roll of one strategic bet into a single win or loss, same as
        each deployment's own Stats tab default. Click any column to rank by it. Numbers built on only a
        few days of history (see the Days column) are easy to over-read — a short lucky streak isn't a
        proven edge yet.
      </div>
    `;
  },

  // Long/tidy format -- one row per (deployment, snapshot) pair, rather
  // than a wide table with one column per deployment: deployments'
  // snapshot timestamps don't line up exactly (see the chart's own
  // index-based X axis comment above), so a wide layout would need
  // interpolation or ragged blank cells. Long format has no such
  // problem and is the more generically useful shape for pivoting
  // elsewhere (Excel, pandas, ...) anyway. Step 106: the summary
  // metrics (drawdown/ratio/win rate/profit factor/trades) are the
  // same for every row of a given deployment, so they're repeated
  // per-row here rather than needing a second sheet/section — trivial
  // to filter down to one row per deployment in a spreadsheet if only
  // the summary is wanted.
  exportCsv() {
    if (!this._lastResult) return;
    const rows = this._lastResult.flatMap(r =>
      r.points.map(p => ({
        deployment_name: r.deployment.deployment_name,
        strategy_name: r.deployment.strategy_name,
        snapshot_at: p.snapshot_at,
        total_value: p.total_value,
        pct_return: p.pct.toFixed(4),
        max_drawdown_pct: r.drawdown ? r.drawdown.pct.toFixed(4) : '',
        return_drawdown_ratio: r.returnToDrawdown == null ? '' : (r.returnToDrawdown === Infinity ? 'inf' : r.returnToDrawdown.toFixed(4)),
        win_rate_pct: r.stats.winRatePct == null ? '' : r.stats.winRatePct.toFixed(2),
        profit_factor: r.stats.profitFactor == null ? '' : (r.stats.profitFactor === Infinity ? 'inf' : r.stats.profitFactor.toFixed(4)),
        positions_closed: r.stats.closedCount,
      }))
    );
    const csv = toCsv(rows, [
      { key: 'deployment_name', label: 'Deployment' },
      { key: 'strategy_name', label: 'Strategy' },
      { key: 'snapshot_at', label: 'Time' },
      { key: 'total_value', label: 'Total Value' },
      { key: 'pct_return', label: 'Pct Return' },
      { key: 'max_drawdown_pct', label: 'Max Drawdown Pct' },
      { key: 'return_drawdown_ratio', label: 'Return/Drawdown' },
      { key: 'win_rate_pct', label: 'Win Rate Pct' },
      { key: 'profit_factor', label: 'Profit Factor' },
      { key: 'positions_closed', label: 'Positions Closed' },
    ]);
    downloadCsv('strategy_comparison.csv', csv);
  },
};
