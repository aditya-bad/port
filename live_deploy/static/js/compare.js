// live_deploy — Strategy Comparison view.
//
// Step 109 — second round of redesign, per explicit feedback on Step
// 107's leaderboard: "the top performer and all is good but I am
// still unhappy" — narrowed down to two concrete things:
//
// 1. "Still can't decide" — a sortable table of numbers, even a good
//    one with flags, still makes the READER do the work of turning
//    "Return/Drawdown = 0.43" into "don't trust this one." So this
//    version leads with a "What needs attention" panel: plain-English
//    callouts (see renderAttentionPanel), one per flagged deployment,
//    that say the thing out loud instead of leaving it to be inferred
//    from a number. The leaderboard below also gives the Return/
//    Drawdown ratio a plain-language label (_ratioLabel) for the same
//    reason — "6.75 (excellent)" needs no quant intuition to read.
// 2. "Layout / readability" — an 8+ numeric-column table needs
//    constant horizontal scrolling on a phone, exactly the device this
//    page gets checked from most. Replaced with a CARD per deployment
//    (see .compare-card-grid, index.html) — every number for one
//    deployment lives in one glanceable block, no scrolling to
//    correlate a row with a column header. Sorting moves from
//    clickable table headers to a `<select>` + a direction toggle,
//    since cards have no header row to click.
//
// Structurally unchanged from Step 107: still an always-on leaderboard
// (every deployment loads and ranks itself the instant the page opens,
// no picker/Run button), still fetches snapshots+positions ONCE up
// front so every later re-sort/re-filter/chart-selection is a pure
// client-side re-render, still the same flag precedence (New suppresses
// everything else; then loss-streak; then Quiet; then exactly one Top
// performer). See README's Step 40 for the categorical --chart-1..6
// palette the overlay chart/swatches draw from — picked and validated
// (CVD-safe, both themes) via the dataviz skill, deliberately NOT the
// app's own semantic gain/loss/brass/info tokens (those ARE used here,
// for the attention panel's accent borders — that IS the semantic case
// those tokens carry fixed meaning for everywhere else in the app).
//
// Step 110 — the equity-curve overlay alone doesn't answer "what am I
// actually comparing," per direct feedback and a concrete proposed fix:
// checking rows should produce a TRANSPOSED head-to-head table (one
// COLUMN per deployment, one ROW per metric — the opposite orientation
// of the leaderboard below), with each row's own winner (or tie) called
// out, plus one overall verdict tallying wins across every SCORED
// metric. See HEAD_TO_HEAD_METRICS/_rowWinners/_overallWinners and
// renderComparisonSection. The comparison section also now sits ABOVE
// the leaderboard cards in the DOM (see index.html) — checking rows for
// comparison visually pushes the individual cards down and puts the
// actual comparison output first, exactly the "cards go to the bottom"
// behavior asked for.

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
let _compareChartSeries = [];   // [{deployment, points, color}] for whatever's currently checked and has enough history to draw

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

  const rows = _compareChartSeries.map(r => {
    const n = r.points.length;
    const idx = Math.max(0, Math.min(n - 1, Math.round(xFrac * (n - 1))));
    const p = r.points[idx];
    const swatch = `<span style="display:inline-block; width:8px; height:8px; border-radius:2px; background:${r.color}; margin-right:5px;"></span>`;
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

// A plain-language read of the Return/Drawdown ratio, so "6.75" doesn't
// require quant intuition to interpret -- directly answers "is this
// number actually good," the thing raw metrics alone were leaving to
// the reader (Step 109's whole point). Thresholds are deliberately
// simple/round, not a precision-tuned model: this is a first-glance
// label, not a score.
function _ratioLabel(v) {
  if (v == null) return null;
  if (v === Infinity) return 'no losses yet';
  if (v >= 3) return 'excellent';
  if (v >= 1) return 'good';
  if (v >= 0) return 'weak';
  return 'losing money';
}

function _lastPoint(points) {
  return points.length ? points[points.length - 1] : null;
}

// A tiny inline equity-curve shape, no axes/labels/interaction -- the
// full interactive chart is one checkbox away for anyone who wants to
// dig in; this is purely "is this one trending up or down, and how
// bumpy" at a glance. A normalized viewBox + CSS width:100% (see
// .compare-sparkline) lets this stretch to fill whatever width the
// card gives it, rather than a fixed pixel size.
function _sparklineSvg(points) {
  const W = 100, H = 28;
  if (points.length < 2) {
    return `<div class="table-note" style="margin:6px 0 0; font-size:10px;">not enough history yet</div>`;
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
  return `<svg class="compare-sparkline" height="${H}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <polyline points="${coords}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke" />
  </svg>`;
}

// ── Head-to-head table (Step 110) ──────────────────────────────────────
// One row per metric, transposed against the leaderboard's own
// orientation on purpose — reading "which deployment wins THIS metric"
// across a row is the actual comparison question; a table shaped like
// the leaderboard (one row per deployment) makes you do that scan
// yourself, column by column, which is the exact thing this section
// exists to do instead.
//
// `better`: 'higher' or 'lower' for a metric that has a genuine winner;
// `null` for a metric shown purely for CONTEXT — Positions closed/Days
// live/Current equity/Initial Capital/Realized P&L/Trades per Day/Avg
// Holding Period/Largest Win/Largest Loss are all context: more trades
// or more days isn't inherently "better," a bigger largest-win/-loss
// often just reflects bigger position sizing rather than a better edge,
// and neither Initial Capital, Realized P&L (an absolute rupee number),
// nor Current equity are fairly comparable across deployments with
// different capital sizes the way the indexed % numbers already are.
// Context rows never contribute to the overall winner tally.
//
// Two returns, deliberately both shown (Step 111, "capital vs
// returns"): "Return" is indexed to this deployment's OWN first
// snapshot (unaffected by exactly when snapshotting started); "Return
// on Capital" is realized profit as a % of the ORIGINAL initial_capital
// committed to it. These can genuinely differ -- most visibly for a
// "positional" deployment currently holding an open position: its
// indexed Return reflects that position's live mark-to-market (Step 99
// intentionally includes this for positional mode), while Return on
// Capital only counts money actually, permanently realized. Seeing both
// side by side answers "how is this doing right now" AND "how much of
// what I put in has this actually paid back so far" as two distinct
// questions, not one blended number.
const HEAD_TO_HEAD_METRICS = [
  {
    key: 'capital', label: 'Initial Capital', better: null,
    value: r => r.deployment.initial_capital,
    format: r => fmtMoney(r.deployment.initial_capital),
  },
  {
    key: 'return', label: 'Return (since tracked)', better: 'higher',
    value: r => { const p = _lastPoint(r.points); return p ? p.pct : null; },
    format: r => { const p = _lastPoint(r.points); return p ? `${p.pct >= 0 ? '+' : ''}${p.pct.toFixed(2)}%` : '—'; },
  },
  {
    key: 'roc', label: 'Return on Capital', better: 'higher',
    value: r => { const p = _lastPoint(r.points); return (p && r.deployment.initial_capital) ? (p.realized_pnl_cumulative / r.deployment.initial_capital) * 100 : null; },
    format: r => {
      const p = _lastPoint(r.points);
      if (!p || !r.deployment.initial_capital) return '—';
      const v = (p.realized_pnl_cumulative / r.deployment.initial_capital) * 100;
      return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
    },
  },
  {
    key: 'realizedpnl', label: 'Realized P&L', better: null,
    value: r => { const p = _lastPoint(r.points); return p ? p.realized_pnl_cumulative : null; },
    format: r => { const p = _lastPoint(r.points); return p != null ? fmtSignedMoney(p.realized_pnl_cumulative) : '—'; },
  },
  {
    key: 'drawdown', label: 'Max drawdown', better: 'lower',
    value: r => r.drawdown ? r.drawdown.pct : null,
    format: r => r.drawdown ? `${fmtMoney(r.drawdown.abs)} (${r.drawdown.pct.toFixed(2)}%)` : '—',
  },
  {
    key: 'ratio', label: 'Return / Drawdown', better: 'higher',
    value: r => r.returnToDrawdown == null ? null : (r.returnToDrawdown === Infinity ? Number.MAX_VALUE : r.returnToDrawdown),
    format: r => { const lbl = _ratioLabel(r.returnToDrawdown); return `${_fmtRatio(r.returnToDrawdown)}${lbl ? ` (${lbl})` : ''}`; },
  },
  {
    key: 'winrate', label: 'Win rate', better: 'higher',
    value: r => r.stats.winRatePct,
    format: r => r.stats.winRatePct == null ? '—' : fmtPct(r.stats.winRatePct),
  },
  {
    key: 'profitfactor', label: 'Profit factor', better: 'higher',
    value: r => r.stats.profitFactor == null ? null : (r.stats.profitFactor === Infinity ? Number.MAX_VALUE : r.stats.profitFactor),
    format: r => _fmtRatio(r.stats.profitFactor),
  },
  {
    key: 'avgpnl', label: 'Avg P&L per Position', better: 'higher',
    value: r => r.stats.closedCount ? r.stats.totalRealizedPnl / r.stats.closedCount : null,
    format: r => r.stats.closedCount ? fmtSignedMoney(r.stats.totalRealizedPnl / r.stats.closedCount) : '—',
  },
  { key: 'trades', label: 'Positions closed', better: null, value: r => r.stats.closedCount, format: r => String(r.stats.closedCount) },
  {
    key: 'frequency', label: 'Positions / Day', better: null,
    value: r => r.points.length ? r.stats.closedCount / r.points.length : null,
    format: r => r.points.length ? (r.stats.closedCount / r.points.length).toFixed(2) : '—',
  },
  { key: 'days', label: 'Days live', better: null, value: r => r.points.length, format: r => String(r.points.length) },
  {
    key: 'holdperiod', label: 'Avg Holding Period', better: null,
    value: r => _avgHoldMs(r),
    format: r => { const ms = _avgHoldMs(r); return ms != null ? fmtDuration(ms) : '—'; },
  },
  {
    key: 'largestwin', label: 'Largest Win', better: null,
    value: r => _extremePnl(r, 'max'),
    format: r => { const v = _extremePnl(r, 'max'); return v != null ? fmtSignedMoney(v) : '—'; },
  },
  {
    key: 'largestloss', label: 'Largest Loss', better: null,
    value: r => _extremePnl(r, 'min'),
    format: r => { const v = _extremePnl(r, 'min'); return v != null ? fmtSignedMoney(v) : '—'; },
  },
  {
    key: 'equity', label: 'Current equity', better: null,
    value: r => { const p = _lastPoint(r.points); return p ? p.total_value : null; },
    format: r => { const p = _lastPoint(r.points); return p ? fmtMoney(p.total_value) : '—'; },
  },
];

// Avg wall-clock holding period across a row's own CLOSED episodes
// (Step 111) -- same "opened_at to closed_at, mean across closed units"
// definition Detail's Stats tab already uses for its own Avg Holding
// Period stat, just sourced from `r.units` (already computed per row
// for win rate/profit factor) instead of a second pass over raw
// positions.
function _avgHoldMs(r) {
  const durations = r.units
    .filter(u => u.status === 'closed' && u.opened_at && u.closed_at)
    .map(u => new Date(u.closed_at) - new Date(u.opened_at));
  return durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null;
}

// Largest single CLOSED episode's own realized_pnl in either direction
// -- a risk-profile number, not a scored one (a bigger largest-loss
// often just reflects bigger position sizing, not a worse edge).
function _extremePnl(r, which) {
  const pnls = r.units.filter(u => u.status === 'closed').map(u => u.realized_pnl);
  if (!pnls.length) return null;
  return which === 'max' ? Math.max(...pnls) : Math.min(...pnls);
}

// Which of `rows` (already filtered to the deployments being compared)
// win a given metric's row. Rounds to 2 decimals before comparing so
// two numbers that are equal for all practical purposes (float noise
// from division) don't spuriously miss a tie. A value of `null` for a
// given row is simply excluded from contention -- "no data" never
// "wins" by default, but also never blocks another row's real value
// from winning.
function _rowWinners(metric, rows) {
  const vals = rows.map(r => metric.value(r));
  const real = vals.filter(v => v != null);
  if (!metric.better || !real.length) return { winners: new Set(), tie: false, noData: !real.length };
  const round = v => Math.round(v * 100) / 100;
  const best = metric.better === 'higher' ? Math.max(...real.map(round)) : Math.min(...real.map(round));
  const winners = new Set();
  vals.forEach((v, i) => { if (v != null && round(v) === best) winners.add(i); });
  return { winners, tie: winners.size > 1, noData: false };
}

// Tally each row-index's wins across every SCORED metric (context
// metrics never contribute) and report whoever has the most -- possibly
// more than one, an explicit overall tie, never silently broken.
function _overallWinners(rows) {
  const tally = rows.map(() => 0);
  HEAD_TO_HEAD_METRICS.filter(m => m.better).forEach(m => {
    _rowWinners(m, rows).winners.forEach(i => tally[i]++);
  });
  const maxScore = Math.max(...tally);
  const winners = tally.map((_, i) => i).filter(i => tally[i] === maxScore);
  return { tally, winners, maxScore, tie: winners.length > 1 };
}

// ── Month/Year/All-Time leaderboards (Step 113) ────────────────────────
// "Who's winning this month" and "who's winning all-time" are genuinely
// different, both useful, questions -- a strategy having a rough
// quarter after a strong debut, or one that's just caught fire this
// month, is invisible in an all-time-only ranking. All three scopes
// reuse the exact same per-deployment `points`/`units` this page
// already fetched ONCE on load (see Compare.load) -- no new DB table
// and no new network call, just filtering/re-basing data already
// sitting in memory, consistent with this page's whole "recompute,
// don't cache" approach (and the user's own explicit sign-off: "if
// there's no separate DB needed for this, then it's fine").
//
// Calendar buckets are IST, not the browser's local zone or UTC --
// toLocaleDateString('en-CA', {timeZone:'Asia/Kolkata'}) reliably
// produces a YYYY-MM-DD string in a given IANA zone without hand-rolled
// UTC-offset math, the same trick list_snapshots leans on server-side
// (Step 96) for "what IST calendar day was this."
function _istDateKey(iso) {
  return new Date(iso).toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
}
function _istMonthKey(iso) { return _istDateKey(iso).slice(0, 7); }   // "2026-08"
function _istYearKey(iso) { return _istDateKey(iso).slice(0, 4); }    // "2026"
function _istMonthLabel(monthKey) {
  const [y, m] = monthKey.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
}

// The contiguous run of one deployment's `points` whose IST month/year
// key matches `periodKey`, PLUS the one snapshot immediately before the
// period ("baseline") when one exists. The baseline matters twice over:
// it re-bases % return to "how did THIS period go" instead of "how has
// this deployment gone since it started tracking," and it lets a
// drawdown occurring on the very FIRST day of the period be seen at
// all -- without a prior point to fall from, computeMaxDrawdown would
// treat that first in-period point as the peak itself and miss the drop.
function _periodSlice(row, keyFn, periodKey) {
  const idxs = [];
  row.points.forEach((p, i) => { if (keyFn(p.snapshot_at) === periodKey) idxs.push(i); });
  if (!idxs.length) return null;
  const first = idxs[0], last = idxs[idxs.length - 1];
  const slice = row.points.slice(first, last + 1);
  const baseline = first > 0 ? row.points[first - 1] : slice[0];
  return { slice, baseline };
}

// One deployment's metrics recomputed for a single scope+period.
// scope 'all' just reuses the row's own already-computed all-time
// numbers (they mean the exact same thing already). 'month'/'year'
// re-derive Return/Drawdown/Return-Drawdown from the period slice
// above, and Win rate/Profit factor/Return on Capital/Positions closed
// from whichever of the row's own episodes CLOSED within the period --
// independent of whether the fetched snapshot set happens to have a day
// boundary on either side of that close.
function _computePeriodRow(row, scope, periodKey) {
  if (scope === 'all') {
    const p = _lastPoint(row.points);
    return {
      deployment: row.deployment,
      returnPct: p ? p.pct : null,
      rocPct: (p && row.deployment.initial_capital) ? (p.realized_pnl_cumulative / row.deployment.initial_capital) * 100 : null,
      drawdownPct: row.drawdown ? row.drawdown.pct : null,
      ratio: row.returnToDrawdown,
      winRatePct: row.stats.winRatePct,
      profitFactor: row.stats.profitFactor,
      closedCount: row.stats.closedCount,
    };
  }

  const keyFn = scope === 'month' ? _istMonthKey : _istYearKey;
  const periodUnits = row.units.filter(u => u.status === 'closed' && u.closed_at && keyFn(u.closed_at) === periodKey);
  const stats = computeUnitStats(periodUnits);

  let returnPct = null, drawdownPct = null, ratio = null;
  const ps = _periodSlice(row, keyFn, periodKey);
  if (ps) {
    const baseValue = ps.baseline.total_value;
    const lastP = ps.slice[ps.slice.length - 1];
    returnPct = baseValue ? ((lastP.total_value - baseValue) / baseValue) * 100 : 0;
    const snapsForDrawdown = ps.baseline === ps.slice[0] ? ps.slice : [ps.baseline, ...ps.slice];
    const dd = computeMaxDrawdown(snapsForDrawdown, row.deployment.initial_capital);
    drawdownPct = dd ? dd.pct : null;
    if (dd && dd.pct > 0) ratio = returnPct / dd.pct;
    else if (dd && dd.pct === 0 && returnPct > 0) ratio = Infinity;
  }

  return {
    deployment: row.deployment,
    returnPct, drawdownPct, ratio,
    rocPct: row.deployment.initial_capital ? (stats.totalRealizedPnl / row.deployment.initial_capital) * 100 : null,
    winRatePct: stats.winRatePct,
    profitFactor: stats.profitFactor,
    closedCount: stats.closedCount,
  };
}

// Seven ranking categories, per explicit request to "figure out how
// many leaderboards, different type of rankings I can give" -- the
// user's own two suggestions (max return %, highest win rate) plus five
// more the same already-loaded data supports. `eligible` keeps a
// deployment with nothing relevant to show from falsely topping a
// category at 0/undefined -- e.g. no closed positions this period means
// no Win Rate to rank, not a 0% one.
const BOARD_CATEGORIES = [
  {
    key: 'return', label: 'Best Return', icon: '📈', better: 'higher',
    eligible: pr => pr.returnPct != null,
    value: pr => pr.returnPct,
    format: pr => `${pr.returnPct >= 0 ? '+' : ''}${pr.returnPct.toFixed(2)}%`,
  },
  {
    key: 'roc', label: 'Best Return on Capital', icon: '💰', better: 'higher',
    eligible: pr => pr.rocPct != null && pr.closedCount > 0,
    value: pr => pr.rocPct,
    format: pr => `${pr.rocPct >= 0 ? '+' : ''}${pr.rocPct.toFixed(2)}%`,
  },
  {
    key: 'drawdown', label: 'Lowest Drawdown', icon: '🛡️', better: 'lower',
    eligible: pr => pr.drawdownPct != null,
    value: pr => pr.drawdownPct,
    format: pr => `${pr.drawdownPct.toFixed(2)}%`,
  },
  {
    key: 'ratio', label: 'Best Return / Drawdown', icon: '⚖️', better: 'higher',
    eligible: pr => pr.ratio != null,
    value: pr => pr.ratio === Infinity ? Number.MAX_VALUE : pr.ratio,
    format: pr => _fmtRatio(pr.ratio),
  },
  {
    key: 'winrate', label: 'Best Win Rate', icon: '🎯', better: 'higher',
    eligible: pr => pr.winRatePct != null && pr.closedCount > 0,
    value: pr => pr.winRatePct,
    format: pr => fmtPct(pr.winRatePct),
  },
  {
    key: 'profitfactor', label: 'Best Profit Factor', icon: '🏭', better: 'higher',
    eligible: pr => pr.profitFactor != null && pr.closedCount > 0,
    value: pr => pr.profitFactor === Infinity ? Number.MAX_VALUE : pr.profitFactor,
    format: pr => _fmtRatio(pr.profitFactor),
  },
  {
    key: 'active', label: 'Most Active', icon: '⚡', better: 'higher',
    eligible: pr => pr.closedCount > 0,
    value: pr => pr.closedCount,
    format: pr => `${pr.closedCount} closed`,
  },
];

// Top 3 (or fewer, if fewer qualify) eligible rows for one category,
// best first.
function _topBoardRows(cat, periodRows, n = 3) {
  return periodRows
    .filter(pr => cat.eligible(pr))
    .sort((a, b) => cat.better === 'higher' ? cat.value(b) - cat.value(a) : cat.value(a) - cat.value(b))
    .slice(0, n);
}

// Sort key -> comparable value, always a real number/string (never
// null/undefined) so sorting never has to special-case "no data yet" --
// a deployment with nothing to show for a metric just sorts to one end,
// same convention Deployments' own DEPLOY_COLUMNS (Step 93) uses.
// `dir` is each key's OWN sensible default direction the moment it's
// picked from the sort dropdown -- "best first" for every metric,
// alphabetical for name -- overridable afterward via the direction
// toggle button.
const SORT_KEYS = {
  ratio: { label: 'Return / Drawdown', dir: 'desc', value: r => r.returnToDrawdown == null ? -Infinity : (r.returnToDrawdown === Infinity ? Number.MAX_VALUE : r.returnToDrawdown) },
  return: { label: 'Return', dir: 'desc', value: r => { const p = _lastPoint(r.points); return p ? p.pct : -Infinity; } },
  drawdown: { label: 'Max drawdown (smallest first)', dir: 'asc', value: r => r.drawdown ? r.drawdown.pct : Infinity },
  winrate: { label: 'Win rate', dir: 'desc', value: r => r.stats.winRatePct == null ? -1 : r.stats.winRatePct },
  profitfactor: { label: 'Profit factor', dir: 'desc', value: r => r.stats.profitFactor == null ? -1 : (r.stats.profitFactor === Infinity ? Number.MAX_VALUE : r.stats.profitFactor) },
  days: { label: 'Days live', dir: 'desc', value: r => r.points.length },
  equity: { label: 'Current equity', dir: 'desc', value: r => { const p = _lastPoint(r.points); return p ? p.total_value : -Infinity; } },
  name: { label: 'Name', dir: 'asc', value: r => r.deployment.deployment_name.toLowerCase() },
};

const Compare = {
  _rows: [],           // full computed dataset, one entry per deployment -- independent of filter/sort/selection
  _statusFilter: '',    // '' = all statuses
  _sortKey: 'ratio',
  _sortDir: 'desc',
  _selected: new Set(), // deployment ids currently checked for the overlay chart -- Set preserves insertion order, used for stable color assignment
  _boardScope: 'month',  // 'month' | 'year' | 'all' -- Step 113's leaderboards
  _boardPeriod: null,    // e.g. "2026-08" / "2026" / null for 'all' -- null also means "not yet picked," resolved to the latest available period the first time renderBoards runs

  async load() {
    document.getElementById('compareAttention').innerHTML = '';
    document.getElementById('compareBoards').innerHTML = '';
    document.getElementById('compareLeaderboard').innerHTML = spinnerHtml();
    document.getElementById('compareComparisonSection').innerHTML = '';
    document.getElementById('compareExportBtn').style.display = 'none';
    this._rows = [];
    this._statusFilter = '';
    this._sortKey = 'ratio';
    this._sortDir = 'desc';
    this._selected = new Set();
    this._boardScope = 'month';
    this._boardPeriod = null;

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
      realized_pnl_cumulative: s.realized_pnl_cumulative,
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
    this.render();
  },

  _filteredRows() {
    return this._statusFilter ? this._rows.filter(r => r.deployment.status === this._statusFilter) : this._rows;
  },

  // Picking a new key from the sort dropdown adopts THAT key's own
  // sensible default direction (see SORT_KEYS); the toggle button lets
  // it be flipped afterward without needing to re-pick from the list.
  setSort(key) {
    this._sortKey = key;
    this._sortDir = SORT_KEYS[key].dir;
    this.renderLeaderboard();
  },

  toggleSortDir() {
    this._sortDir = this._sortDir === 'asc' ? 'desc' : 'asc';
    this.renderLeaderboard();
  },

  _sortRows(rows) {
    const key = SORT_KEYS[this._sortKey];
    if (!key) return rows;
    const dir = this._sortDir === 'desc' ? -1 : 1;
    return rows.slice().sort((a, b) => {
      const av = key.value(a), bv = key.value(b);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  },

  // Checking a card adds it to the overlay chart below -- no fetch, the
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
    this.renderAttentionPanel();
    this.renderBoards();
    this.renderComparisonSection();
    this.renderLeaderboard();
  },

  // Every month/year key with AT LEAST ONE deployment snapshot in it,
  // across the whole roster -- not blind calendar arithmetic from
  // today's date, so Prev/Next only ever steps to periods that actually
  // have something to show (a 3-week-old account has exactly one month
  // to look at, not twelve empty ones). Plain string sort works
  // correctly for both "YYYY-MM" and "YYYY" keys.
  _availablePeriods(scope) {
    if (scope === 'all') return ['all'];
    const keyFn = scope === 'month' ? _istMonthKey : _istYearKey;
    const keys = new Set();
    this._rows.forEach(r => r.points.forEach(p => keys.add(keyFn(p.snapshot_at))));
    return Array.from(keys).sort();
  },

  setBoardScope(scope) {
    this._boardScope = scope;
    this._boardPeriod = null;   // re-resolved to that scope's latest available period inside renderBoards
    this.renderBoards();
  },

  stepBoardPeriod(delta) {
    const periods = this._availablePeriods(this._boardScope);
    const next = periods.indexOf(this._boardPeriod) + delta;
    if (next < 0 || next >= periods.length) return;
    this._boardPeriod = periods[next];
    this.renderBoards();
  },

  // Step 113 -- three ranking scopes (Month/Year/All-Time), Prev/Next
  // through whatever periods actually have data, one card per category
  // with its own top 3 (🥇🥈🥉). Placed right after "What needs
  // attention" and before the head-to-head/leaderboard cards -- "who's
  // winning" is the natural next question once "what needs attention"
  // has been read, ahead of drilling into any one deployment's numbers.
  renderBoards() {
    const el = document.getElementById('compareBoards');
    if (!this._rows.length) { el.innerHTML = ''; return; }

    const periods = this._availablePeriods(this._boardScope);
    if (!periods.length) { el.innerHTML = ''; return; }
    if (this._boardPeriod == null || !periods.includes(this._boardPeriod)) {
      this._boardPeriod = periods[periods.length - 1];   // default to the most recent period with data
    }
    const idx = periods.indexOf(this._boardPeriod);
    const periodRows = this._rows.map(r => _computePeriodRow(r, this._boardScope, this._boardPeriod));

    const periodLabel = this._boardScope === 'all' ? 'All-Time'
      : this._boardScope === 'month' ? _istMonthLabel(this._boardPeriod)
      : this._boardPeriod;

    const medals = ['🥇', '🥈', '🥉'];
    const cards = BOARD_CATEGORIES.map(cat => {
      const top = _topBoardRows(cat, periodRows);
      const rowsHtml = top.length ? top.map((pr, i) => `
        <div class="board-row">
          <span class="board-medal">${medals[i]}</span>
          <a href="#/deployments/${pr.deployment.id}" class="board-name">${escapeHtml(pr.deployment.deployment_name)}</a>
          <b class="board-value">${cat.format(pr)}</b>
        </div>
      `).join('') : `<div class="board-row board-empty">Nobody qualifies yet</div>`;
      return `
        <div class="card board-card">
          <div class="board-card-head">${cat.icon} ${cat.label}</div>
          ${rowsHtml}
        </div>
      `;
    }).join('');

    const nav = this._boardScope === 'all'
      ? `<div class="report-period-nav"><span class="report-period-label">${escapeHtml(periodLabel)}</span></div>`
      : `
        <div class="report-period-nav">
          <button class="btn btn-sm" onclick="Compare.stepBoardPeriod(-1)" ${idx <= 0 ? 'disabled' : ''} title="Previous ${this._boardScope}">← Prev</button>
          <span class="report-period-label">${escapeHtml(periodLabel)}</span>
          <button class="btn btn-sm" onclick="Compare.stepBoardPeriod(1)" ${idx >= periods.length - 1 ? 'disabled' : ''} title="Next ${this._boardScope}">Next →</button>
        </div>
      `;

    el.innerHTML = `
      <section style="margin-bottom:22px;">
        <h2>🏆 Leaderboards</h2>
        <div class="tabs" id="compareBoardTabs">
          <button class="${this._boardScope === 'month' ? 'active' : ''}" onclick="Compare.setBoardScope('month')">Monthly</button>
          <button class="${this._boardScope === 'year' ? 'active' : ''}" onclick="Compare.setBoardScope('year')">Yearly</button>
          <button class="${this._boardScope === 'all' ? 'active' : ''}" onclick="Compare.setBoardScope('all')">All-Time</button>
        </div>
        ${nav}
        <div class="board-grid">${cards}</div>
      </section>
    `;
  },

  // Step 109 -- the direct answer to "still can't decide": one
  // plain-English line per flagged deployment instead of leaving the
  // reader to interpret a number. Worst news first (a loss streak or a
  // quiet strategy is the most actionable thing to know right now),
  // the positive callout after, and every "too new to judge" row
  // batched into one footnote line rather than given its own -- it's
  // not actionable, just a caveat, and doesn't deserve equal billing
  // with something that might need a decision today. Reflects the
  // CURRENT status filter (not the full unfiltered set), so "what
  // needs attention" always matches what's actually visible below it.
  renderAttentionPanel() {
    const el = document.getElementById('compareAttention');
    const rows = this._filteredRows();
    const byFlag = label => rows.filter(r => r.flag && r.flag.label === label);
    const lossStreaks = byFlag('3-loss streak');
    const quiet = byFlag('Quiet');
    const top = byFlag('Top performer');
    const fresh = byFlag('New');

    const items = [];
    lossStreaks.forEach(r => {
      const lastThree = r.units.filter(u => u.status === 'closed')
        .slice().sort((a, b) => new Date(a.closed_at || a.opened_at) - new Date(b.closed_at || b.opened_at)).slice(-3);
      const total = lastThree.reduce((s, u) => s + u.realized_pnl, 0);
      items.push({ cls: 'attn-bad', html:
        `⚠️ <a href="#/deployments/${r.deployment.id}">${escapeHtml(r.deployment.deployment_name)}</a> has lost its last 3 closed positions in a row ` +
        `(${fmtSignedMoney(total)} combined). Worth a closer look — is the strategy or the market regime still working?` });
    });
    quiet.forEach(r => {
      items.push({ cls: 'attn-warn', html:
        `💤 <a href="#/deployments/${r.deployment.id}">${escapeHtml(r.deployment.deployment_name)}</a> has been running ${r.points.length} days without closing a single position. ` +
        `Worth checking it’s actually generating signals.` });
    });
    top.forEach(r => {
      items.push({ cls: 'attn-good', html:
        `🏆 <a href="#/deployments/${r.deployment.id}">${escapeHtml(r.deployment.deployment_name)}</a> is your best risk-adjusted performer right now ` +
        `(Return/Drawdown ${_fmtRatio(r.returnToDrawdown)} — ${_ratioLabel(r.returnToDrawdown)}). A candidate for more capital, once you trust its track record.` });
    });
    if (fresh.length) {
      items.push({ cls: 'attn-info', html:
        `🆕 ${fresh.length} deployment${fresh.length > 1 ? 's are' : ' is'} too new to judge yet: ` +
        `${fresh.map(r => escapeHtml(r.deployment.deployment_name)).join(', ')}.` });
    }

    if (!items.length) {
      el.innerHTML = '';
      return;
    }
    el.innerHTML = `
      <section style="margin-bottom:22px;">
        <h2>What needs attention</h2>
        <div class="attn-list">${items.map(it => `<div class="attn-item ${it.cls}">${it.html}</div>`).join('')}</div>
      </section>
    `;
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

    const cards = sorted.map(r => {
      const selIdx = selOrder.indexOf(r.deployment.id);
      const isSelected = selIdx !== -1;
      const atCap = !isSelected && this._selected.size >= COMPARE_MAX;
      const last = _lastPoint(r.points);
      const ratioLabel = _ratioLabel(r.returnToDrawdown);
      // A left-border accent in the SAME color as the head-to-head
      // table/chart's own swatch for this row -- scrolling down from
      // the comparison section to the leaderboard still visually ties
      // a card back to which colored column/line it was up there.
      const selColor = isSelected ? COMPARE_COLORS[selIdx % COMPARE_COLORS.length] : null;
      return `
        <div class="card compare-card${isSelected ? ' selected' : ''}" ${selColor ? `style="--card-accent:${selColor};"` : ''}>
          <div class="compare-card-head">
            <label class="compare-card-check" title="${atCap ? `Up to ${COMPARE_MAX} at a time for the chart below` : 'Add to the chart comparison below'}">
              <input type="checkbox" ${isSelected ? 'checked' : ''} ${atCap ? 'disabled' : ''}
                     onchange="Compare.toggleSelect('${r.deployment.id}', this.checked)">
              ${isSelected ? `<span class="legend-swatch" style="background:${selColor}"></span>` : ''}
            </label>
            <div style="flex:1; min-width:0;">
              <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
                <a href="#/deployments/${r.deployment.id}" style="font-weight:700;">${escapeHtml(r.deployment.deployment_name)}</a>
                <span class="tag tag-${r.deployment.status}">${r.deployment.status}</span>
                ${r.flag ? `<span class="tag ${r.flag.cls}" title="${escapeHtml(r.flag.title)}">${r.flag.icon} ${escapeHtml(r.flag.label)}</span>` : ''}
              </div>
              <div style="font-size:10.5px; color:var(--parchment); margin-top:1px;">${escapeHtml(r.deployment.strategy_name)}</div>
            </div>
          </div>
          ${_sparklineSvg(r.points)}
          <div class="compare-card-metrics">
            <div class="row"><span>Initial capital</span><b>${fmtMoney(r.deployment.initial_capital)}</b></div>
            <div class="row"><span>Return</span><b class="${last ? pnlClass(last.pct) : ''}">${last ? `${last.pct >= 0 ? '+' : ''}${last.pct.toFixed(2)}%` : '—'}</b></div>
            <div class="row"><span>Max drawdown</span><b class="${r.drawdown ? 'neg' : ''}">${r.drawdown ? `${fmtMoney(r.drawdown.abs)} (${r.drawdown.pct.toFixed(2)}%)` : '—'}</b></div>
            <div class="row"><span>Return / Drawdown</span><b>${_fmtRatio(r.returnToDrawdown)}${ratioLabel ? ` <span class="compare-ratio-label">${escapeHtml(ratioLabel)}</span>` : ''}</b></div>
            <div class="row"><span>Win rate</span><b>${r.stats.winRatePct == null ? '—' : fmtPct(r.stats.winRatePct)}</b></div>
            <div class="row"><span>Profit factor</span><b>${_fmtRatio(r.stats.profitFactor)}</b></div>
            <div class="row"><span>Positions closed</span><b>${r.stats.closedCount}</b></div>
            <div class="row"><span>Days live</span><b>${r.points.length}</b></div>
            <div class="row"><span>Current equity</span><b>${last ? fmtMoney(last.total_value) : '—'}</b></div>
          </div>
        </div>
      `;
    }).join('');

    el.innerHTML = `
      <div class="filters" style="margin-bottom:12px; align-items:center;">
        <select onchange="Compare.setStatusFilter(this.value)">
          <option value="" ${this._statusFilter === '' ? 'selected' : ''}>All statuses</option>
          <option value="active" ${this._statusFilter === 'active' ? 'selected' : ''}>Active</option>
          <option value="paused" ${this._statusFilter === 'paused' ? 'selected' : ''}>Paused</option>
          <option value="stopped" ${this._statusFilter === 'stopped' ? 'selected' : ''}>Stopped</option>
        </select>
        <select onchange="Compare.setSort(this.value)">
          ${Object.entries(SORT_KEYS).map(([key, def]) =>
            `<option value="${key}" ${this._sortKey === key ? 'selected' : ''}>Sort: ${escapeHtml(def.label)}</option>`
          ).join('')}
        </select>
        <button class="btn btn-secondary btn-sm" onclick="Compare.toggleSortDir()" title="Flip sort direction">
          ${this._sortDir === 'desc' ? '▼ high to low' : '▲ low to high'}
        </button>
        <span class="table-note" style="margin:0;">${sorted.length} deployment(s) · check up to ${COMPARE_MAX} to chart them below</span>
      </div>
      <div class="compare-card-grid">${cards}</div>
      <div class="table-note" style="margin-top:14px;">
        Return/Drawdown is the number most worth ranking by — return earned per unit of capital actually,
        permanently lost, not a live paper swing on anything still open. Win rate/Profit factor/Positions
        closed combine every leg, adjustment, and roll of one strategic bet into a single win or loss.
      </div>
    `;
  },

  // Renders BOTH the head-to-head table and the equity-curve overlay
  // into #compareComparisonSection, which sits ABOVE the leaderboard
  // cards in the DOM (index.html) -- checking 2+ rows makes this
  // section appear and visually pushes the individual cards down,
  // exactly the "cards go to the bottom" behavior asked for. Empty
  // (both sections cleared) when fewer than 2 are checked.
  renderComparisonSection() {
    const el = document.getElementById('compareComparisonSection');
    const selOrder = Array.from(this._selected);
    const compared = selOrder.map(id => this._rows.find(r => r.deployment.id === id)).filter(Boolean);

    if (selOrder.length < 2) {
      el.innerHTML = '';
      return;
    }

    el.innerHTML = `
      <section style="margin-bottom:20px;">
        <h2>Head-to-head</h2>
        ${this._renderHeadToHead(compared)}
      </section>
      <div id="compareEquitySection"></div>
    `;
    this._renderEquityChart(compared);
  },

  // The actual answer to "what am I comparing": ONE ROW PER METRIC
  // (Return, Max drawdown, Return/Drawdown, Win rate, Profit factor,
  // then three context-only rows), one COLUMN per checked deployment --
  // transposed against the leaderboard's own per-deployment-row shape
  // on purpose, since "who wins this metric" is a ROW you read across,
  // not a column you scan down. Every scored row calls out its own
  // winner (🏆) or tie (🤝); a verdict banner above the table tallies
  // wins across every scored row into one overall call, itself capable
  // of being a tie.
  _renderHeadToHead(rows) {
    const overall = _overallWinners(rows);
    const scoredCount = HEAD_TO_HEAD_METRICS.filter(m => m.better).length;
    const verdict = overall.tie
      ? `🤝 Tie — ${overall.winners.map(i => escapeHtml(rows[i].deployment.deployment_name)).join(' & ')} each win ${overall.maxScore} of ${scoredCount} scored metrics`
      : `🏆 ${escapeHtml(rows[overall.winners[0]].deployment.deployment_name)} wins overall — ${overall.maxScore} of ${scoredCount} scored metrics`;

    const headerCells = rows.map((r, i) =>
      `<th><span class="legend-swatch" style="background:${COMPARE_COLORS[i % COMPARE_COLORS.length]}"></span> ${escapeHtml(r.deployment.deployment_name)}</th>`
    ).join('');

    const bodyRows = HEAD_TO_HEAD_METRICS.map(m => {
      const { winners, tie, noData } = _rowWinners(m, rows);
      const cells = rows.map((r, i) => {
        const isWinner = m.better && !noData && winners.has(i);
        const marker = isWinner ? (tie ? ' 🤝' : ' 🏆') : '';
        return `<td class="${isWinner ? 'h2h-winner' : ''}">${m.format(r)}${marker}</td>`;
      }).join('');
      return `<tr class="${m.better ? '' : 'h2h-context'}"><td>${escapeHtml(m.label)}</td>${cells}</tr>`;
    }).join('');

    return `
      <div class="h2h-verdict">${verdict}</div>
      <div class="table-wrap">
      <table class="deploy-table h2h-table"><thead><tr><th>Metric</th>${headerCells}</tr></thead>
      <tbody>${bodyRows}</tbody></table>
      </div>
      <div class="table-note">
        🏆 marks a row's winner, 🤝 a tie — lower is better for Max drawdown, higher for every other marked
        row (Return, Return on Capital, Return/Drawdown, Win rate, Profit factor, Avg P&amp;L per Position).
        Rows that never show a marker (Initial Capital, Realized P&amp;L, Positions closed, Positions/Day,
        Days live, Avg Holding Period, Largest Win, Largest Loss, Current equity) are shown for context but
        don't affect the verdict above.
      </div>
    `;
  },

  _renderEquityChart(compared) {
    const el = document.getElementById('compareEquitySection');
    // A deployment with only 0-1 snapshots has nothing to draw a LINE
    // with (the head-to-head table above still shows its real numbers
    // -- or "—" -- regardless; only the chart itself needs 2+ points).
    // Color index is looked up in the ORIGINAL `compared` order, not
    // position within this filtered list -- otherwise a skipped
    // deployment would shift every later one's color, breaking the
    // link to the head-to-head table's own swatches for the same rows.
    const withData = compared.filter(r => r.points.length >= 2);
    const colorOf = r => COMPARE_COLORS[compared.indexOf(r) % COMPARE_COLORS.length];
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
    // Index-based keeps every curve's full shape readable; each card
    // above carries the real timestamps for anyone who needs them. The
    // hover/touch crosshair (Step 88) follows the same convention: the
    // cursor's X FRACTION (0-1 across the chart) maps independently
    // into each series' own nearest point by that same fraction, since
    // there's no shared index to look up directly across series of
    // different lengths.
    const polylines = withData.map(r => {
      const n = r.points.length;
      const points = r.points.map((p, j) => {
        const x = PAD + (n === 1 ? 0 : (j / (n - 1)) * (W - 2 * PAD));
        const y = H - PAD - ((p.pct - min) / range) * (H - 2 * PAD);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      return `<polyline points="${points}" fill="none" stroke="${colorOf(r)}" stroke-width="2" vector-effect="non-scaling-stroke" />`;
    }).join('');

    const legend = withData.map(r => {
      const last = r.points[r.points.length - 1].pct;
      return `
        <div class="legend-item">
          <span class="legend-swatch" style="background:${colorOf(r)}"></span>
          <span class="legend-name">${escapeHtml(r.deployment.deployment_name)}</span>
          <span class="legend-value ${pnlClass(last)}">${last >= 0 ? '+' : ''}${last.toFixed(2)}%</span>
        </div>
      `;
    }).join('');

    // _compareChartPointerAt (module-level, reused across renders)
    // needs each series' own color for its tooltip -- carry `color`
    // alongside so it doesn't have to re-derive the same compared-order
    // index itself.
    _compareChartSeries = withData.map(r => ({ ...r, color: colorOf(r) }));

    const skipped = compared.length - withData.length;
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
  // as currently filtered/sorted), not a per-snapshot long format:
  // this page is a scorecard, and a scorecard is what's worth taking
  // elsewhere (a note, a spreadsheet of decisions made). The raw
  // per-day series for any single deployment is still on its own
  // Detail page.
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
