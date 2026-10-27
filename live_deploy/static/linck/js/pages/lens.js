// LinckLens: what the Kite account holds (app/lens.py), beside LinckTrade's
// deployments. For now its long-term holdings: shares and mutual funds,
// read live from Kite, with what they cost, what they're worth and the
// day's move.

import { api } from '../core/api.js';
import { html, mount, actions } from '../core/dom.js';
import { inr, pct, price, num, hhmm, dShort, fromDay, plural, tone } from '../core/format.js';
import { sect, empty, loading, stat } from '../ui/components.js';
import { say, fail } from '../ui/toast.js';

const pctOrDash = v => (v == null ? '—' : pct(v));
const signedInr = v => (v == null ? '—' : inr(v));

// Sortable columns: [key, label, value of a row]
const EQ_SORT = [['value', 'Current'], ['pnl', 'P&L'], ['pnl_pct', 'P&L %'], ['day_change', 'Today'], ['symbol', 'Name']];
const MF_SORT = [['value', 'Current'], ['pnl', 'P&L'], ['pnl_pct', 'P&L %'], ['fund', 'Name']];

function sorted(rows, key, dir) {
  const v = r => (r[key] == null ? (typeof rows[0]?.[key] === 'string' ? '' : -Infinity) : r[key]);
  return [...rows].sort((a, b) => {
    const x = v(a), y = v(b);
    const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
    return dir === 'asc' ? c : -c;
  });
}

function head(cols, sort, which) {
  return html`<div class="tr th lens-row ${which}" role="row">${cols.map(([k, l, cls]) => (k
    ? html`<button type="button" class="${cls || ''} ${sort.key === k ? 'on' : ''}" data-act="sort" data-which="${which}" data-k="${k}"
        aria-sort="${sort.key === k ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}">${l}${sort.key === k ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}</button>`
    : html`<span class="${cls || ''}">${l}</span>`))}</div>`;
}

function equityTable(rows, t, sort) {
  if (!rows.length) return html`<p class="small mute">No shares in this account.</p>`;
  return html`<div class="tbl lens-tbl" role="table" data-lens="equity">
    ${head([['symbol', 'Share'], [null, 'Qty', 'num'], [null, 'Avg cost', 'num'], [null, 'Price', 'num'], [null, 'Invested', 'num'],
      ['value', 'Current', 'num'], ['pnl', 'P&L', 'num'], ['pnl_pct', 'P&L %', 'num'], ['day_change', 'Today', 'num']], sort, 'eq')}
    ${sorted(rows, sort.key, sort.dir).map(r => html`<div class="tr lens-row eq" role="row" data-symbol="${r.symbol}">
      <span class="cell-name"><b>${r.symbol}</b><span>${r.exchange || ''}${r.t1_qty ? ` · ${num(r.t1_qty, 0)} T1` : ''}${r.pledged_qty ? ` · ${num(r.pledged_qty, 0)} pledged` : ''}</span></span>
      <span class="num" data-l="Qty">${num(r.qty, 0)}</span>
      <span class="num" data-l="Avg cost">${price(r.avg)}</span>
      <span class="num" data-l="Price">${price(r.ltp)}</span>
      <span class="num" data-l="Invested">${inr(r.invested, { sign: false })}</span>
      <span class="num" data-l="Current">${inr(r.value, { sign: false })}</span>
      <span class="num ${tone(r.pnl)}" data-l="P&L">${inr(r.pnl)}</span>
      <span class="num ${tone(r.pnl)}" data-l="P&L %">${pctOrDash(r.pnl_pct)}</span>
      <span class="num ${tone(r.day_change)}" data-l="Today">${signedInr(r.day_change)}${r.day_change_pct != null ? html`<small> ${pct(r.day_change_pct)}</small>` : ''}</span>
    </div>`)}
    <div class="tr tot lens-row eq" role="row">
      <span>${plural(t.count, 'share')}</span><span></span><span></span><span></span>
      <span class="num" data-l="Invested">${inr(t.invested, { sign: false })}</span>
      <span class="num" data-l="Current">${inr(t.value, { sign: false })}</span>
      <span class="num ${tone(t.pnl)}" data-l="P&L">${inr(t.pnl)}</span>
      <span class="num ${tone(t.pnl)}" data-l="P&L %">${pctOrDash(t.pnl_pct)}</span>
      <span class="num ${tone(t.day_change)}" data-l="Today">${signedInr(t.day_change)}</span>
    </div>
  </div>`;
}

function mfTable(rows, t, sort) {
  if (!rows.length) return html`<p class="small mute">No mutual funds in this account.</p>`;
  return html`<div class="tbl lens-tbl" role="table" data-lens="mf">
    ${head([['fund', 'Fund'], [null, 'Units', 'num'], [null, 'Avg NAV', 'num'], [null, 'NAV', 'num'], [null, 'Invested', 'num'],
      ['value', 'Current', 'num'], ['pnl', 'P&L', 'num'], ['pnl_pct', 'P&L %', 'num']], sort, 'mf')}
    ${sorted(rows, sort.key, sort.dir).map(r => html`<div class="tr lens-row mf" role="row" data-isin="${r.isin}">
      <span class="cell-name"><b>${r.fund}</b><span>${r.folio ? `Folio ${r.folio}` : r.isin}</span></span>
      <span class="num" data-l="Units">${num(r.units, 3)}</span>
      <span class="num" data-l="Avg NAV">${price(r.avg)}</span>
      <span class="num" data-l="NAV">${price(r.nav)}${r.nav_date ? html`<small> ${dShort(fromDay(r.nav_date))}</small>` : ''}</span>
      <span class="num" data-l="Invested">${inr(r.invested, { sign: false })}</span>
      <span class="num" data-l="Current">${inr(r.value, { sign: false })}</span>
      <span class="num ${tone(r.pnl)}" data-l="P&L">${inr(r.pnl)}</span>
      <span class="num ${tone(r.pnl)}" data-l="P&L %">${pctOrDash(r.pnl_pct)}</span>
    </div>`)}
    <div class="tr tot lens-row mf" role="row">
      <span>${plural(t.count, 'fund')}</span><span></span><span></span><span></span>
      <span class="num" data-l="Invested">${inr(t.invested, { sign: false })}</span>
      <span class="num" data-l="Current">${inr(t.value, { sign: false })}</span>
      <span class="num ${tone(t.pnl)}" data-l="P&L">${inr(t.pnl)}</span>
      <span class="num ${tone(t.pnl)}" data-l="P&L %">${pctOrDash(t.pnl_pct)}</span>
    </div>
  </div>`;
}

// Shares against funds, by what they're worth now.
function split(t) {
  const all = t.all.value || 0;
  if (!all) return '';
  const eq = (t.equity.value / all) * 100;
  return html`<div class="lens-split" aria-label="Shares ${eq.toFixed(0)}%, mutual funds ${(100 - eq).toFixed(0)}%">
    <div class="bar"><i class="eq" style="width:${eq}%"></i><i class="mf" style="width:${100 - eq}%"></i></div>
    <div class="row wrap small" style="gap:6px 18px">
      <span><i class="key eq"></i>Shares ${inr(t.equity.value, { sign: false })} · ${eq.toFixed(1)}%</span>
      <span><i class="key mf"></i>Mutual funds ${inr(t.mf.value, { sign: false })} · ${(100 - eq).toFixed(1)}%</span>
    </div></div>`;
}

export default {
  title: () => 'Holdings · LinckLens',
  render(main) {
    let data = null, err = null, busy = false;
    const sort = { eq: { key: 'value', dir: 'desc' }, mf: { key: 'value', dir: 'desc' } };

    async function load(fresh = false) {
      busy = true; paint();
      try { data = await api.lensHoldings(fresh); err = null; } catch (e) { err = e; }
      busy = false; paint();
    }
    function paint() {
      const t = data && data.totals;
      mount(main, html`<div class="page-head">
          <div class="col"><h1 class="t-h1">Holdings</h1><span class="mute">The shares and mutual funds in your Kite account, as Kite has them now.</span></div>
          <span class="grow"></span>
          ${data && data.as_of ? html`<span class="small mute">Read ${hhmm(data.as_of)}</span>` : ''}
          <button type="button" class="btn btn-sm" data-act="refresh" ${busy ? 'disabled' : ''}>${busy ? 'Reading…' : 'Refresh'}</button>
        </div>
        <div class="page lens" style="padding-top:4px">
          ${err ? (err.status === 409
              ? empty('Kite isn’t connected', 'Your holdings are read from your Kite account. Connect it with Reconnect at the top, then Refresh.')
              : empty('Couldn’t read your holdings', err.message))
            : !data ? loading(6)
            : html`
              <div class="strip lens-strip">
                ${stat('Current value', inr(t.all.value, { sign: false }), `${plural(t.equity.count, 'share')} · ${plural(t.mf.count, 'fund')}`)}
                ${stat('Invested', inr(t.all.invested, { sign: false }))}
                ${stat('Total P&L', html`<span class="${tone(t.all.pnl)}">${inr(t.all.pnl)}</span>`, pctOrDash(t.all.pnl_pct))}
                ${stat('Today, shares', html`<span class="${tone(t.equity.day_change)}">${signedInr(t.equity.day_change)}</span>`,
                  t.equity.day_change_pct == null ? 'against yesterday’s close' : `${pct(t.equity.day_change_pct)} against yesterday’s close`)}
              </div>
              ${split(t)}
              ${sect(`Shares`, t.equity.count ? `${plural(t.equity.count, 'holding')} · P&L ${inr(t.equity.pnl)} (${pctOrDash(t.equity.pnl_pct)})` : '')}
              ${equityTable(data.equity, t.equity, sort.eq)}
              ${sect(`Mutual funds`, t.mf.count ? `${plural(t.mf.count, 'fund')} · P&L ${inr(t.mf.pnl)} (${pctOrDash(t.mf.pnl_pct)}) · at each fund’s last NAV` : '')}
              ${mfTable(data.mf, t.mf, sort.mf)}`}
        </div>`);
    }
    const off = actions(main, {
      refresh: async () => { await load(true); if (!err) say('Holdings read from Kite again.'); else fail(err, 'Couldn’t read your holdings'); },
      sort: x => {
        const s = sort[x.which];
        if (s.key === x.k) s.dir = s.dir === 'asc' ? 'desc' : 'asc';
        else { s.key = x.k; s.dir = ['symbol', 'fund'].includes(x.k) ? 'asc' : 'desc'; }
        paint();
      },
    });
    load();
    return () => off();
  },
};
