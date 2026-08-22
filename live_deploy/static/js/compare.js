// live_deploy — Strategy Comparison view: a LEADERBOARD, not a picker.
//
// Step 107 — complete redesign, per explicit feedback that the
// pick-2-6-then-click-Run version (Step 106) still wasn't giving
// "meaningful insights... to actually compare and take decisions."
// Rethought from what the page is actually FOR: a solo operator running
// several paper-trading deployments, checking in (often on a phone)
// to decide which to keep, which to kill, which deserves more capital.
// That's a "show me everything, ranked, right now" question, not a
// "let me pick a couple of things to look at" one — so:
//
// - EVERY deployment loads and ranks itself the instant the page opens.
//   No picker, no "Run" button, no empty page waiting for input. All
//   the data (snapshots + positions) is fetched ONCE up front; every
//   later re-sort/re-filter/chart-selection is a pure client-side
//   re-render with zero extra network calls.
// - Sorted by Return/Drawdown by default (Step 106's own risk-adjusted
//   metric) — "which is actually working," not just "which has the
//   biggest number," is the default view, not something you have to
//   discover by re-sorting.
// - Each row carries a FLAG — 🆕 too new to trust yet, ⚠️ a real losing
//   streak, 💤 running but nothing has closed, 🏆 the single best
//   risk-adjusted performer right now — so the table doesn't just show
//   numbers, it points at what's actually worth a second look, the way
//   a person scanning for decisions would want it done for them.
// - Each row also carries an inline SPARKLINE — the shape of its
//   equity curve at a glance, right in the row, no separate chart
//   needed to see "is this one trending up or down."
// - The big overlaid % chart is still here, but it's now a natural
//   extension of the leaderboard: check any 2-6 rows and it appears
//   below, updating instantly (same reasoning as above — the data's
//   already loaded).
//
// See README's Step 40 for the categorical --chart-1..6 palette the
// chart/swatches draw from — picked and validated (CVD-safe, both
// themes) via the dataviz skill, deliberately NOT the app's own
// semantic gain/loss/brass/info tokens, which carry fixed meaning
// everywhere else in the app.

const COMPARE_MAX = 6;   // matches the validated --chart-1..6 palette -- see index.html's :root comment
const COMPARE_COLORS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-6)'];

// Below this many days of snapshot history, EVERY derived number here
// (return, drawdown, ratio, win rate) is built on too little data to
// mean anything -- a "🏆 Top performer" badge on a deployment that
// happened to have one good day would be actively misleading, not
// insightful. So a row this new gets the 🆕 flag and is EXCLUDED from
// "who's the best right now" -- it still shows its real numbers
// (nothing is hidden), just not held up as a winner or loser yet.
const MIN_DAYS_FOR_JUDGMENT = 3;

// Module-level, not a Compare.* field -- read by the chart's own
// hover/touch handlers below (_compareChartPointerAt etc.), which are
// plain functions (not Compare methods) so they can be referenced by
// name straight from an inline onmousemove/ontouchstart attribute the
// same way api.js's own _equityChart* handlers are. Only ever one
// Compare chart on screen at a time, so a single module-level variable
// (rather than api.js's per-chartId registry, built for multiple
// simultaneous instances) is the right amount of machinery here.
let _compareChartSeries = [];   // [{deployment, points}] for whatever's currently checked

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

function _lastPoint(points) {
  return points.length ? points[points.length - 1] : null;
}

// A tiny inline equity-curve shape, no axes/labels/interaction -- the
// full interactive chart is one checkbox away for anyone who wants to
// dig in; this is purely "is this one trending up or down, and how
// bumpy" at a glance, right in the row it belongs to.
function _sparklineSvg(points) {
  const W = 72, H = 22;
  if (points.length < 2) {
    return `<span class="table-note" style="margin:0; font-size:10px;">not enough history</span>`;
  }
  const vals = points.map(p => p.pct);
  const min = Math.min(...vals, 0), max = Math.max(...vals, 0);
  const range = (max - min) || 1;
  const color = vals[vals.length - 1] >= 0 ? 'var(--gain)' : 'var(--loss)';
  const coords = points.map((p, i) => {
    const x = (i / (points.length - 1)) * W;
    const y = H - ((p.pct - min) / range) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return `<svg class="compare-sparkline" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <polyline points="${coords}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke" />
  </svg>`;
}

// One row per column. `sortValue` always returns a real, comparable
// number/string (never null/undefined) so sorting never has to special-
// case "no data yet" -- a deployment with nothing to show for a metric
// just sorts to one end, same convention Deployments' own DEPLOY_COLUMNS
// (Step 93) uses. `headerTitle` carries the one-line explanation for
// anything whose meaning isn't obvious from the label alone.
const COMPARE_COLUMNS = [
  { key: 'select', label: '', sortable: false },
  { key: 'name', label: 'Deployment', sortValue: r => r.deployment.deployment_name.toLowerCase() },
  { key: 'flag', label: 'Flag', sortable: false },
  {
    key: 'days', label: 'Days', numeric: true,
    headerTitle: 'One snapshot per trading day -- how much real history this row is actually based on.',
    sortValue: r => r.points.length,
  },
  {
    key: 'return', label: 'Return', numeric: true,
    headerTitle: 'Percent return indexed to this deployment’s own first snapshot, not annualized -- weigh this against Days.',
    sortValue: r => { const p = _lastPoint(r.points); return p ? p.pct : -Infinity; },
  },
  {
    key: 'drawdown', label: 'Max drawdown', numeric: true,
    headerTitle: 'Largest peak-to-trough decline in REALIZED equity -- capital actually, permanently lost, not a live paper dip on anything still open.',
    sortValue: r => r.drawdown ? -r.drawdown.pct : Infinity,   // smaller drawdown ranks better -- negate so ascending sort still means "best first"
  },
  {
    key: 'ratio', label: 'Return / Drawdown', numeric: true,
    headerTitle: 'Return earned per unit of capital actually lost along the way -- the single best "which of these is working" number here. A big return built on an even bigger drawdown ranks BELOW a steadier one.',
    sortValue: r => r.returnToDrawdown == null ? -Infinity : (r.returnToDrawdown === Infinity ? Number.MAX_VALUE : r.returnToDrawdown),
  },
  {
    key: 'winrate', label: 'Win rate', numeric: true,
    headerTitle: 'Per POSITION -- every leg, adjustment, and roll of one strategic bet combined into a single win or loss, same default as each deployment’s own Stats tab.',
    sortValue: r => r.stats.winRatePct == null ? -1 : r.stats.winRatePct,
  },
  {
    key: 'profitfactor', label: 'Profit factor', numeric: true,
    headerTitle: 'Gross wins ÷ gross losses, per position. Above 1 means winners outweigh losers in rupee terms, not just in count.',
    sortValue: r => r.stats.profitFactor == null ? -1 : (r.stats.profitFactor === Infinity ? Number.MAX_VALUE : r.stats.profitFactor),
  },
  { key: 'trades', label: 'Positions closed', numeric: true, sortValue: r => r.stats.closedCount },
  { key: 'equity', label: 'Current equity', numeric: true, sortValue: r => { const p = _lastPoint(r.points); return p ? p.total_value : -Infinity; } },
];

const Compare = {
  _rows: [],           // full computed dataset, one entry per deployment -- independent of filter/sort/selection
  _statusFilter: '',    // '' = all statuses
  _sortKey: 'ratio',
  _sortDir: 'desc',
  _selected: new Set(), // deployment ids currently checked for the overlay chart -- Set preserves insertion order, used for stable color assignment

  async load() {
    document.getElementById('compareLeaderboard').innerHTML = spinnerHtml();
    document.getElementById('compareChartSection').innerHTML = '';
    document.getElementById('compareExportBtn').style.display = 'none';
    this._rows = [];
    this._statusFilter = '';
    this._sortKey = 'ratio';
    this._sortDir = 'desc';
    this._selected = new Set();

    // Every deployment, not just active/paused (unlike Portfolio's
    // combined equity curve) -- "how did my old stopped strategy do
    // against this one" is exactly the kind of question this view
    // exists to answer, so scoping it to "live" deployments only would
    // rule out the most common actual use case. The status filter below
    // lets anyone narrow this down without hiding anything by default.
    const deployments = await Api.listDeployments();
    if (!deployments.length) {
      document.getElementById('compareLeaderboard').innerHTML =
        emptyHtml('No deployments yet — deploy a strategy from the Catalog to get started.');
      return;
    }

    // Fetched ONCE, up front, for every deployment -- everything after
    // this (sorting, filtering, checking rows for the chart) is a pure
    // client-side re-render with zero further network calls.
    const [snapshotLists, positionLists] = await Promise.all([
      Promise.all(deployments.map(d => Api.getSnapshots(d.id))),
      Promise.all(deployments.map(d => Api.getPositions(d.id, 'all'))),
    ]);

    this._rows = deployments.map((d, i) => this._buildRow(d, snapshotLists[i], positionLists[i]));
    this._computeFlags();
    document.getElementById('compareExportBtn').style.display = '';
    this.render();
  },

  // % return, indexed to this deployment's OWN first snapshot -- not
  // its initial_capital, since the first snapshot already reflects
  // whatever cash/position state existed by the time the snapshot loop
  // first ran, not necessarily the instant it was deployed. Every curve
  // starts at 0% by construction, so the comparison is "which one grew
  // faster from when we started watching," not skewed by one deployment
  // happening to be seeded richer.
  _buildRow(d, snaps, allPositions) {
    const base = snaps.length ? snaps[0].total_value : null;
    const points = snaps.map(s => ({
      snapshot_at: s.snapshot_at,
      total_value: s.total_value,
      pct: base ? ((s.total_value - base) / base) * 100 : 0,
    }));
    // computeMaxDrawdown wants the RAW snaps (with their own
    // realized_pnl_cumulative), not the indexed % points -- drawdown as
    // a % is already peak-relative by definition, so computing it off
    // the already-indexed series would just double that peak-relativity
    // for no reason. Same shared helper Detail's own Stats tab uses
    // (api.js), so "max drawdown" can't mean two different things in
    // two views -- both mean capital actually, permanently lost, not a
    // live paper dip.
    const drawdown = computeMaxDrawdown(snaps, d.initial_capital);

    // Per-position trade stats (win rate/profit factor/trades closed),
    // same episode grouping and default granularity Detail's Stats tab
    // uses. No per-trade toggle here on purpose: this page's whole
    // point is ranking deployments against each other, which only means
    // something if every row is measured the same way.
    const units = groupPositionsIntoUnits(allPositions, 'position');
    const stats = computeUnitStats(units);

    // Return/drawdown -- a simple, standard risk-adjusted read: how
    // much return this deployment is generating for the (permanent)
    // capital loss it's actually taken on along the way. Infinity when
    // there's real return and literally zero realized drawdown yet;
    // null when there's nothing meaningful to divide (no return data at
    // all, or flat/negative return with no drawdown either).
    const last = _lastPoint(points);
    let returnToDrawdown = null;
    if (last && drawdown && drawdown.pct > 0) {
      returnToDrawdown = last.pct / drawdown.pct;
    } else if (last && last.pct > 0 && drawdown && drawdown.pct === 0) {
      returnToDrawdown = Infinity;
    }

    return { deployment: d, points, drawdown, units, stats, returnToDrawdown, flag: null };
  },

  // One holistic flag per row -- precedence matters. Too little history
  // to trust ANY judgment comes first and suppresses everything else; a
  // real losing streak is the most urgent thing to know about a row
  // that DOES have enough history; a live deployment that hasn't closed
  // a single position yet deserves a different kind of attention (is it
  // even working); and exactly ONE row -- the best risk-adjusted return
  // among everything with enough history -- gets the positive callout.
  // Every other row stays unflagged, which is fine: not every strategy
  // needs a badge, only the ones actually worth a second look.
  _computeFlags() {
    const eligible = this._rows.filter(r => r.points.length >= MIN_DAYS_FOR_JUDGMENT && r.returnToDrawdown != null);
    const ratioValue = r => r.returnToDrawdown === Infinity ? Number.MAX_VALUE : r.returnToDrawdown;
    let best = null;
    eligible.forEach(r => { if (!best || ratioValue(r) > ratioValue(best)) best = r; });

    this._rows.forEach(r => {
      if (r.points.length < MIN_DAYS_FOR_JUDGMENT) {
        r.flag = {
          icon: '🆕', label: 'New', cls: 'tag-info',
          title: `Only ${r.points.length} day(s) of history -- too early to trust these numbers, or rank it as best/worst, yet.`,
        };
        return;
      }
      const closedByTime = r.units.filter(u => u.status === 'closed')
        .slice().sort((a, b) => new Date(a.closed_at || a.opened_at) - new Date(b.closed_at || b.opened_at));
      const lastThree = closedByTime.slice(-3);
      if (lastThree.length === 3 && lastThree.every(u => u.realized_pnl <= 0)) {
        r.flag = { icon: '⚠️', label: '3-loss streak', cls: 'tag-error', title: 'Its last 3 closed positions were all losses.' };
        return;
      }
      if (r.deployment.status === 'active' && r.stats.closedCount === 0) {
        r.flag = { icon: '💤', label: 'Quiet', cls: 'tag-warn', title: 'Still running, but nothing has closed yet despite enough time to judge -- worth checking it’s actually firing.' };
        return;
      }
      if (r === best) {
        r.flag = { icon: '🏆', label: 'Top performer', cls: 'tag-active', title: 'Best risk-adjusted return (Return ÷ Drawdown) among deployments with enough history to judge.' };
        return;
      }
      r.flag = null;
    });
  },

  setStatusFilter(value) {
    this._statusFilter = value;
    this.renderLeaderboard();
  },

  _filteredRows() {
    return this._statusFilter ? this._rows.filter(r => r.deployment.status === this._statusFilter) : this._rows;
  },

  // Click a column header to sort by it, click again to reverse; only
  // re-renders the leaderboard (the chart section is untouched), same
  // "no server round-trip, everything's already loaded" pattern
  // Deployments' own setSort (Step 93) uses. Metric columns default to
  // DESC on first click (biggest/best at the top -- that's what
  // "ranking by this" means); name defaults to ASC (alphabetical is the
  // natural reading order for a text column).
  setSort(key) {
    if (this._sortKey === key) {
      this._sortDir = this._sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      this._sortKey = key;
      this._sortDir = key === 'name' ? 'asc' : 'desc';
    }
    this.renderLeaderboard();
  },

  _sortRows(rows) {
    const col = COMPARE_COLUMNS.find(c => c.key === this._sortKey);
    if (!col || !col.sortValue) return rows;
    const dir = this._sortDir === 'desc' ? -1 : 1;
    return rows.slice().sort((a, b) => {
      const av = col.sortValue(a), bv = col.sortValue(b);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  },

  // Checking a row adds it to the overlay chart below -- no fetch, the
  // data's already sitting in this._rows from load(). Capped at
  // COMPARE_MAX to match the validated chart palette.
  toggleSelect(id, checked) {
    if (checked) {
      if (this._selected.size >= COMPARE_MAX) return;   // the checkbox is already disabled past the cap; this is just a backstop
      this._selected.add(id);
    } else {
      this._selected.delete(id);
    }
    this.render();
  },

  render() {
    this.renderLeaderboard();
    this.renderChartSection();
  },

  renderLeaderboard() {
    const el = document.getElementById('compareLeaderboard');
    const filtered = this._filteredRows();
    if (!filtered.length) {
      el.innerHTML = emptyHtml('No deployments match this status filter.');
      return;
    }
    const sorted = this._sortRows(filtered);
    const selOrder = Array.from(this._selected);   // insertion order -> stable color per selected row

    const bodyRows = sorted.map(r => {
      const selIdx = selOrder.indexOf(r.deployment.id);
      const isSelected = selIdx !== -1;
      const atCap = !isSelected && this._selected.size >= COMPARE_MAX;
      const last = _lastPoint(r.points);
      return `<tr>
        <td>
          <input type="checkbox" ${isSelected ? 'checked' : ''} ${atCap ? 'disabled' : ''}
                 onchange="Compare.toggleSelect('${r.deployment.id}', this.checked)"
                 title="${atCap ? `Up to ${COMPARE_MAX} at a time for the chart below` : 'Add to the chart comparison below'}">
          ${isSelected ? `<span class="legend-swatch" style="background:${COMPARE_COLORS[selIdx % COMPARE_COLORS.length]}; margin-left:6px;"></span>` : ''}
        </td>
        <td>
          <div style="font-weight:700; display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
            <a href="#/deployments/${r.deployment.id}">${escapeHtml(r.deployment.deployment_name)}</a>
            <span class="tag tag-${r.deployment.status}">${r.deployment.status}</span>
          </div>
          <div style="font-size:10.5px; color:var(--parchment); margin:1px 0 4px;">${escapeHtml(r.deployment.strategy_name)}</div>
          ${_sparklineSvg(r.points)}
        </td>
        <td class="compare-flag">${r.flag ? `<span class="tag ${r.flag.cls}" title="${escapeHtml(r.flag.title)}">${r.flag.icon} ${escapeHtml(r.flag.label)}</span>` : ''}</td>
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
    el.innerHTML = `
      <div class="filters" style="margin-bottom:12px; align-items:center;">
        <select onchange="Compare.setStatusFilter(this.value)">
          <option value="" ${this._statusFilter === '' ? 'selected' : ''}>All statuses</option>
          <option value="active" ${this._statusFilter === 'active' ? 'selected' : ''}>Active</option>
          <option value="paused" ${this._statusFilter === 'paused' ? 'selected' : ''}>Paused</option>
          <option value="stopped" ${this._statusFilter === 'stopped' ? 'selected' : ''}>Stopped</option>
        </select>
        <span class="table-note" style="margin:0;">${sorted.length} deployment(s) · check up to ${COMPARE_MAX} to chart them below</span>
      </div>
      <div class="table-wrap">
      <table class="deploy-table"><thead><tr>
        ${COMPARE_COLUMNS.map(c => {
          if (c.sortable === false) return `<th${c.numeric ? ' class="text-right"' : ''}>${escapeHtml(c.label)}</th>`;
          const isSorted = this._sortKey === c.key;
          const arrow = isSorted ? (this._sortDir === 'asc' ? '▲' : '▼') : '▲';
          return `<th class="sortable${c.numeric ? ' text-right' : ''}" onclick="Compare.setSort('${c.key}')"
                      ${c.headerTitle ? `title="${escapeHtml(c.headerTitle)}"` : ''}
                      aria-sort="${isSorted ? (this._sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}">
                    ${escapeHtml(c.label)}<span class="sort-arrow${isSorted ? ' active' : ''}">${arrow}</span>
                  </th>`;
        }).join('')}
      </tr></thead>
      <tbody>${bodyRows}</tbody></table>
      </div>
      <div class="table-note">
        Return/Drawdown is the number most worth ranking by — return earned per unit of capital actually,
        permanently lost, not a live paper swing on anything still open. Win rate/Profit factor/Positions
        closed combine every leg, adjustment, and roll of one strategic bet into a single win or loss.
        Flags: 🆕 too new to trust yet · ⚠️ last 3 closes were all losses · 💤 running but nothing has
        closed yet · 🏆 best risk-adjusted return right now.
      </div>
    `;
  },

  renderChartSection() {
    const el = document.getElementById('compareChartSection');
    const selOrder = Array.from(this._selected);
    const withData = selOrder
      .map(id => this._rows.find(r => r.deployment.id === id))
      .filter(r => r && r.points.length >= 2);

    if (selOrder.length < 2) {
      el.innerHTML = '';
      return;
    }
    if (!withData.length) {
      el.innerHTML = emptyHtml(
        'None of the checked deployments have at least 2 days of equity history yet — one point ' +
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
    // Index-based keeps every curve's full shape readable; the
    // leaderboard above carries the real timestamps for anyone who
    // needs them. The hover/touch crosshair (Step 88) follows the same
    // convention: the cursor's X FRACTION (0-1 across the chart) maps
    // independently into each series' own nearest point by that same
    // fraction, since there's no shared index to look up directly
    // across series of different lengths.
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

    const skipped = selOrder.length - withData.length;
    el.innerHTML = `
      <section style="margin-top:20px;">
        <h2>Equity curves</h2>
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
      </section>
    `;
  },

  // Summary export -- one row per deployment (the leaderboard exactly
  // as currently filtered/sorted), not the old per-snapshot long
  // format: this page is a scorecard now, and a scorecard is what's
  // worth taking elsewhere (a note, a spreadsheet of decisions made).
  // Anyone who wants the raw per-day series for one deployment still
  // has that on its own Detail page.
  exportCsv() {
    if (!this._rows.length) return;
    const rows = this._sortRows(this._filteredRows()).map(r => {
      const last = _lastPoint(r.points);
      return {
        deployment_name: r.deployment.deployment_name,
        strategy_name: r.deployment.strategy_name,
        status: r.deployment.status,
        flag: r.flag ? r.flag.label : '',
        days: r.points.length,
        pct_return: last ? last.pct.toFixed(4) : '',
        current_equity: last ? last.total_value : '',
        max_drawdown_abs: r.drawdown ? r.drawdown.abs.toFixed(2) : '',
        max_drawdown_pct: r.drawdown ? r.drawdown.pct.toFixed(4) : '',
        return_drawdown_ratio: r.returnToDrawdown == null ? '' : (r.returnToDrawdown === Infinity ? 'inf' : r.returnToDrawdown.toFixed(4)),
        win_rate_pct: r.stats.winRatePct == null ? '' : r.stats.winRatePct.toFixed(2),
        profit_factor: r.stats.profitFactor == null ? '' : (r.stats.profitFactor === Infinity ? 'inf' : r.stats.profitFactor.toFixed(4)),
        positions_closed: r.stats.closedCount,
      };
    });
    const csv = toCsv(rows, [
      { key: 'deployment_name', label: 'Deployment' },
      { key: 'strategy_name', label: 'Strategy' },
      { key: 'status', label: 'Status' },
      { key: 'flag', label: 'Flag' },
      { key: 'days', label: 'Days' },
      { key: 'pct_return', label: 'Pct Return' },
      { key: 'current_equity', label: 'Current Equity' },
      { key: 'max_drawdown_abs', label: 'Max Drawdown' },
      { key: 'max_drawdown_pct', label: 'Max Drawdown Pct' },
      { key: 'return_drawdown_ratio', label: 'Return/Drawdown' },
      { key: 'win_rate_pct', label: 'Win Rate Pct' },
      { key: 'profit_factor', label: 'Profit Factor' },
      { key: 'positions_closed', label: 'Positions Closed' },
    ]);
    downloadCsv('strategy_comparison.csv', csv);
  },
};
