// LinckLens › Exiter: the shares and funds you mean to sell. Tick them
// in the list of everything you hold (find them by name, kind, gain or
// loss, long or short term); each is picked whole, and its quantity can be
// edited. What it all adds up to: put in, worth now, the gain, and, from
// the tradebook, how much of that gain is long term and how much short
// (oldest lots first, the order the exchange sells them).
//
// The picks are kept on the server (/v2/lens/exiter, data/lens.js), so
// no filter, search, refresh or other device loses them. A pick whose
// holding has gone stays listed, marked, until you remove it.

import { html, download } from '../../core/dom.js';
import { inr, price, num, plural, tone } from '../../core/format.js';
import { stat, sect, chips, empty } from '../../ui/components.js';
import { tradesOf } from '../../data/lens.js';
import { pctOr, inr0, signed, ASSET_SHORT } from './parts.js';

const FUND_KIND = { mf_equity: 'Equity fund', mf_elss: 'Tax-saver (ELSS)', mf_index: 'Index fund', mf_hybrid: 'Hybrid fund', mf_debt: 'Debt fund',
  mf_liquid: 'Liquid fund', mf_fof: 'Fund of funds', mf_other: 'Other fund' };
const kindLabel = r => (r.kind === 'fund' ? FUND_KIND[r.asset] || 'Fund' : ASSET_SHORT[r.asset] || 'Share');

export const SORTS = [['value', 'Worth now'], ['invested', 'Put in'], ['pnl', 'Gain ₹'], ['pnl_pct', 'Gain %'], ['weight', 'Weight'], ['name', 'Name']];
const KEY = { value: r => r.value, invested: r => r.invested, pnl: r => r.pnl, pnl_pct: r => r.pnl_pct, weight: r => r.weight, name: r => r.symbol };

// Everything held, shares and funds, in one shape.
export function holdings(h) {
  const eq = (h.equity || []).map(r => ({
    key: `eq:${r.isin || r.symbol}`, kind: 'share', symbol: r.symbol, name: r.name || r.exchange || '', isin: r.isin || '', asset: r.asset,
    held: r.qty, dp: 0, avg: r.avg, price: r.ltp, invested: r.invested, value: r.value, pnl: r.pnl, pnl_pct: r.pnl_pct, weight: r.weight || 0 }));
  const mf = (h.mf || []).map(f => ({
    key: `mf:${f.isin}:${f.folio || ''}`, kind: 'fund', symbol: f.fund, name: [f.amc, f.folio ? `folio ${f.folio}` : ''].filter(Boolean).join(' · '),
    isin: f.isin || '', asset: f.asset, plan: f.plan, held: f.units, dp: 3, avg: f.avg, price: f.nav, invested: f.invested, value: f.value,
    pnl: f.pnl, pnl_pct: f.pnl_pct, weight: f.weight || 0 }));
  const all = [...eq, ...mf];
  const n = {};
  for (const r of all) if (r.isin) n[r.isin] = (n[r.isin] || 0) + 1;
  for (const r of all) r.lone = !!r.isin && n[r.isin] === 1;   // one row for its ISIN: the tradebook's lots are this row's
  return all;
}

// The tradebook's lots for a holding, when they add up to what Kite holds.
function lotsOf(r) {
  const p = r.lone ? tradesOf(r.isin) : null;
  if (!p || !p.lots || !p.lots.length) return null;
  return Math.abs(p.open_qty - r.held) <= 0.001 * Math.max(1, r.held) ? p : null;
}

// Selling q of a holding, oldest lots first: how much is long term and
// short (debt funds' slab rate counts with short), and the gain on each at
// today's price. null without a tradebook that matches the holding.
export function terms(r, q) {
  const p = lotsOf(r);
  if (!p) return null;
  const out = { ltQty: 0, ltGain: 0, stQty: 0, stGain: 0, slab: false };
  let left = q;
  for (const l of [...p.lots].sort((a, b) => a.day.localeCompare(b.day))) {
    if (left <= 1e-9) break;
    const take = Math.min(left, l.qty), gain = (r.price - l.price) * take;
    if (l.term === 'long') { out.ltQty += take; out.ltGain += gain; } else { out.stQty += take; out.stGain += gain; if (l.term === 'slab') out.slab = true; }
    left -= take;
  }
  return out;
}

// One pick against its holding: how much is sold, and what it comes to.
export function exitOf(pick, r) {
  if (!r) return { pick, row: null };
  const q = Math.min(pick.qty == null ? r.held : pick.qty, r.held);
  const invested = r.avg * q, value = r.price * q;
  return { pick, row: r, q, all: pick.qty == null || pick.qty >= r.held, over: pick.qty != null && pick.qty > r.held + 1e-9,
    invested, value, pnl: value - invested, pnl_pct: invested ? (value / invested - 1) * 100 : null, t: terms(r, q) };
}

export function shown(rows, picked, ui) {
  const q = (ui.xq || '').trim().toUpperCase();
  const kind = ui.xkind || 'all', pnl = ui.xpnl || 'any', term = ui.xterm || 'any', show = ui.xshow || 'all';
  let out = rows.filter(r => {
    if (q && !`${r.symbol} ${r.name} ${r.isin}`.toUpperCase().includes(q)) return false;
    if (kind === 'share' || kind === 'fund' ? r.kind !== kind : kind !== 'all' && r.asset !== kind) return false;
    if (pnl === 'gain' && !(r.pnl > 0)) return false;
    if (pnl === 'loss' && !(r.pnl < 0)) return false;
    if (term !== 'any') {
      const p = lotsOf(r), lt = p ? p.long_qty : null;
      if (lt == null) return false;
      if (term === 'long' && !(lt > 0)) return false;
      if (term === 'short' && lt > 0) return false;
    }
    if (show === 'picked' && !picked.has(r.key)) return false;
    if (show === 'not' && picked.has(r.key)) return false;
    return true;
  });
  const k = KEY[ui.xsort] || KEY.value;
  out = [...out].sort((a, b) => {
    const x = k(a), y = k(b);
    if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1;
    const c = typeof x === 'string' ? x.localeCompare(y) : y - x;
    return ui.xdir === 'asc' ? -c : c;
  });
  return out;
}

// A typed quantity, read for a holding: { qty } (null: all of it), or { err }.
export function readQty(text, r) {
  const s = String(text ?? '').replace(/,/g, '').trim();
  if (!s || /^all$/i.test(s)) return { qty: null };
  const v = Number(s);
  if (!Number.isFinite(v) || v <= 0) return { err: 'Type a quantity above zero, or leave it empty for all of it.' };
  if (r.kind === 'share' && !Number.isInteger(v)) return { err: 'Shares sell whole: type a whole number.' };
  const q = r.kind === 'fund' ? Math.round(v * 1000) / 1000 : v;
  if (q >= r.held - 1e-9) return { qty: null, note: q > r.held + 1e-9 ? `You hold ${num(r.held, r.dp)}: set to all of it.` : '' };
  return { qty: q };
}

export const pickOf = r => ({ key: r.key, kind: r.kind, symbol: r.symbol, name: r.name, isin: r.isin, qty: null, added: new Date().toISOString() });

export function csv(exits) {
  const cell = v => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const head = ['Kind', 'Symbol or fund', 'ISIN', 'Detail', 'Held', 'Sell', 'Avg cost', 'Price', 'Put in', 'Worth now', 'Gain', 'Long-term qty', 'Long-term gain', 'Short-term qty', 'Short-term gain'];
  const rows = exits.filter(e => e.row).map(e => {
    const r = e.row, dp = r.dp, rd = v => (v == null ? '' : v.toFixed(2));
    return [r.kind === 'fund' ? 'Fund' : 'Share', r.symbol, r.isin, r.name, r.held.toFixed(dp), e.q.toFixed(dp), rd(r.avg), rd(r.price), rd(e.invested), rd(e.value), rd(e.pnl),
      e.t ? e.t.ltQty.toFixed(dp) : '', e.t ? rd(e.t.ltGain) : '', e.t ? e.t.stQty.toFixed(dp) : '', e.t ? rd(e.t.stGain) : ''];
  });
  return [head, ...rows].map(r => r.map(cell).join(',')).join('\n') + '\n';
}

function saveState(st) {
  if (st.xErr && st.x) return html`<span class="xt-save bad" role="alert" data-save="error">Not saved: ${st.xErr.message} <button type="button" class="btn btn-sm" data-act="xretry">Save again</button></span>`;
  if (st.xSaving) return html`<span class="xt-save" data-save="saving"><i class="spin" aria-hidden="true"></i> Saving…</span>`;
  return html`<span class="xt-save tiny mute" data-save="saved">Saved: kept through refreshes and filters</span>`;
}

const termCell = (q, g, dp, l) => html`<span class="num ${tone(g)}" data-l="${l}">${q ? inr(g) : '—'}<small>${q ? `${num(q, dp)} ${dp ? 'units' : 'qty'}` : ''}</small></span>`;

function exitList(st, ui, exits) {
  const live = exits.filter(e => e.row), gone = exits.filter(e => !e.row);
  const tot = live.reduce((a, e) => ({ invested: a.invested + e.invested, value: a.value + e.value, pnl: a.pnl + e.pnl }), { invested: 0, value: 0, pnl: 0 });
  const withT = live.filter(e => e.t), lt = withT.reduce((a, e) => a + e.t.ltGain, 0), stg = withT.reduce((a, e) => a + e.t.stGain, 0);
  const draft = ui.xdraft || {};
  return html`${sect('Your exit list', exits.length ? plural(exits.length, 'pick') : 'nothing picked yet',
      exits.length ? html`<span class="row" style="gap:8px">${saveState(st)}
        <button type="button" class="btn btn-sm" data-act="xcsv" ${live.length ? '' : 'disabled'}>Download CSV</button>
        <button type="button" class="btn btn-sm" data-act="xclear">Clear list</button></span>` : saveState(st))}
    ${!exits.length ? html`<p class="small mute xt-empty" data-exit="empty">Tick holdings in the list below to pick them. Each is picked whole; change the quantity here after.</p>` : html`
    <div class="tbl xl-tbl" role="table" data-exit="list">
      <div class="tr th xl-row" role="row"><span>Holding</span><span class="num">Held</span><span class="num">Sell</span><span class="num">Avg cost</span><span class="num">Price</span>
        <span class="num">Put in</span><span class="num">Worth now</span><span class="num">Gain</span><span class="num">Long term</span><span class="num">Short term</span><span></span></div>
      ${live.map(e => {
        const r = e.row, k = r.key, v = draft[k] != null ? draft[k] : num(e.q, r.dp).replace(/,/g, '');
        return html`<div class="tr xl-row" role="row" data-pick="${k}">
          <span class="cell-name"><b>${r.symbol} <span class="tag">${kindLabel(r)}</span></b><span>${r.name}</span></span>
          <span class="num" data-l="Held">${num(r.held, r.dp)}</span>
          <span class="num xl-q" data-l="Sell"><input class="input" type="text" inputmode="decimal" value="${v}" data-f="xq:${k}" data-k="${k}" aria-label="Quantity of ${r.symbol} to sell">
            ${e.all ? html`<small data-all>all</small>` : html`<button type="button" class="linkish xl-all" data-act="xall" data-k="${k}">sell all</button>`}</span>
          <span class="num" data-l="Avg cost">${price(r.avg)}</span>
          <span class="num" data-l="Price">${price(r.price)}</span>
          <span class="num" data-l="Put in">${inr0(e.invested)}</span>
          <span class="num" data-l="Worth now">${inr0(e.value)}</span>
          <span class="num ${tone(e.pnl)}" data-l="Gain">${inr(e.pnl)}<small>${pctOr(e.pnl_pct)}</small></span>
          ${e.t ? termCell(e.t.ltQty, e.t.ltGain, r.dp, 'Long term') : html`<span class="num mute" data-l="Long term" data-tip="${r.lone ? 'Your tradebook doesn’t add up to what Kite holds of it (or isn’t uploaded): import it on the Trades tab' : 'Held in more than one folio: the tradebook can’t tell which lots are which'}">—</span>`}
          ${e.t ? termCell(e.t.stQty, e.t.stGain, r.dp, e.t.slab ? 'Short term / slab' : 'Short term') : html`<span class="num mute" data-l="Short term">—</span>`}
          <span class="xl-x"><button type="button" class="btn btn-sm" data-act="xdrop" data-k="${k}" aria-label="Remove ${r.symbol} from the list">Remove</button></span>
        </div>`;
      })}
      ${gone.map(e => html`<div class="tr xl-row gone" role="row" data-pick="${e.pick.key}" data-gone>
          <span class="cell-name"><b>${e.pick.symbol}</b><span>No longer in your account: sold, or not read from Kite this time</span></span>
          <span></span><span class="num" data-l="Sell">${e.pick.qty == null ? 'all' : num(e.pick.qty, e.pick.kind === 'fund' ? 3 : 0)}</span><span></span><span></span><span></span><span></span><span></span><span></span><span></span>
          <span class="xl-x"><button type="button" class="btn btn-sm" data-act="xdrop" data-k="${e.pick.key}" aria-label="Remove ${e.pick.symbol} from the list">Remove</button></span></div>`)}
      ${live.length ? html`<div class="tr tot xl-row" role="row"><span>${plural(live.length, 'holding')} to sell</span><span></span><span></span><span></span><span></span>
        <span class="num" data-l="Put in">${inr0(tot.invested)}</span><span class="num" data-l="Worth now">${inr0(tot.value)}</span>
        <span class="num ${tone(tot.pnl)}" data-l="Gain">${inr(tot.pnl)}</span>
        <span class="num ${tone(lt)}" data-l="Long term">${withT.length ? inr(lt) : ''}</span><span class="num ${tone(stg)}" data-l="Short term">${withT.length ? inr(stg) : ''}</span><span></span></div>` : ''}
    </div>
    <p class="tiny mute lens-note">Put in and gain are against Kite’s average cost; worth now at today’s price (a fund at its last NAV). Long and short term are an estimate from your tradebook: what you sell goes oldest lots first, and each lot’s gain is at today’s price against what you paid for it.${withT.length < live.length ? ' A dash: no tradebook that adds up to what Kite holds of it.' : ''} Nothing here places an order.</p>`}`;
}

function holdingsTable(st, ui, rows, picked) {
  const list = shown(rows, picked, ui);
  const assets = [...new Set(rows.map(r => r.asset))];
  const kinds = [['all', 'All'], ['share', 'All shares'], ['fund', 'All funds'],
    ...assets.map(a => [a, rows.find(r => r.asset === a).kind === 'fund' ? FUND_KIND[a] || 'Fund' : ASSET_SHORT[a] || a])];
  const hasT = !!(st.t && st.t.imports && st.t.imports.length);
  const allPicked = list.length && list.every(r => picked.has(r.key));
  const tot = list.reduce((a, r) => ({ invested: a.invested + r.invested, value: a.value + r.value, pnl: a.pnl + r.pnl }), { invested: 0, value: 0, pnl: 0 });
  return html`${sect('Everything you hold', `${plural(rows.length, 'holding')} · tick what to sell`)}
    <div class="lens-tools xt-tools">
      <input class="input search" type="search" placeholder="Find by name, symbol or ISIN" value="${ui.xq || ''}" data-f="xsearch" aria-label="Find a holding">
      <label class="row small" style="gap:6px">Kind <select class="input" data-f="xkind" style="height:34px">${kinds.map(([k, l]) => html`<option value="${k}" ${k === (ui.xkind || 'all') ? 'selected' : ''}>${l}</option>`)}</select></label>
      <label class="row small" style="gap:6px">Sort <select class="input" data-f="xsort" style="height:34px">${SORTS.map(([k, l]) => html`<option value="${k}" ${k === (ui.xsort || 'value') ? 'selected' : ''}>${l}</option>`)}</select></label>
      <button type="button" class="btn btn-sm" data-act="xdir" aria-label="Reverse the order">${ui.xdir === 'asc' ? '↑' : '↓'}</button>
    </div>
    <div class="lens-tools xt-tools">
      <div class="row wrap" style="gap:6px" data-filter="pnl">${chips([['any', 'Gain or loss'], ['gain', 'In gain'], ['loss', 'In loss']], ui.xpnl || 'any', 'xpnl')}</div>
      ${hasT ? html`<div class="row wrap" style="gap:6px" data-filter="term">${chips([['any', 'Any term'], ['long', 'Has long term'], ['short', 'All short term']], ui.xterm || 'any', 'xterm')}</div>` : ''}
      <div class="row wrap" style="gap:6px" data-filter="show">${chips([['all', 'All'], ['picked', `Picked ${picked.size}`], ['not', 'Not picked']], ui.xshow || 'all', 'xshow')}</div>
      <span class="grow"></span>
      ${list.length ? html`<button type="button" class="btn btn-sm" data-act="${allPicked ? 'xunpickShown' : 'xpickShown'}">${allPicked ? `Unpick these ${list.length}` : `Pick all ${list.length} shown`}</button>` : ''}
    </div>
    <div class="tbl xh-tbl" role="table" data-exit="holdings">
      <div class="tr th xh-row" role="row"><span></span><span>Holding</span><span>Kind</span><span class="num">Held</span><span class="num">Avg cost</span><span class="num">Price</span>
        <span class="num">Put in</span><span class="num">Worth now</span><span class="num">Gain</span><span class="num">Long term</span><span class="num">Weight</span></div>
      ${list.map(r => {
        const on = picked.has(r.key), p = lotsOf(r);
        return html`<div class="tr xh-row click ${on ? 'sel' : ''}" role="row" data-key="${r.key}" data-act="xpick" data-k="${r.key}" aria-selected="${on}">
          <span class="xh-c"><input type="checkbox" ${on ? 'checked' : ''} aria-label="Pick ${r.symbol}" tabindex="-1"></span>
          <span class="cell-name"><b>${r.symbol}</b><span>${r.name}</span></span>
          <span data-l="Kind"><span class="tag ${r.plan === 'regular' ? 'warn' : ''}">${kindLabel(r)}</span></span>
          <span class="num" data-l="Held">${num(r.held, r.dp)}</span>
          <span class="num" data-l="Avg cost">${price(r.avg)}</span>
          <span class="num" data-l="Price">${price(r.price)}</span>
          <span class="num" data-l="Put in">${inr0(r.invested)}</span>
          <span class="num" data-l="Worth now">${inr0(r.value)}</span>
          <span class="num ${tone(r.pnl)}" data-l="Gain">${inr(r.pnl)}<small>${pctOr(r.pnl_pct)}</small></span>
          <span class="num" data-l="Long term">${p ? num(Math.min(p.long_qty, r.held), r.dp) : '—'}</span>
          <span class="num" data-l="Weight">${r.weight.toFixed(1)}%</span>
        </div>`;
      })}
      ${list.length ? html`<div class="tr tot xh-row" role="row"><span></span><span>${list.length === rows.length ? plural(rows.length, 'holding') : `${list.length} of ${rows.length} shown`}</span><span></span><span></span><span></span><span></span>
        <span class="num" data-l="Put in">${inr0(tot.invested)}</span><span class="num" data-l="Worth now">${inr0(tot.value)}</span>
        <span class="num ${tone(tot.pnl)}" data-l="Gain">${inr(tot.pnl)}</span><span></span><span></span></div>`
        : html`<p class="small mute" style="margin:10px 0" data-exit="nomatch">Nothing matches these filters.${picked.size ? ' Your picks are still in the list above.' : ''}</p>`}
    </div>
    <p class="tiny mute lens-note">Click a row, or its box, to pick it or put it back. Filters only change what’s shown here: what you’ve picked stays picked.${hasT ? ' Long term: how much you’ve held over a year, from your tradebook.' : ' Import your tradebook on the Trades tab to see how much of each is long term.'}</p>`;
}

export function render(st, ui) {
  if (st.x == null) {
    return st.xErr
      ? html`<div data-exit="loaderr">${empty('Couldn’t read your exit list', `${st.xErr.message}. Nothing can be picked until it’s read, so nothing saved is overwritten.`,
        html`<button type="button" class="btn btn-sm" data-act="xreload">Try again</button>`)}</div>`
      : html`<p class="small mute"><i class="spin" aria-hidden="true"></i> Reading your exit list…</p>`;
  }
  const rows = holdings(st.h), by = new Map(rows.map(r => [r.key, r]));
  const picks = st.x, picked = new Set(picks.map(p => p.key));
  const exits = picks.map(p => exitOf(p, by.get(p.key)));
  const live = exits.filter(e => e.row);
  const sum = k => live.reduce((a, e) => a + e[k], 0);
  const inv = sum('invested'), val = sum('value'), gain = val - inv;
  const withT = live.filter(e => e.t);
  const nShares = live.filter(e => e.row.kind === 'share').length, nFunds = live.length - nShares;
  return html`
    <div class="strip lens-strip" data-exit="strip">
      ${stat('Picked to sell', String(live.length), live.length ? [nShares ? plural(nShares, 'share') : '', nFunds ? plural(nFunds, 'fund') : ''].filter(Boolean).join(' · ') : 'tick holdings below')}
      ${stat('Put in', inr0(inv), live.length ? 'what the picked quantities cost' : '')}
      ${stat('Worth now', inr0(val), live.length ? `of ${inr0(st.h.totals.all ? st.h.totals.all.value : 0)} you hold` : '')}
      ${stat('Gain', live.length ? signed(gain, inr(gain)) : '—', live.length && inv ? pctOr((val / inv - 1) * 100) : '')}
      ${withT.length ? stat('Long term / short term', html`${signed(withT.reduce((a, e) => a + e.t.ltGain, 0), inr(withT.reduce((a, e) => a + e.t.ltGain, 0)))} <span class="mute">/</span> ${signed(withT.reduce((a, e) => a + e.t.stGain, 0), inr(withT.reduce((a, e) => a + e.t.stGain, 0)))}`,
        `gain, estimated from your tradebook${withT.length < live.length ? `, ${withT.length} of ${live.length}` : ''}`) : ''}
    </div>
    ${exitList(st, ui, exits)}
    ${holdingsTable(st, ui, rows, picked)}`;
}

// The page's own controls: picking, quantities, filters (index.js wires
// the rest). Returns what undoes them.
export function wire(main, ui, paint, { on, actions, lens, setExiter, retryExiter, loadExiter, say }) {
  ui.xdraft = ui.xdraft || {};
  const rowsNow = () => holdings(lens.h);
  const save = items => setExiter(items);
  const offActs = actions(main, {
    xpick: x => {
      if (!lens.x) return;
      const r = rowsNow().find(y => y.key === x.k);
      if (!r) return;
      save(lens.x.some(p => p.key === r.key) ? lens.x.filter(p => p.key !== r.key) : [...lens.x, pickOf(r)]);
    },
    xpickShown: () => {
      const have = new Set(lens.x.map(p => p.key));
      const add = shown(rowsNow(), have, ui).filter(r => !have.has(r.key)).map(pickOf);
      save([...lens.x, ...add]);
    },
    xunpickShown: () => {
      const drop = new Set(shown(rowsNow(), new Set(lens.x.map(p => p.key)), ui).map(r => r.key));
      save(lens.x.filter(p => !drop.has(p.key)));
    },
    xdrop: x => { delete ui.xdraft[x.k]; save(lens.x.filter(p => p.key !== x.k)); },
    xall: x => { delete ui.xdraft[x.k]; save(lens.x.map(p => (p.key === x.k ? { ...p, qty: null } : p))); },
    xclear: () => {
      const before = lens.x;
      ui.xdraft = {};
      save([]);
      say(`Cleared ${plural(before.length, 'pick')}.`, () => save(before), 8000);
    },
    xcsv: () => {
      const by = new Map(rowsNow().map(r => [r.key, r]));
      download(`exit-list-${new Date().toISOString().slice(0, 10)}.csv`, csv(lens.x.map(p => exitOf(p, by.get(p.key)))));
    },
    xretry: () => retryExiter(),
    xreload: () => loadExiter(),
    xpnl: x => { ui.xpnl = x.v; paint(); },
    xterm: x => { ui.xterm = x.v; paint(); },
    xshow: x => { ui.xshow = x.v; paint(); },
    xdir: () => { ui.xdir = ui.xdir === 'asc' ? 'desc' : 'asc'; paint(); },
  });
  const offSearch = on(main, 'input', '[data-f="xsearch"]', (e, el) => { ui.xq = el.value; paint(); });
  const offSel = on(main, 'change', 'select[data-f="xkind"], select[data-f="xsort"]', (e, el) => {
    if (el.dataset.f === 'xkind') ui.xkind = el.value;
    else { ui.xsort = el.value; ui.xdir = el.value === 'name' ? 'asc' : 'desc'; }
    paint();
  });
  // a quantity being typed is kept through redraws (live prices redraw
  // the page), and saved when you leave the box or press Enter
  const offType = on(main, 'input', 'input[data-k]', (e, el) => { ui.xdraft[el.dataset.k] = el.value; });
  const commit = el => {
    const k = el.dataset.k, r = rowsNow().find(y => y.key === k);
    if (!r || !lens.x) return;
    const got = readQty(el.value, r);
    delete ui.xdraft[k];
    if (got.err) { say(got.err); paint(); return; }
    if (got.note) say(got.note);
    const cur = lens.x.find(p => p.key === k);
    if (cur && (cur.qty ?? null) === got.qty) { paint(); return; }
    save(lens.x.map(p => (p.key === k ? { ...p, qty: got.qty } : p)));
  };
  const offChange = on(main, 'change', 'input[data-k]', (e, el) => commit(el));
  const offKey = on(main, 'keydown', 'input[data-k]', (e, el) => { if (e.key === 'Enter') { e.preventDefault(); el.blur(); } });
  return () => { offActs(); offSearch(); offSel(); offType(); offChange(); offKey(); };
}
