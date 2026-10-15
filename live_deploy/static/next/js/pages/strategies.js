// Strategies — every deployment, filterable and sortable, live P&L.
import { html, raw, mount, $, money, pct, tone, ago, ICONS, on } from '../util.js';
import { api, pick } from '../api.js';
import { bus, livePnl } from '../live.js';
import { menu } from '../ui.js';
import { statusBadge, pnlText, empty, skeleton } from '../components.js';
import { actionItems, flattenAll } from '../actions.js';
import { rememberDeployments } from '../app.js';

const ui = { status: 'all', q: '', strategy: '', sort: 'status', dir: 1 };
let data = null;

async function load() {
  const [deps, open, active] = await Promise.all([api.deployments(), api.openPositions(), api.activePeriods()]);
  rememberDeployments(deps);
  return { deps, open, active };
}

function rows() {
  const activeById = new Map(data.active.map(a => [a.deployment_id, a]));
  return data.deps.map(d => {
    const opens = data.open.filter(p => p.deployment_id === d.id);
    const unreal = opens.length ? opens.reduce((a, p) => a + (livePnl(p) ?? 0), 0) : (pick(d, 'unrealized_pnl') || 0);
    const realized = pick(d, 'realized_pnl') || 0;
    const total = realized + unreal;
    const a = activeById.get(d.id) || {};
    return {
      d, opens, realized, unreal, total,
      ret: d.initial_capital ? (total / d.initial_capital) * 100 : null,
      today: d.mode === 'positional' ? (a.active ? (pick(a, 'realized_pnl') || 0) + unreal : null) : (pick(a, 'today_realized_pnl') || 0) + unreal,
      last: a.last_action_at || d.updated_at,
    };
  });
}

const ORDER = { active: 0, paused: 1, stopped: 2 };
const SORTS = {
  status: (a, b) => ORDER[a.d.status] - ORDER[b.d.status] || a.d.deployment_name.localeCompare(b.d.deployment_name),
  name: (a, b) => a.d.deployment_name.localeCompare(b.d.deployment_name),
  total: (a, b) => a.total - b.total,
  ret: (a, b) => (a.ret ?? -1e9) - (b.ret ?? -1e9),
  today: (a, b) => (a.today ?? -1e9) - (b.today ?? -1e9),
  last: (a, b) => new Date(a.last || 0) - new Date(b.last || 0),
};

function filtered() {
  const q = ui.q.trim().toLowerCase();
  return rows()
    .filter(r => ui.status === 'all' || r.d.status === ui.status)
    .filter(r => !ui.strategy || r.d.strategy_name === ui.strategy)
    .filter(r => !q || `${r.d.deployment_name} ${r.d.strategy_name} ${r.d.notes || ''} ${(r.d.tags || []).map(t => t.name || t).join(' ')}`.toLowerCase().includes(q))
    .sort((a, b) => SORTS[ui.sort](a, b) * ui.dir);
}

function paint(main) {
  const all = data.deps;
  const counts = { all: all.length, active: 0, paused: 0, stopped: 0 };
  all.forEach(d => { counts[d.status] = (counts[d.status] || 0) + 1; });
  const list = filtered();
  const totals = list.reduce((a, r) => ({ total: a.total + r.total, today: a.today + (r.today || 0), open: a.open + r.opens.length, cap: a.cap + (r.d.initial_capital || 0) }), { total: 0, today: 0, open: 0, cap: 0 });
  const strategies = [...new Set(all.map(d => d.strategy_name))].sort();
  const th = (key, label, cls = '') => html`<th class="sortable ${cls}" data-sort="${key}" aria-sort="${ui.sort === key ? (ui.dir > 0 ? 'ascending' : 'descending') : 'none'}">${label}${ui.sort === key ? (ui.dir > 0 ? ' ↑' : ' ↓') : ''}</th>`;

  mount(main, html`
    <div class="page-head">
      <div><h1>Strategies</h1><div class="sub">${counts.active} active · ${counts.paused} paused · ${counts.stopped} stopped</div></div>
      <div class="actions">
        ${data.open.length ? html`<button class="btn danger" data-act="flatten-all">Close all positions</button>` : ''}
        <button class="btn" data-act="csv">Export CSV</button>
        <a class="btn primary" href="#/deploy">Deploy</a>
      </div>
    </div>
    <div class="actions" style="margin-bottom:14px">
      <div class="chips" role="group" aria-label="Status">${['all', 'active', 'paused', 'stopped'].map(s => html`<button class="chip ${ui.status === s ? 'on' : ''}" data-status="${s}">${s[0].toUpperCase() + s.slice(1)} <span class="count">${counts[s] || 0}</span></button>`)}</div>
      <div class="topbar-spacer"></div>
      <select class="input" style="width:auto" data-act="strategy" aria-label="Strategy">
        <option value="">All strategy types</option>${strategies.map(s => html`<option value="${s}" ${ui.strategy === s ? 'selected' : ''}>${s}</option>`)}
      </select>
      <label class="search" style="width:240px"><span class="sr-only">Search</span>${raw(ICONS.search)}<input class="input" data-act="search" placeholder="Search name, type, notes, tags" value="${ui.q}"></label>
    </div>
    ${list.length ? html`
    <div class="card hide-mobile"><div class="table-wrap"><table class="t">
      <thead><tr>${th('name', 'Strategy')}${th('status', 'Status')}<th>Mode</th><th class="num">Capital</th>${th('today', 'Today / cycle', 'num')}${th('total', 'Total P&L', 'num')}${th('ret', 'Return', 'num')}<th class="num">Open</th>${th('last', 'Last action', 'num')}<th></th></tr></thead>
      <tbody>${list.map(r => html`<tr class="link" data-id="${r.d.id}">
        <td><div class="cell-title">${r.d.deployment_name}</div><div class="cell-sub">${r.d.strategy_name}${(r.d.tags || []).length ? ` · ${(r.d.tags || []).map(t => t.name || t).join(', ')}` : ''}</div></td>
        <td>${statusBadge(r.d.status)}</td><td class="sub">${r.d.mode}</td>
        <td class="num sub">${money(r.d.initial_capital, { dp: 0 })}</td>
        <td class="num" data-today="${r.d.id}">${r.today == null ? html`<span class="muted">Flat</span>` : pnlText(r.today)}</td>
        <td class="num" data-total="${r.d.id}" style="font-weight:600">${pnlText(r.total)}</td>
        <td class="num ${tone(r.ret)}">${pct(r.ret, { sign: true })}</td>
        <td class="num">${r.opens.length || html`<span class="muted">0</span>`}</td>
        <td class="num sub">${ago(r.last)}</td>
        <td class="num"><button class="btn ghost sm icon" data-menu="${r.d.id}" aria-label="Actions for ${r.d.deployment_name}">${raw(ICONS.more)}</button></td></tr>`)}</tbody>
      <tfoot><tr><td colspan="3">Total (${list.length} shown)</td><td class="num">${money(totals.cap, { dp: 0 })}</td><td class="num">${pnlText(totals.today)}</td><td class="num">${pnlText(totals.total)}</td><td class="num ${tone(totals.total)}">${pct(totals.cap ? totals.total / totals.cap * 100 : null, { sign: true })}</td><td class="num">${totals.open}</td><td colspan="2"></td></tr></tfoot>
    </table></div></div>
    <div class="cards only-mobile">${list.map(r => html`<a class="scard" href="#/s/${r.d.id}">
      <div class="row"><div class="cell-title">${r.d.deployment_name}</div>${statusBadge(r.d.status)}</div>
      <div class="row"><span class="sub small">${r.d.strategy_name} · ${r.d.mode}</span><span class="small muted">${ago(r.last)}</span></div>
      <div class="row"><div><div class="label">Total P&amp;L</div><div style="font-weight:650;font-size:17px">${pnlText(r.total)}</div></div>
        <div style="text-align:right"><div class="label">${r.d.mode === 'positional' ? 'Cycle' : 'Today'}</div><div>${r.today == null ? html`<span class="muted">Flat</span>` : pnlText(r.today)}</div></div></div></a>`)}</div>`
    : html`<div class="card">${empty(all.length ? 'Nothing matches these filters' : 'No strategies deployed yet', all.length ? 'Try a different status or search.' : html`<a class="btn primary sm" style="margin-top:10px" href="#/deploy">Deploy a strategy</a>`)}</div>`}`);
}

function exportCsv() {
  const head = ['Name', 'Strategy', 'Status', 'Mode', 'Capital', 'Realized', 'Unrealized', 'Total', 'Return %', 'Open'];
  const lines = filtered().map(r => [r.d.deployment_name, r.d.strategy_name, r.d.status, r.d.mode, r.d.initial_capital, r.realized.toFixed(2), r.unreal.toFixed(2), r.total.toFixed(2), r.ret == null ? '' : r.ret.toFixed(2), r.opens.length]);
  const csv = [head, ...lines].map(l => l.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = 'strategies.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

export default {
  title: 'Strategies',
  async render(main) {
    if (!data) mount(main, skeleton(4));
    data = await load();
    paint(main);
    const refresh = async () => { data = await load(); paint(main); };
    this._refresh = refresh;
    const offs = [
      on(main, 'click', '[data-status]', (e, b) => { ui.status = b.dataset.status; paint(main); }),
      on(main, 'click', 'th[data-sort]', (e, t) => { const k = t.dataset.sort; ui.dir = ui.sort === k ? -ui.dir : (k === 'name' || k === 'status' ? 1 : -1); ui.sort = k; paint(main); }),
      on(main, 'change', '[data-act="strategy"]', (e, s) => { ui.strategy = s.value; paint(main); }),
      on(main, 'input', '[data-act="search"]', (e, i) => {
        ui.q = i.value;
        const pos = i.selectionStart;
        paint(main);
        const ni = $('[data-act="search"]', main);
        ni.focus(); ni.setSelectionRange(pos, pos);
      }),
      on(main, 'click', '[data-act="csv"]', exportCsv),
      on(main, 'click', '[data-act="flatten-all"]', async () => { if (await flattenAll(data.open.length)) refresh(); }),
      on(main, 'click', '[data-menu]', (e, b) => {
        e.stopPropagation();
        const d = data.deps.find(x => x.id === b.dataset.menu);
        menu(b, actionItems(d, { hasOpen: data.open.some(p => p.deployment_id === d.id), refresh }));
      }),
      on(main, 'click', 'tr[data-id]', (e, tr) => { if (!e.target.closest('button')) location.hash = `#/s/${tr.dataset.id}`; }),
    ];
    const onTicks = () => {
      for (const r of rows()) {
        const t = main.querySelector(`[data-total="${r.d.id}"]`);
        if (t && r.opens.length) t.innerHTML = String(pnlText(r.total));
        const td = main.querySelector(`[data-today="${r.d.id}"]`);
        if (td && r.opens.length && r.today != null) td.innerHTML = String(pnlText(r.today));
      }
    };
    bus.addEventListener('ticks', onTicks);
    return () => { offs.forEach(f => f()); bus.removeEventListener('ticks', onTicks); this._refresh = null; };
  },
  onChanged() { if (this._refresh) this._refresh().catch(() => {}); },
};
