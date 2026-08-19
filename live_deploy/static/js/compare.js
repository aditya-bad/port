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

const Compare = {
  _deployments: [],
  _selected: new Set(),
  _lastResult: null,   // [{deployment, points: [{snapshot_at, total_value, pct}]}] -- kept around for exportCsv()

  async load() {
    document.getElementById('comparePicker').innerHTML = spinnerHtml();
    document.getElementById('compareResult').innerHTML = '';
    document.getElementById('compareExportBtn').style.display = 'none';
    this._lastResult = null;

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
    const snapshotLists = await Promise.all(selectedDeployments.map(d => Api.getSnapshots(d.id)));

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
      // computeMaxDrawdown wants the RAW snaps (rupee total_value), not
      // the indexed % points -- drawdown as a % is already peak-relative
      // by definition, so computing it off the already-indexed series
      // would just double that peak-relativity for no reason. Same
      // shared helper Detail's own Stats tab uses (api.js), so "max
      // drawdown" can't mean two different things in two views.
      return { deployment: d, points, drawdown: computeMaxDrawdown(snaps) };
    });

    this._lastResult = result;
    this.renderChart(result);
    this.renderTable(result);
    document.getElementById('compareExportBtn').style.display = result.some(r => r.points.length) ? '' : 'none';
    btn.disabled = this._selected.size < 2;
  },

  renderChart(result) {
    const el = document.getElementById('compareResult');
    const withData = result.filter(r => r.points.length >= 2);
    if (!withData.length) {
      el.innerHTML = emptyHtml(
        'None of the selected deployments have at least 2 equity snapshots yet — snapshots are ' +
        'recorded roughly every 5 minutes per active deployment. Check back once they\'ve been running a while.'
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

  renderTable(result) {
    const wrap = document.getElementById('compareResult');
    const rows = result.map((r, i) => {
      const n = r.points.length;
      const last = n ? r.points[n - 1] : null;
      const swatch = `<span class="legend-swatch" style="background:${COMPARE_COLORS[i % COMPARE_COLORS.length]}"></span>`;
      return `<tr>
        <td>${swatch} ${escapeHtml(r.deployment.deployment_name)}</td>
        <td>${escapeHtml(r.deployment.strategy_name)}</td>
        <td><span class="tag tag-${r.deployment.status}">${r.deployment.status}</span></td>
        <td>${n}</td>
        <td class="${last ? pnlClass(last.pct) : ''}">${last ? `${last.pct >= 0 ? '+' : ''}${last.pct.toFixed(2)}%` : '—'}</td>
        <td>${last ? fmtMoney(last.total_value) : '—'}</td>
        <td class="${r.drawdown ? 'neg' : ''}">${r.drawdown ? `${fmtMoney(r.drawdown.abs)} (${r.drawdown.pct.toFixed(2)}%)` : '—'}</td>
      </tr>`;
    }).join('');
    wrap.innerHTML += `
      <div class="table-wrap" style="margin-top:14px;">
      <table><thead><tr>
        <th>Deployment</th><th>Strategy</th><th>Status</th><th>Snapshots</th><th>Return</th><th>Current equity</th><th>Max drawdown</th>
      </tr></thead>
      <tbody>${rows}</tbody></table>
      </div>
      <div class="table-note">Max drawdown — largest peak-to-trough decline in each deployment's own equity, same definition as its Stats tab.</div>
    `;
  },

  // Long/tidy format -- one row per (deployment, snapshot) pair, rather
  // than a wide table with one column per deployment: deployments'
  // snapshot timestamps don't line up exactly (see the chart's own
  // index-based X axis comment above), so a wide layout would need
  // interpolation or ragged blank cells. Long format has no such
  // problem and is the more generically useful shape for pivoting
  // elsewhere (Excel, pandas, ...) anyway.
  exportCsv() {
    if (!this._lastResult) return;
    const rows = this._lastResult.flatMap(r =>
      r.points.map(p => ({
        deployment_name: r.deployment.deployment_name,
        strategy_name: r.deployment.strategy_name,
        snapshot_at: p.snapshot_at,
        total_value: p.total_value,
        pct_return: p.pct.toFixed(4),
      }))
    );
    const csv = toCsv(rows, [
      { key: 'deployment_name', label: 'Deployment' },
      { key: 'strategy_name', label: 'Strategy' },
      { key: 'snapshot_at', label: 'Time' },
      { key: 'total_value', label: 'Total Value' },
      { key: 'pct_return', label: 'Pct Return' },
    ]);
    downloadCsv('strategy_comparison.csv', csv);
  },
};
