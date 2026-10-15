// Today — what's happening right now, or (after hours) how the last
// session went. Never a wall of ₹0.
import { html, raw, mount, $, $$, money, num, weekday, session, istKey, tone, pct, ago } from '../util.js';
import { api, pick, pnlMode, MODE_LABEL } from '../api.js';
import { bus, status, livePnl, livePrice, loginWithKite } from '../live.js';
import { toastError } from '../ui.js';
import { bars, sparkline } from '../charts.js';
import { pnlText, statusBadge, empty, skeleton, groupFills, when, winRate, pctText, reasonBadge } from '../components.js';
import { rememberDeployments } from '../app.js';

let state = null;

async function load() {
  const [deps, active, open, digest, recent] = await Promise.all([
    api.deployments(), api.activePeriods(), api.openPositions(),
    api.portfolioDigest('day', 30), api.recentTrades(80).catch(() => []),
  ]);
  rememberDeployments(deps);
  return { deps, active, open, digest, recent };
}

function heroModel(s) {
  const sess = session();
  const todayKey = istKey(new Date());
  const latest = s.digest[0];
  const latestIsToday = latest && istKey(latest.period_start) === todayKey;
  const realizedToday = s.active.reduce((a, r) => a + (pick(r, 'today_realized_pnl') || 0), 0);
  const openPnl = s.open.reduce((a, p) => a + (livePnl(p) || 0), 0);
  const live = sess.open || s.open.length > 0 || latestIsToday || Math.abs(realizedToday) > 0.004;
  if (live) {
    return {
      live: true, label: `Today · ${weekday(new Date())}`, value: realizedToday + openPnl,
      parts: [['Realized', realizedToday], ['Open', openPnl]],
      note: s.open.length ? `${s.open.length} open position${s.open.length === 1 ? '' : 's'}` : 'No open positions',
    };
  }
  if (!latest) return { live: false, label: 'No trading history yet', value: null, parts: [], note: 'Deploy a strategy to get started.' };
  const wr = winRate(latest.wins, latest.losses);
  return {
    live: false, label: `Last session · ${weekday(latest.period_start)}`, value: pick(latest, 'realized_pnl'),
    parts: [], note: `${latest.positions_closed} positions closed · ${pctText(wr)} win rate · ${latest.fills} fills`,
  };
}

function paint(main) {
  const s = state;
  const h = heroModel(s);
  const sessionsAsc = [...s.digest].reverse();
  const vals = sessionsAsc.map(d => pick(d, 'realized_pnl') || 0);
  const sum = n => vals.slice(-n).reduce((a, b) => a + b, 0);
  let cum = 0;
  const cumulative = vals.map(v => (cum += v));
  const sess = session();

  // Attention
  const alerts = [];
  if (status.kite === false) {
    alerts.push(html`<div class="alert bad"><span class="dot bad"></span><div class="grow"><b>Kite is ${status.needsLogin ? 'not logged in' : 'disconnected'}.</b>
      <span class="sub">${sess.open ? 'Strategies can’t trade and prices are frozen until you log in.' : 'Log in before 9:15 so strategies can trade today.'}</span></div>
      <button class="btn primary sm" data-act="kite">Log in to Kite</button></div>`);
  }
  const pausedWithOpen = s.deps.filter(d => d.status === 'paused' && s.open.some(p => p.deployment_id === d.id));
  pausedWithOpen.forEach(d => alerts.push(html`<div class="alert warn"><span class="dot warn"></span><div class="grow"><b>${d.deployment_name}</b> is paused with open positions — nothing is managing them.</div>
    <a class="btn sm" href="#/s/${d.id}">Review</a></div>`));

  // Strategies at a glance
  const activeById = new Map(s.active.map(r => [r.deployment_id, r]));
  const liveDeps = s.deps.filter(d => d.status !== 'stopped').map(d => {
    const a = activeById.get(d.id) || {};
    const opens = s.open.filter(p => p.deployment_id === d.id);
    const openPnl = opens.reduce((x, p) => x + (livePnl(p) || 0), 0);
    let figure, caption;
    if (d.mode === 'positional') {
      if (a.active) { figure = (pick(a, 'realized_pnl') || 0) + openPnl; caption = a.period_label || 'Current cycle'; }
      else { figure = pick(a, 'last_cycle_pnl'); caption = figure == null ? 'No cycles yet' : 'Flat · last cycle'; }
    } else {
      const t = pick(a, 'today_realized_pnl') || 0;
      figure = h.live ? t + openPnl : null;
      caption = h.live ? 'Today' : 'Flat';
    }
    return { d, figure, caption, opens, last: a.last_action_at };
  }).sort((x, y) => (x.d.status === 'active' ? 0 : 1) - (y.d.status === 'active' ? 0 : 1) || Math.abs(y.figure || 0) - Math.abs(x.figure || 0));

  mount(main, html`
    <div class="page-head">
      <div><h1>${h.live ? 'Today' : 'Overview'}</h1><div class="sub">${sess.label}${pnlMode() !== 'gross' ? html` · P&amp;L ${MODE_LABEL[pnlMode()].toLowerCase()}` : ''}</div></div>
      <div class="actions"><a class="btn" href="#/strategies">All strategies</a><a class="btn primary" href="#/deploy">Deploy</a></div>
    </div>
    <div class="stack">
      <section class="card hero" aria-label="Headline P&amp;L">
        <div>
          <div class="eyebrow">${h.live ? html`<span class="dot ${sess.open ? 'live' : 'ok'}"></span>` : ''}${h.label}</div>
          <div class="big ${tone(h.value)}" id="heroValue">${h.value == null ? '—' : money(h.value, { sign: true })}</div>
          <div class="split">
            ${h.parts.map(([k, v]) => html`<span>${k} <b class="${tone(v)}" data-hero-part="${k}">${money(v, { sign: true })}</b></span>`)}
            <span>${h.note}</span>
          </div>
        </div>
        <div class="hero-side">
          <div class="split" style="justify-content:flex-end">
            <span>Last 5 sessions <b class="${tone(sum(5))}">${money(sum(5), { sign: true, dp: 0 })}</b></span>
            <span>Last ${vals.length} <b class="${tone(sum(vals.length))}">${money(sum(vals.length), { sign: true, dp: 0 })}</b></span>
          </div>
          ${raw(sparkline(cumulative, { w: 220, h: 44 }))}
        </div>
      </section>

      ${alerts.length ? html`<div class="attention">${alerts}</div>` : ''}

      <div class="grid two">
        <section class="card" aria-label="Strategies">
          <div class="card-head"><div><h2>Strategies</h2><div class="sub">${s.deps.filter(d => d.status === 'active').length} active · ${s.deps.filter(d => d.status === 'paused').length} paused</div></div><a class="btn ghost sm" href="#/strategies">View all</a></div>
          <div class="rows">
            ${liveDeps.length ? liveDeps.slice(0, 9).map(r => html`<a class="row-item" href="#/s/${r.d.id}">
              <div><div class="cell-title">${r.d.deployment_name}</div>
                <div class="cell-sub">${statusBadge(r.d.status)} &nbsp;${r.d.strategy_name} · ${r.d.mode}${r.opens.length ? ` · ${r.opens.length} open` : ''}</div></div>
              <div class="right"><div class="${tone(r.figure)}" style="font-weight:650" data-dep-figure="${r.d.id}">${r.figure == null ? '—' : money(r.figure, { sign: true })}</div>
                <div class="cell-sub">${r.caption}</div></div></a>`)
              : empty('No strategies yet', html`<a class="btn primary sm" style="margin-top:10px" href="#/deploy">Deploy your first</a>`)}
          </div>
        </section>

        <section class="card" aria-label="Recent activity">
          <div class="card-head"><div><h2>Activity</h2><div class="sub">Fills grouped into actions</div></div></div>
          <div class="rows">${groupFills(s.recent).slice(0, 8).map(g => html`<a class="feed-item" href="#/s/${g.deployment_id}/activity">
            <div class="feed-time">${when(g.t)}</div>
            <div><div class="feed-title">${g.deployment_name}</div>
              <div class="feed-detail">${g.title}${g.reasons.length ? ` · ${g.reasons.join(', ')}` : ''}</div></div>
            <div>${reasonBadge(g.reasons[0])}</div></a>`)}
            ${s.recent.length ? '' : empty('No fills yet', '')}</div>
        </section>
      </div>

      <section class="card" aria-label="Open positions">
        <div class="card-head"><div><h2>Open positions</h2><div class="sub">${s.open.length ? 'Marked to the latest tick' : 'Every open leg across all strategies appears here'}</div></div></div>
        ${s.open.length ? html`<div class="table-wrap"><table class="t">
          <thead><tr><th class="hide-mobile">Strategy</th><th>Contract</th><th class="hide-mobile">Side</th><th class="num hide-mobile">Qty</th><th class="num hide-mobile">Entry</th><th class="num">LTP</th><th class="num">P&amp;L</th></tr></thead>
          <tbody>${s.open.map(p => html`<tr class="link" data-href="#/s/${p.deployment_id}">
            <td class="hide-mobile">${p.deployment_name}</td><td class="mono small">${p.symbol}<div class="cell-sub only-mobile">${p.deployment_name} · ${p.side} ${num(p.qty, 0)} @ ${num(p.avg_entry_price)}</div></td><td class="hide-mobile">${p.side}</td>
            <td class="num hide-mobile">${num(p.qty, 0)}</td><td class="num hide-mobile">${num(p.avg_entry_price)}</td>
            <td class="num" data-ltp="${p.id}">${num(livePrice(p))}</td>
            <td class="num" data-pnl="${p.id}">${pnlText(livePnl(p))}</td></tr>`)}</tbody>
          <tfoot><tr><td colspan="6" class="hide-mobile">Total</td><td class="only-mobile">Total</td><td class="only-mobile"></td><td class="num" id="openTotal">${pnlText(s.open.reduce((a, p) => a + (livePnl(p) || 0), 0))}</td></tr></tfoot>
        </table></div>` : empty('Nothing open right now', sess.open ? 'Positions appear here the moment a strategy enters.' : 'The market is closed.')}
      </section>

      <section class="card" aria-label="Recent sessions">
        <div class="card-head"><div><h2>Last ${sessionsAsc.length} sessions</h2><div class="sub">Realized P&amp;L per trading day, all strategies in reports</div></div>
          <a class="btn ghost sm" href="#/performance">Performance</a></div>
        <div class="card-body"><div class="chart" id="sessionBars"></div></div>
      </section>
    </div>`);

  const stopBars = bars($('#sessionBars', main), sessionsAsc.map(d => ({
    t: d.period_start, value: pick(d, 'realized_pnl') || 0,
    detail: `<div class="tip-row"><span>Closed</span><b>${d.positions_closed}</b></div><div class="tip-row"><span>Win rate</span><b>${pctText(winRate(d.wins, d.losses))}</b></div>`,
  })), { height: 190 });
  return stopBars;
}

function updateLive(main) {
  if (!state) return;
  let total = 0;
  for (const p of state.open) {
    const v = livePnl(p);
    total += v || 0;
    const cell = main.querySelector(`[data-pnl="${p.id}"]`);
    if (cell) cell.innerHTML = String(pnlText(v));
    const ltp = main.querySelector(`[data-ltp="${p.id}"]`);
    if (ltp) ltp.textContent = num(livePrice(p));
  }
  for (const d of state.deps) {
    const cell = main.querySelector(`[data-dep-figure="${d.id}"]`);
    const opens = state.open.filter(p => p.deployment_id === d.id);
    if (!cell || !opens.length) continue;
    const a = state.active.find(r => r.deployment_id === d.id) || {};
    const base = d.mode === 'positional' ? (pick(a, 'realized_pnl') || 0) : (pick(a, 'today_realized_pnl') || 0);
    const v = base + opens.reduce((x, p) => x + (livePnl(p) || 0), 0);
    cell.textContent = money(v, { sign: true });
    cell.className = tone(v);
    cell.style.fontWeight = '650';
  }
  const ot = main.querySelector('#openTotal');
  if (ot) ot.innerHTML = String(pnlText(total));
  const h = heroModel(state);
  const hv = main.querySelector('#heroValue');
  if (hv && h.live) { hv.textContent = money(h.value, { sign: true }); hv.className = `big ${tone(h.value)}`; }
  const op = main.querySelector('[data-hero-part="Open"]');
  if (op) { op.textContent = money(total, { sign: true }); op.className = tone(total); }
}

export default {
  title: 'Today',
  async render(main) {
    if (!state) mount(main, skeleton(3));
    state = await load();
    let stopChart = paint(main);
    const onTicks = () => updateLive(main);
    const onStatus = () => { stopChart(); stopChart = paint(main); };
    bus.addEventListener('ticks', onTicks);
    bus.addEventListener('status', onStatus);
    const onClick = e => {
      const row = e.target.closest('tr[data-href]');
      if (row) location.hash = row.dataset.href;
      if (e.target.closest('[data-act="kite"]')) loginWithKite().catch(err => toastError(err, 'Kite login failed'));
    };
    main.addEventListener('click', onClick);
    this._repaint = async () => {
      try { state = await load(); stopChart(); stopChart = paint(main); } catch (e) { /* keep last good render */ }
    };
    return () => {
      stopChart();
      bus.removeEventListener('ticks', onTicks);
      bus.removeEventListener('status', onStatus);
      main.removeEventListener('click', onClick);
      this._repaint = null;
    };
  },
  onChanged() { if (this._repaint) this._repaint(); },
};
