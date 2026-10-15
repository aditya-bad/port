// Strategy page — one deployment: live state, performance, cycle replay,
// activity and settings.
import { html, raw, mount, $, on, money, num, pct, tone, ago, date, dateY, time, weekday, duration, istKey, ICONS } from '../util.js';
import { api, pick } from '../api.js';
import { bus, livePnl, livePrice } from '../live.js';
import { menu, toast, toastError } from '../ui.js';
import { bars, line, replay } from '../charts.js';
import { statusBadge, pnlText, kpi, empty, skeleton, groupFills, reasonBadge, reasonKind, when, winRate, pctText } from '../components.js';
import { configForm } from '../configform.js';
import * as act from '../actions.js';
import { switchToClassic } from '../app.js';

const TABS = [['overview', 'Overview'], ['performance', 'Performance'], ['replay', 'Cycle replay'], ['activity', 'Activity'], ['settings', 'Settings']];
let S = null;            // loaded data for the current deployment
const view = { gran: 'position', actKind: 'all', actMode: 'fills', strikes: true };

async function load(id) {
  const [d, positions, trades, snapshots, digest, status] = await Promise.all([
    api.deployment(id), api.positions(id, 'all'), api.trades(id, 2000),
    api.snapshots(id).catch(() => []), api.digest(id, 'day', 400).catch(() => []),
    api.strategyStatus(id).catch(() => null),
  ]);
  return { d, positions, lots: trades.lots || [], snapshots, digest, status, events: null, cycles: null };
}

// ── Derived stats ──────────────────────────────────────────────────
function closingReasons() {
  const side = new Map(S.positions.map(p => [p.id, p.side]));
  const out = new Map();
  for (const l of S.lots) {
    const s = side.get(l.position_id);
    if ((s === 'long' && l.action === 'sell') || (s === 'short' && l.action === 'buy')) out.set(l.position_id, l.reason);
  }
  return out;
}
function units(gran) {
  const reasons = closingReasons();
  const groups = new Map();
  for (const p of S.positions) {
    const key = gran === 'trade' ? p.id : `${p.episode_opened_at}|${p.episode_closed_at || 'open'}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }
  return [...groups.values()].map(rows => {
    const closed = rows.every(p => p.status === 'closed');
    const last = rows.reduce((a, p) => (!a || (p.closed_at && p.closed_at > a.closed_at) ? p : a), null);
    return {
      opened_at: gran === 'trade' ? rows[0].opened_at : rows[0].episode_opened_at,
      closed_at: closed ? (gran === 'trade' ? rows[0].closed_at : rows[0].episode_closed_at) : null,
      status: closed ? 'closed' : 'open',
      pnl: rows.reduce((a, p) => a + (pick(p, 'realized_pnl') || 0), 0),
      legs: rows.length,
      reason: last ? reasons.get(last.id) : null,
    };
  }).sort((a, b) => new Date(a.opened_at) - new Date(b.opened_at));
}
function stats(us) {
  const closed = us.filter(u => u.status === 'closed');
  const pnls = closed.map(u => u.pnl);
  const wins = pnls.filter(v => v > 0), losses = pnls.filter(v => v < 0);
  const gw = wins.reduce((a, b) => a + b, 0), gl = losses.reduce((a, b) => a + b, 0);
  const holds = closed.filter(u => u.closed_at).map(u => new Date(u.closed_at) - new Date(u.opened_at));
  return {
    closed: closed.length, open: us.length - closed.length,
    winRate: winRate(wins.length, losses.length),
    pf: gl < 0 ? gw / -gl : gw > 0 ? Infinity : null,
    avgWin: wins.length ? gw / wins.length : null, avgLoss: losses.length ? gl / losses.length : null,
    expectancy: pnls.length ? pnls.reduce((a, b) => a + b, 0) / pnls.length : null,
    best: pnls.length ? Math.max(...pnls) : null, worst: pnls.length ? Math.min(...pnls) : null,
    hold: holds.length ? holds.reduce((a, b) => a + b, 0) / holds.length : null,
  };
}
function drawdown() {
  let peak = -Infinity, maxDd = 0, maxPct = 0;
  for (const s of S.snapshots) {
    peak = Math.max(peak, s.total_value);
    const dd = peak - s.total_value;
    if (dd > maxDd) { maxDd = dd; maxPct = peak ? dd / peak * 100 : 0; }
  }
  return { abs: maxDd, pct: maxPct };
}
const openPositions = () => S.positions.filter(p => p.status === 'open');
const openPnl = () => openPositions().reduce((a, p) => a + (livePnl(p) ?? (pick(p, 'unrealized_pnl') || 0)), 0);

// ── Header ─────────────────────────────────────────────────────────
function header(tab) {
  const d = S.d;
  const realized = pick(d, 'realized_pnl') || 0, open = openPnl(), total = realized + open;
  const st = stats(units('position'));
  const dd = drawdown();
  return html`
    <div class="crumb"><a href="#/strategies">${raw(ICONS.back)} Strategies</a></div>
    <div class="page-head">
      <div>
        <div class="actions" style="gap:10px"><h1>${d.deployment_name}</h1>${statusBadge(d.status)}</div>
        <div class="sub">${d.strategy_name} · ${d.mode} · ${money(d.initial_capital, { dp: 0 })} capital · deployed ${ago(d.created_at)}${d.include_in_reports ? '' : ' · excluded from reports'}</div>
        ${d.notes ? html`<div class="sub small" style="margin-top:4px;max-width:720px">${d.notes}</div>` : ''}
      </div>
      <div class="actions">
        ${d.status === 'active' ? html`<button class="btn" data-act="pause">Pause</button>` : ''}
        ${d.status === 'paused' ? html`<button class="btn primary" data-act="resume">Resume</button>` : ''}
        ${d.status !== 'stopped' && openPositions().length ? html`<button class="btn danger" data-act="flatten">Close positions</button>` : ''}
        <button class="btn icon" data-act="more" aria-label="More actions">${raw(ICONS.more)}</button>
      </div>
    </div>
    <div class="grid kpis" style="margin-bottom:18px">
      ${kpi('Total P&L', html`<span id="kTotal" class="${tone(total)}">${money(total, { sign: true })}</span>`, html`<span class="${tone(total)}" id="kRet">${pct(d.initial_capital ? total / d.initial_capital * 100 : null, { sign: true })}</span> on capital`)}
      ${kpi('Realized', pnlText(realized), `${st.closed} closed position${st.closed === 1 ? '' : 's'}`)}
      ${kpi('Open', html`<span id="kOpen" class="${tone(open)}">${openPositions().length ? money(open, { sign: true }) : '—'}</span>`, `${openPositions().length} open leg${openPositions().length === 1 ? '' : 's'}`)}
      ${kpi('Win rate', pctText(st.winRate), st.pf == null ? 'Per whole position' : `Profit factor ${st.pf === Infinity ? '∞' : st.pf.toFixed(2)}`)}
      ${kpi('Max drawdown', dd.abs ? html`<span class="neg">${money(-dd.abs)}</span>` : '—', dd.abs ? `${pct(-dd.pct)} from peak` : 'Settled equity')}
    </div>
    <nav class="tabs" aria-label="Sections">${TABS.map(([k, l]) => html`<a href="#/s/${d.id}/${k}" class="${tab === k ? 'on' : ''}" aria-current="${tab === k ? 'page' : 'false'}">${l}</a>`)}</nav>`;
}

// ── Tabs ───────────────────────────────────────────────────────────
function overview() {
  const d = S.d, open = openPositions();
  const fields = S.status && S.status.fields ? S.status.fields : [];
  const today = istKey(new Date());
  const isPositional = d.mode === 'positional';
  const openEpisode = isPositional && open.length ? open[0].episode_opened_at : null;
  const curRows = isPositional
    ? S.positions.filter(p => openEpisode && p.episode_opened_at === openEpisode)
    : S.positions.filter(p => istKey(p.opened_at) === today);
  const curRealized = curRows.reduce((a, p) => a + (pick(p, 'realized_pnl') || 0), 0);
  S.curRealized = curRealized;
  const us = units('position').filter(u => u.status === 'closed');
  const lastUnit = us[us.length - 1];
  const groups = groupFills(S.lots.map(l => ({ ...l, deployment_id: d.id, deployment_name: d.deployment_name }))).slice(0, 8);
  return html`
    <div class="grid two">
      <div class="stack">
        <section class="card">
          <div class="card-head"><div><h2>${isPositional ? (openEpisode ? 'Current cycle' : 'No open cycle') : 'Today'}</h2>
            <div class="sub">${isPositional ? (openEpisode ? `Started ${weekday(openEpisode)} · ${duration(Date.now() - new Date(openEpisode))}` : (lastUnit ? `Last cycle closed ${ago(lastUnit.closed_at)}` : 'Waiting for the first entry')) : weekday(new Date())}</div></div>
            ${isPositional && !openEpisode && lastUnit ? html`<div style="text-align:right"><div class="label">Last cycle</div>${pnlText(lastUnit.pnl)}</div>` : html`<div style="text-align:right"><div class="label">Realized + open</div><b id="curTotal">${pnlText(curRealized + openPnl())}</b></div>`}</div>
          ${open.length ? html`<div class="table-wrap"><table class="t">
            <thead><tr><th>Contract</th><th class="hide-mobile">Side</th><th class="num hide-mobile">Qty</th><th class="num hide-mobile">Entry</th><th class="num">LTP</th><th class="num">P&amp;L</th><th class="num hide-mobile">Opened</th></tr></thead>
            <tbody>${open.map(p => html`<tr><td class="mono small">${p.symbol}<div class="cell-sub only-mobile">${p.side} ${num(p.qty, 0)} @ ${num(p.avg_entry_price)}</div></td><td class="hide-mobile">${p.side}</td><td class="num hide-mobile">${num(p.qty, 0)}</td><td class="num hide-mobile">${num(p.avg_entry_price)}</td>
              <td class="num" data-ltp="${p.id}">${num(livePrice(p))}</td><td class="num" data-pnl="${p.id}">${pnlText(livePnl(p))}</td><td class="num sub hide-mobile">${when(p.opened_at)}</td></tr>`)}</tbody>
          </table></div>` : empty('No open positions', d.status === 'active' ? 'The strategy enters on its next signal.' : `The strategy is ${d.status}.`)}
        </section>
        <section class="card">
          <div class="card-head"><div><h2>Equity</h2><div class="sub">End-of-day account value · baseline is starting capital</div></div></div>
          <div class="card-body"><div class="chart" id="equity"></div></div>
        </section>
      </div>
      <div class="stack">
        ${fields.length ? html`<section class="card"><div class="card-head"><div><h2>Strategy state</h2><div class="sub">${S.status.source === 'live' ? 'Live from the running strategy' : 'Last saved state'}</div></div></div>
          <div class="rows">${fields.map(f => html`<div class="row-item"><span class="sub">${f.label}</span><span class="right" style="font-weight:600">${f.value}</span></div>`)}</div></section>` : ''}
        <section class="card">
          <div class="card-head"><div><h2>Recent activity</h2></div><a class="btn ghost sm" href="#/s/${d.id}/activity">All activity</a></div>
          <div class="rows">${groups.length ? groups.map(g => html`<div class="feed-item"><div class="feed-time">${when(g.t)}</div>
            <div><div class="feed-title">${g.title}</div><div class="feed-detail">${g.lots.map(l => `${l.action === 'sell' ? 'Sold' : 'Bought'} ${l.symbol} @ ${num(l.price)}`).join(' · ')}</div></div>
            <div>${reasonBadge(g.reasons[0])}</div></div>`) : empty('No fills yet', '')}</div>
        </section>
      </div>
    </div>`;
}

function performance() {
  const us = units(view.gran);
  const st = stats(us);
  const closed = us.filter(u => u.status === 'closed');
  // monthly
  const months = new Map();
  for (const u of closed) {
    const k = istKey(u.closed_at).slice(0, 7);
    const m = months.get(k) || { k, pnl: 0, n: 0, w: 0, l: 0 };
    m.pnl += u.pnl; m.n++; if (u.pnl > 0) m.w++; else if (u.pnl < 0) m.l++;
    months.set(k, m);
  }
  const monthRows = [...months.values()].sort((a, b) => b.k.localeCompare(a.k));
  // exit reasons
  const reasons = new Map();
  for (const u of closed) {
    const k = u.reason || 'unknown';
    const r = reasons.get(k) || { k, pnl: 0, n: 0 };
    r.pnl += u.pnl; r.n++;
    reasons.set(k, r);
  }
  const reasonRows = [...reasons.values()].sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl));
  const maxAbs = Math.max(1, ...reasonRows.map(r => Math.abs(r.pnl)));
  const unitLabel = view.gran === 'position' ? 'position' : 'trade';
  return html`
    <div class="actions" style="justify-content:space-between;margin-bottom:14px">
      <div class="sub">${view.gran === 'position' ? 'A straddle’s legs plus every roll and adjustment count as one position.' : 'Every individual leg counts on its own.'}</div>
      <div class="seg" role="group" aria-label="Granularity"><button data-gran="position" class="${view.gran === 'position' ? 'on' : ''}">Per position</button><button data-gran="trade" class="${view.gran === 'trade' ? 'on' : ''}">Per trade</button></div>
    </div>
    <div class="grid kpis" style="margin-bottom:18px">
      ${kpi(`Closed ${unitLabel}s`, num(st.closed, 0), st.open ? `${st.open} open` : 'None open')}
      ${kpi('Win rate', pctText(st.winRate), st.pf == null ? '' : `Profit factor ${st.pf === Infinity ? '∞' : st.pf.toFixed(2)}`)}
      ${kpi('Average win', st.avgWin == null ? '—' : pnlText(st.avgWin), st.avgLoss == null ? '' : html`Average loss ${pnlText(st.avgLoss)}`)}
      ${kpi('Expectancy', st.expectancy == null ? '—' : pnlText(st.expectancy), `Per ${unitLabel}`)}
      ${kpi('Best / worst', st.best == null ? '—' : pnlText(st.best), st.worst == null ? '' : html`Worst ${pnlText(st.worst)}`)}
      ${kpi('Average hold', st.hold == null ? '—' : duration(st.hold), 'Open to close')}
    </div>
    <section class="card" style="margin-bottom:18px">
      <div class="card-head"><div><h2>${S.d.mode === 'positional' ? 'P&L per cycle' : 'P&L per day'}</h2><div class="sub">${S.d.mode === 'positional' ? 'Each bar is one closed cycle' : 'Realized P&L per trading day'}</div></div></div>
      <div class="card-body"><div class="chart" id="perfBars"></div></div>
    </section>
    <div class="grid halves">
      <section class="card">
        <div class="card-head"><div><h2>By month</h2><div class="sub">Closed ${unitLabel}s, by IST month of close</div></div></div>
        ${monthRows.length ? html`<div class="table-wrap"><table class="t"><thead><tr><th>Month</th><th class="num">P&amp;L</th><th class="num">${unitLabel[0].toUpperCase() + unitLabel.slice(1)}s</th><th class="num">Win rate</th></tr></thead>
          <tbody>${monthRows.map(m => html`<tr><td>${new Date(`${m.k}-15T00:00:00Z`).toLocaleDateString('en-IN', { month: 'long', year: 'numeric' })}</td><td class="num">${pnlText(m.pnl)}</td><td class="num">${m.n}</td><td class="num">${pctText(winRate(m.w, m.l))}</td></tr>`)}</tbody></table></div>`
          : empty('Nothing closed yet', '')}
      </section>
      <section class="card">
        <div class="card-head"><div><h2>By exit reason</h2><div class="sub">What actually made or lost money</div></div></div>
        <div class="card-body">${reasonRows.length ? html`<div class="bar-list">${reasonRows.map(r => html`<div class="bar-row">
          <span class="name" title="${r.k}">${reasonBadge(r.k)} ${r.k}</span>
          <div class="bar-track"><span class="zero"></span><i class="${r.pnl >= 0 ? 'pos' : 'neg'}" style="${r.pnl >= 0 ? `left:50%;width:${Math.abs(r.pnl) / maxAbs * 50}%` : `right:50%;width:${Math.abs(r.pnl) / maxAbs * 50}%`}"></i></div>
          <span class="nowrap">${pnlText(r.pnl)} <span class="muted small">×${r.n}</span></span></div>`)}</div>` : empty('Nothing closed yet', '')}</div>
      </section>
    </div>`;
}

function cycleLabel(c) {
  return S.d.mode === 'positional' ? `${date(c.start)} → ${c.end ? date(c.end) : 'open'}` : weekday(c.start);
}
function replayTab(sub) {
  const cs = S.cycles || [];
  if (!cs.length) return empty('No cycles yet', 'Every cycle (or trading day, for intraday) gets its own replay once the strategy trades.');
  const key = cs.some(c => c.key === sub) ? sub : cs[0].key;
  S.cycleKey = key;
  return html`
    <div class="grid replay-grid" id="replayGrid">
      <section class="card replay-list">
        <div class="card-head"><h2>${cs.length} ${S.d.mode === 'positional' ? 'cycle' : 'day'}${cs.length === 1 ? '' : 's'}</h2></div>
        <div class="rows">${cs.map(c => html`<a class="row-item" href="#/s/${S.d.id}/replay/${c.key}" style="${c.key === key ? 'background:var(--accent-soft)' : ''}" aria-current="${c.key === key}">
          <div><div class="cell-title" style="font-size:13px">${cycleLabel(c)}</div><div class="cell-sub">${c.positions} legs · ${c.adjustments} adj · ${duration((c.end ? new Date(c.end) : new Date()) - new Date(c.start))}</div></div>
          <div class="right">${pnlText(pick(c, 'realized_pnl'))}${c.status === 'open' ? html`<div class="cell-sub">open</div>` : ''}</div></a>`)}</div>
      </section>
      <div class="stack" id="replayBody"><div class="skeleton" style="height:420px"></div></div>
    </div>`;
}
async function paintReplay(main, cleanups) {
  const body = $('#replayBody', main);
  if (!body) return;
  const key = S.cycleKey;
  let data;
  try { data = await api.cycleChart(S.d.id, key); } catch (e) { mount(body, html`<div class="card">${empty('Couldn’t load this cycle', e.message)}</div>`); return; }
  if (S.cycleKey !== key || !document.body.contains(body)) return;
  const s = data.stats, u = s.underlying;
  const final = data.cycle.status === 'closed' ? pick(s, 'realized_pnl') : [...data.series.pnl].reverse().find(v => v != null);
  const at = ms => (ms ? `${date(ms)} ${time(ms)}` : '');
  mount(body, html`
    <div class="grid kpis">
      ${kpi(data.cycle.status === 'closed' ? 'Cycle P&L' : 'Cycle P&L (live)', pnlText(final), `${s.positions} legs · ${s.adjustments} adjustment${s.adjustments === 1 ? '' : 's'} · ${duration(s.duration_minutes * 60000)}`)}
      ${u ? kpi(`${data.underlying_symbol || 'Underlying'} move`, html`<span class="${tone(u.change)}">${u.change > 0 ? '+' : ''}${num(u.change)}</span>`, `${num(u.open)} → ${num(u.close)} (${pct(u.change_pct, { sign: true })})`) : ''}
      ${u ? kpi('Underlying range', `${num(u.high - u.low)} pts`, `High ${num(u.high)} · Low ${num(u.low)}`) : ''}
      ${s.max_pnl != null ? kpi('Best / worst point', pnlText(s.max_pnl), html`Best ${at(s.max_pnl_t)} · worst ${pnlText(s.min_pnl)}`) : ''}
    </div>
    <section class="card">
      <div class="card-head" style="flex-wrap:wrap"><div class="legend">
        <span><i class="lk"></i>${data.underlying_symbol || 'Underlying'}</span><span><i class="lk pos"></i>Cycle P&amp;L (gross)</span>
        <span><i class="lk dash"></i>Strikes held</span><span><i class="lk shape entry"></i>Entry</span><span><i class="lk shape adjustment"></i>Adjustment</span><span><i class="lk shape exit"></i>Exit</span>
        ${data.snapshot_pnl.length && data.series.pnl.some(v => v == null) ? html`<span><i class="lk dash"></i>P&amp;L from account snapshots</span>` : ''}</div>
        <label class="actions small" style="gap:8px"><span class="switch"><input type="checkbox" data-act="strikes" ${view.strikes ? 'checked' : ''}><i></i></span>Strikes</label></div>
      <div class="card-body"><div class="chart" id="replayChart"></div>
        ${[data.underlying_error, ...data.notes].filter(Boolean).map(n => html`<div class="sub small" style="margin-top:8px">${n}</div>`)}
        <div class="sub small" style="margin-top:8px">Hover, tap or use ←/→ for the underlying, the cycle P&amp;L and every leg’s P&amp;L at that moment · ${data.window.interval} candles</div></div>
    </section>
    <section class="card"><div class="card-head"><h2>Entries, adjustments &amp; exits</h2></div>
      <div class="table-wrap"><table class="t"><thead><tr><th>When</th><th>Type</th><th>Fills</th><th>Reason</th></tr></thead>
      <tbody>${data.events.map(ev => html`<tr><td class="nowrap">${at(ev.t)}</td><td><span class="badge ${ev.kind === 'adjustment' ? 'adjust' : ev.kind}">${ev.kind}</span></td>
        <td class="small">${ev.fills.map(f => html`<div>${f.opening ? (f.action === 'sell' ? 'Sold' : 'Bought') : (f.action === 'buy' ? 'Bought back' : 'Sold out')} <span class="mono">${f.symbol}</span> @ ${num(f.price)}</div>`)}</td>
        <td class="sub small">${ev.reason || '—'}</td></tr>`)}</tbody></table></div></section>`);
  const strikes = $('[data-act="strikes"]', body);
  let stop = replay($('#replayChart', body), data, { showStrikes: view.strikes });
  strikes.onchange = () => { view.strikes = strikes.checked; stop(); stop = replay($('#replayChart', body), data, { showStrikes: view.strikes }); };
  cleanups.push(() => stop());
}

function activity() {
  if (view.actMode === 'events') {
    const evs = S.events || [];
    return html`${activityToolbar()}
      <section class="card">${evs.length ? html`<div class="table-wrap"><table class="t"><thead><tr><th>When</th><th>Event</th><th>Detail</th></tr></thead>
        <tbody>${evs.map(e => html`<tr><td class="nowrap">${date(e.created_at)} ${time(e.created_at)}</td><td><span class="badge outline">${(e.event_type || '').replace(/_/g, ' ')}</span></td><td class="sub small">${e.message || ''}</td></tr>`)}</tbody></table></div>`
        : empty('No events recorded', '')}</section>`;
  }
  const lots = S.lots.filter(l => view.actKind === 'all' || reasonKind(l.reason) === view.actKind);
  return html`${activityToolbar()}
    <section class="card">${lots.length ? html`<div class="table-wrap"><table class="t">
      <thead><tr><th>When</th><th>Contract</th><th>Action</th><th class="num">Qty</th><th class="num">Price</th><th>Reason</th></tr></thead>
      <tbody>${lots.slice(0, 600).map(l => html`<tr><td class="nowrap">${date(l.executed_at)} ${time(l.executed_at)}</td><td class="mono small">${l.symbol}</td>
        <td>${l.action === 'sell' ? 'Sell' : 'Buy'}</td><td class="num">${num(l.qty, 0)}</td><td class="num">${num(l.price)}</td>
        <td>${reasonBadge(l.reason)} <span class="sub small">${l.reason || ''}</span></td></tr>`)}</tbody></table></div>
      ${lots.length > 600 ? html`<div class="empty">Showing the latest 600 of ${lots.length} fills — export CSV for everything.</div>` : ''}`
      : empty('No fills match', '')}</section>`;
}
function activityToolbar() {
  return html`<div class="actions" style="margin-bottom:14px;justify-content:space-between">
    <div class="actions">
      <div class="seg" role="group" aria-label="Show"><button data-amode="fills" class="${view.actMode === 'fills' ? 'on' : ''}">Fills</button><button data-amode="events" class="${view.actMode === 'events' ? 'on' : ''}">Events</button></div>
      ${view.actMode === 'fills' ? html`<div class="chips">${[['all', 'All'], ['entry', 'Entries'], ['adjust', 'Adjustments'], ['profit', 'Targets'], ['stop', 'Exits']].map(([k, l]) => html`<button class="chip ${view.actKind === k ? 'on' : ''}" data-akind="${k}">${l}</button>`)}</div>` : ''}
    </div>
    ${view.actMode === 'fills' ? html`<button class="btn" data-act="csv">Export CSV</button>` : ''}</div>`;
}

function settings() {
  const d = S.d;
  return html`
    <div class="grid halves" style="align-items:start">
      <section class="card">
        <div class="card-head"><h2>Details</h2></div>
        <div class="card-body stack">
          <label class="field"><span>Name</span><input class="input" id="sName" value="${d.deployment_name}"></label>
          <label class="field"><span>Notes</span><textarea class="input" id="sNotes" rows="3" style="font-family:var(--font)">${d.notes || ''}</textarea></label>
          <div><button class="btn primary" data-act="save-details">Save details</button></div>
        </div>
      </section>
      <section class="card">
        <div class="card-head"><h2>Reporting &amp; alerts</h2></div>
        <div class="card-body">
          <div class="toggle-row"><div><b>Include in reports</b><div class="sub small">Counts toward Performance and portfolio totals.</div></div>
            <label class="switch"><input type="checkbox" data-toggle="include_in_reports" ${d.include_in_reports ? 'checked' : ''}><i></i></label></div>
          <div class="toggle-row"><div><b>Phone notifications</b><div class="sub small">Push alerts for this strategy’s entries and exits.</div></div>
            <label class="switch"><input type="checkbox" data-toggle="notifications_enabled" ${d.notifications_enabled ? 'checked' : ''}><i></i></label></div>
        </div>
      </section>
    </div>
    <section class="card" style="margin-top:18px">
      <div class="card-head"><div><h2>Configuration</h2><div class="sub">${d.status === 'paused' ? 'Changes apply when you resume.' : d.status === 'active' ? 'Pause the strategy to edit its configuration.' : 'Stopped deployments can’t be reconfigured.'}</div></div>
        ${d.status === 'paused' ? html`<button class="btn primary" data-act="save-config">Save configuration</button>` : d.status === 'active' ? html`<button class="btn" data-act="pause">Pause to edit</button>` : ''}</div>
      <div class="card-body" id="cfg"></div>
    </section>`;
}

// ── Page ───────────────────────────────────────────────────────────
async function paint(main, params, cleanups) {
  const tab = TABS.some(([k]) => k === params.tab) ? params.tab : 'overview';
  if (tab === 'replay' && !S.cycles) S.cycles = await api.cycles(S.d.id).catch(() => []);
  if (tab === 'activity' && view.actMode === 'events' && !S.events) S.events = await api.events(S.d.id).catch(() => []);
  const body = { overview, performance, replay: () => replayTab(params.sub), activity, settings }[tab]();
  mount(main, html`${header(tab)}<div id="tabBody">${body}</div>`);
  if (tab === 'overview') {
    const pts = S.snapshots.map(s => ({ t: s.snapshot_at, v: s.total_value }));
    cleanups.push(line($('#equity', main), pts, { label: 'Equity', baseline: S.d.initial_capital, fill: 'sign', height: 210 }));
  }
  if (tab === 'performance') {
    const data = S.d.mode === 'positional'
      ? units('position').filter(u => u.status === 'closed').slice(-60).map(u => ({ t: u.closed_at, value: u.pnl, label: `${date(u.opened_at)}–${date(u.closed_at)}` }))
      : [...S.digest].reverse().slice(-90).map(r => ({ t: r.period_start, value: pick(r, 'realized_pnl') || 0, detail: `<div class="tip-row"><span>Closed</span><b>${r.positions_closed}</b></div>` }));
    cleanups.push(bars($('#perfBars', main), data, { height: 210 }));
  }
  if (tab === 'replay') paintReplay(main, cleanups);
  if (tab === 'settings') {
    let defaults = {};
    try { const all = await api.strategies(); defaults = (all.find(s => s.name === S.d.strategy_name) || {}).default_config || {}; } catch (e) { /* show stored config only */ }
    S.form = configForm($('#cfg', main), { defaults, values: S.d.config || {}, disabled: S.d.status !== 'paused' });
  }
}

export default {
  title: 'Strategy',
  async render(main, params) {
    const sameDeployment = S && S.d.id === params.id;
    if (!sameDeployment) { mount(main, skeleton(4)); S = await load(params.id); }
    const cleanups = [];
    const refresh = async () => {
      const keepCycles = S.cycles, keepKey = S.cycleKey;
      S = await load(params.id);
      if (params.tab === 'replay') { S.cycles = await api.cycles(params.id).catch(() => keepCycles); S.cycleKey = keepKey; }
      cleanups.splice(0).forEach(f => f());
      await paint(main, params, cleanups);
    };
    this._refresh = refresh;
    await paint(main, params, cleanups);
    const d = () => S.d;
    const run = fn => async () => { if (await fn()) refresh().catch(() => {}); };
    const offs = [
      on(main, 'click', '[data-act="pause"]', run(() => act.pause(d()))),
      on(main, 'click', '[data-act="resume"]', run(() => act.resume(d()))),
      on(main, 'click', '[data-act="flatten"]', run(() => act.flatten(d()))),
      on(main, 'click', '[data-act="more"]', (e, b) => menu(b, [
        { label: 'Edit details & configuration', onClick: () => { location.hash = `#/s/${d().id}/settings`; } },
        { label: 'Replay cycles', onClick: () => { location.hash = `#/s/${d().id}/replay`; } },
        d().status !== 'stopped' && { label: 'Remove current trade…', danger: true, onClick: run(() => act.removeTrades(d())) },
        d().status !== 'stopped' && { label: 'Stop…', danger: true, onClick: run(() => act.stop(d(), openPositions().length > 0)) },
        d().status === 'stopped' && { label: 'Delete…', danger: true, onClick: async () => { if (await act.remove(d())) location.hash = '#/strategies'; } },
        { sep: true },
        { label: 'Open in classic experience', onClick: () => switchToClassic(`#/deployments/${d().id}`) },
      ])),
      on(main, 'click', '[data-gran]', (e, b) => { view.gran = b.dataset.gran; cleanups.splice(0).forEach(f => f()); paint(main, params, cleanups); }),
      on(main, 'click', '[data-akind]', (e, b) => { view.actKind = b.dataset.akind; paint(main, params, cleanups); }),
      on(main, 'click', '[data-amode]', async (e, b) => { view.actMode = b.dataset.amode; await paint(main, params, cleanups); }),
      on(main, 'click', '[data-act="csv"]', () => {
        const rows = [['Time', 'Contract', 'Action', 'Qty', 'Price', 'Reason'], ...S.lots.map(l => [l.executed_at, l.symbol, l.action, l.qty, l.price, l.reason || ''])];
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n')], { type: 'text/csv' }));
        a.download = `${S.d.deployment_name.replace(/\W+/g, '_')}_fills.csv`;
        a.click();
      }),
      on(main, 'click', '[data-act="save-details"]', async () => {
        const name = $('#sName', main).value.trim();
        if (!name) { toastError(new Error('Name can’t be empty'), 'Not saved'); return; }
        try { await api.updateDeployment(d().id, { deployment_name: name, notes: $('#sNotes', main).value }); toast('Details saved'); refresh(); }
        catch (e) { toastError(e, 'Not saved'); }
      }),
      on(main, 'change', '[data-toggle]', async (e, inp) => {
        try { await api.updateDeployment(d().id, { [inp.dataset.toggle]: inp.checked }); toast('Saved'); S.d[inp.dataset.toggle] = inp.checked; }
        catch (err) { inp.checked = !inp.checked; toastError(err, 'Not saved'); }
      }),
      on(main, 'click', '[data-act="save-config"]', async () => {
        let cfg;
        try { cfg = S.form.read(); } catch (e) { toastError(e, 'Check the configuration'); return; }
        try { await api.updateDeployment(d().id, { config: cfg }); toast('Configuration saved', 'It takes effect when you resume.'); refresh(); }
        catch (e) { toastError(e, 'Configuration not saved'); }
      }),
    ];
    const onTicks = () => {
      for (const p of openPositions()) {
        const c = main.querySelector(`[data-pnl="${p.id}"]`); if (c) c.innerHTML = String(pnlText(livePnl(p)));
        const l = main.querySelector(`[data-ltp="${p.id}"]`); if (l) l.textContent = num(livePrice(p));
      }
      const open = openPnl(), total = (pick(S.d, 'realized_pnl') || 0) + open;
      const kt = $('#kTotal', main); if (kt) { kt.textContent = money(total, { sign: true }); kt.className = tone(total); }
      const ko = $('#kOpen', main); if (ko && openPositions().length) { ko.textContent = money(open, { sign: true }); ko.className = tone(open); }
      const ct = $('#curTotal', main); if (ct && S.curRealized != null) ct.innerHTML = String(pnlText(S.curRealized + open));
    };
    bus.addEventListener('ticks', onTicks);
    return () => { cleanups.forEach(f => f()); offs.forEach(f => f()); bus.removeEventListener('ticks', onTicks); this._refresh = null; };
  },
  onChanged() { if (this._refresh) this._refresh().catch(() => {}); },
};
