// LinckLens › Trades: what your Console tradebook files show that Kite's
// holdings can't (app/lens_trades.py): your money's return a year (XIRR),
// your shares day by day against the same money in Nifty, money in by
// month, gains booked by financial year, short and long term, lots about to
// turn long term, each holding's lots, how your closed trades went, and
// whether the trades add up to what Kite holds.

import { html } from '../../core/dom.js';
import { inr, pct, num, price, plural, tone, dShort, fromDay } from '../../core/format.js';
import { sect, stat, empty, loading } from '../../ui/components.js';
import { line, yOf, upDownBars } from '../../ui/charts.js';
import { registerScrub } from '../../ui/tooltip.js';
import { pctOr, inr0, signed, importBanner, importButton } from './parts.js';

const CH = 240;
const KIND = { equity: 'Share', mf: 'Fund' };

function how() {
  return html`<ol class="lens-how small" data-how>
    <li>Open <b>Console</b> (console.zerodha.com) › <b>Reports</b> › <b>Tradebook</b>.</li>
    <li>Pick the segment (<b>Equity</b>, then <b>Mutual funds</b>) and a date range: a financial year at a time works.</li>
    <li>Download the <b>CSV</b>, and import each file here. Files can overlap or be imported twice: a trade is kept once.</li>
  </ol>`;
}

function importer(st) {
  return html`<div class="lens-import">${importButton(st.tBusy)}${importBanner(st.importNote)}</div>`;
}

// Your shares day by day against the same money in Nifty, in rupees.
function curveChart(c) {
  if (!c) return '';
  if (c.state === 'loading') {
    return html`<div class="lens-progress" data-curve="loading" role="status"><span class="small"><i class="spin" aria-hidden="true"></i>
      Reading each share’s prices from Kite, back to your first trade${c.total ? html`: <b class="mono">${c.done} of ${c.total}</b>` : '…'}</span>
      <div class="bar"><i style="width:${c.total ? Math.round(c.done / c.total * 100) : 4}%"></i></div>
      <span class="tiny mute">Once: they’re kept, and only each new day is read after this.</span></div>`;
  }
  if (c.state === 'error') return html`<p class="small mute" data-curve="error">${c.error}</p>`;
  if (!c.days || c.days.length < 2) return '';
  const all = [...c.value, ...c.nifty, ...c.invested];
  const lo = Math.min(...all), hi = Math.max(...all), pad = (hi - lo) * 0.06 || 1, L = Math.max(0, lo - pad), H = hi + pad;
  registerScrub('lens-trades-curve', f => {
    const i = Math.round(f * (c.days.length - 1));
    return { t: `${dShort(fromDay(c.days[i]))}\nYour shares  ${inr0(c.value[i])}\nSame money in Nifty 50  ${inr0(c.nifty[i])}\nPut in so far  ${inr0(c.invested[i])}`,
      ys: [yOf(c.value[i], CH, L, H) / CH, yOf(c.nifty[i], CH, L, H) / CH], cs: ['var(--ink)', 'var(--b)'] };
  });
  const n = c.now, d = n.vs_nifty;
  return html`<p class="lens-headline" data-curve="ready">Your shares are worth <b>${inr0(n.value)}</b>. The same buys and sells made in Nifty 50 would be worth
      <b>${inr0(n.nifty)}</b>: you’re <b class="${tone(d)}">${inr0(Math.abs(d))} ${d >= 0 ? 'ahead' : 'behind'}</b>.</p>
    <div class="lens-curve">
      <div class="lens-legend small"><span><i class="key ink"></i>Your shares</span><span><i class="key b"></i>Same money in Nifty 50</span><span><i class="key dash"></i>Put in so far</span></div>
      <div class="lens-plot">${line({ series: [{ values: c.invested, color: 'var(--line2)', width: 1.5, dash: '4 4', label: 'Put in' },
        { values: c.nifty, color: 'var(--b)', width: 1.5, label: 'Nifty 50' }, { values: c.value, color: 'var(--ink)', width: 2, label: 'Your shares' }],
        h: CH, key: 'lens-trades-curve', lo: L, hi: H })}
        <div class="lens-axis tiny mute mono"><span>${dShort(fromDay(c.days[0]))}</span><span>${dShort(fromDay(c.days[c.days.length - 1]))}</span></div></div>
    </div>
    <p class="tiny mute lens-note">Shares only: Kite has no NAV history for funds. Each day is what your trades say you held, at that day’s close; “the same money in Nifty 50” buys Nifty with each purchase and sells it with each sale, same day, same amount.
      ${c.left_out.length ? `Left out, no prices from Kite: ${c.left_out.join(', ')}.` : ''}</p>`;
}

function months(ms) {
  if (!ms.length) return '';
  const vals = ms.map(m => m.bought - m.sold);
  const tips = ms.map(m => `${m.month}\nBought  ${inr0(m.bought)}\nSold  ${inr0(m.sold)}\nShares ${inr(m.equity)} · funds ${inr(m.mf)}\nIn so far  ${inr0(m.net_in)}\n${plural(m.trades, 'trade')}`);
  return html`${sect('Money in, month by month', 'Bought less sold; hover a month')}
    <div class="lens-months">${upDownBars(vals, tips, 140)}
      <div class="lens-axis tiny mute mono"><span>${ms[0].month}</span><span>${ms[ms.length - 1].month}</span></div></div>`;
}

function years(by) {
  if (!by.length) return html`<p class="small mute">Nothing sold yet in these trades.</p>`;
  const slab = by.some(y => y.slab);
  return html`<div class="tbl fy-tbl ${slab ? 'with-slab' : ''}" role="table" data-lens="fy">
    <div class="tr th fy-row" role="row"><span>Financial year</span><span class="num">Short term</span><span class="num">Long term</span>${slab ? html`<span class="num">At slab rate</span>` : ''}<span class="num">Booked</span><span class="num">Sales</span></div>
    ${by.slice().reverse().map(y => html`<div class="tr fy-row" role="row" data-fy="${y.fy}">
      <span><b>${y.fy}</b></span>
      <span class="num ${tone(y.short)}" data-l="Short term">${inr(y.short)}</span>
      <span class="num ${tone(y.long)}" data-l="Long term">${inr(y.long)}</span>
      ${slab ? html`<span class="num ${tone(y.slab)}" data-l="At slab rate">${inr(y.slab)}</span>` : ''}
      <span class="num ${tone(y.short + y.long + y.slab)}" data-l="Booked">${inr(y.short + y.long + y.slab)}<small>${inr(y.gains)} gains · ${inr(y.losses)} losses</small></span>
      <span class="num" data-l="Sales">${y.sales}</span></div>`)}
  </div>
  <p class="tiny mute lens-note">Lots are matched first in, first out, as the tax rules count them; long term is held over 12 months, the rule for listed shares, equity ETFs and equity funds. Debt and liquid funds (and debt ETFs) bought from April 2023 have no long term: their gains are at your slab rate. Brokerage, STT and other charges aren’t in a tradebook, so these are before them. A guide for your filing, not a computation of tax.</p>`;
}

const SOON_SHOWN = 8;
function soon(list) {
  if (!list.length) return '';
  return html`${sect('Turning long term soon', 'Lots in profit that cross 12 months within 60 days')}
    <div class="lens-soon">${list.slice(0, SOON_SHOWN).map(s => html`<div class="lens-soon-i" data-soon="${s.symbol}">
      <b>${s.symbol}</b><span class="small">${num(s.qty, s.kind === 'mf' ? 3 : 0)} bought ${dShort(fromDay(s.bought))}</span>
      <span class="small pos">${inr(s.gain)} on paper</span>
      <span class="small"><b>${s.in_days} day${s.in_days === 1 ? '' : 's'}</b> to go · long term from ${dShort(fromDay(s.long_term_on))}</span></div>`)}</div>
    ${list.length > SOON_SHOWN ? html`<p class="tiny mute">${list.length - SOON_SHOWN} more lot${list.length - SOON_SHOWN === 1 ? '' : 's'}: open each holding below for its lots.</p>` : ''}`;
}

function holdingsTable(rows, ui) {
  return html`<div class="tbl ltr-tbl" role="table" data-lens="trades">
    <div class="tr th ltr-row" role="row"><span>Holding</span><span class="num">First bought</span><span class="num">Trades</span><span class="num">Held now</span>
      <span class="num">Avg cost</span><span class="num">Worth</span><span class="num">On paper</span><span class="num">Booked</span><span class="num">Return</span></div>
    ${rows.map(p => {
      const open = ui.openTrade === p.key;
      return html`<div class="tr ltr-row click ${open ? 'sel' : ''} ${p.open_qty > 0 ? '' : 'off'}" role="row" data-trade="${p.symbol}" data-act="openTrade" data-s="${p.key}" aria-expanded="${open}">
        <span class="cell-name"><b>${p.symbol}${p.kind === 'mf' ? html` <span class="tag">Fund</span>` : ''}</b><span>${p.isin || ''}${p.avg_days_open != null ? ` · held ${p.avg_days_open} days on average` : p.open_qty > 0 ? '' : ' · sold out'}</span></span>
        <span class="num" data-l="First bought">${dShort(fromDay(p.first_day))}</span>
        <span class="num" data-l="Trades">${p.trades}</span>
        <span class="num" data-l="Held now">${p.open_qty > 0 ? num(p.open_qty, p.kind === 'mf' ? 3 : 0) : '—'}${p.held_qty != null && Math.abs(p.held_qty - p.open_qty) > 0.001 ? html`<small class="warn">Kite: ${num(p.held_qty, p.kind === 'mf' ? 3 : 0)}</small>` : ''}</span>
        <span class="num" data-l="Avg cost">${p.avg == null ? '—' : price(p.avg)}</span>
        <span class="num" data-l="Worth">${p.value == null ? '—' : inr0(p.value)}</span>
        <span class="num ${tone(p.unrealized)}" data-l="On paper">${p.unrealized == null ? '—' : inr(p.unrealized)}</span>
        <span class="num ${tone(p.realized)}" data-l="Booked">${p.realized ? inr(p.realized) : '—'}</span>
        <span class="num ${tone(p.xirr ?? p.return_pct)}" data-l="Return" data-tip="${p.xirr != null ? 'XIRR: the yearly rate your buys, sells and what’s held now come to' : `Over ${p.days} days held; a yearly rate (XIRR) once there’s a year of history`}">${p.xirr != null ? html`${pct(p.xirr, 1)}<small>a year</small>` : p.return_pct != null ? html`${pct(p.return_pct, 1)}<small>in ${p.days} days</small>` : '—'}</span>
      </div>${open ? lots(p) : ''}`;
    })}
  </div>`;
}

function lots(p) {
  if (!p.lots.length) return html`<div class="lens-lots small mute" data-lots="${p.symbol}">Nothing held from these trades.</div>`;
  return html`<div class="lens-lots" data-lots="${p.symbol}"><div class="tbl" role="table">
    <div class="tr th lot-row" role="row"><span>Bought</span><span class="num">Qty</span><span class="num">At</span><span class="num">Held</span><span>Term</span><span class="num">On paper</span></div>
    ${p.lots.map(l => html`<div class="tr lot-row" role="row">
      <span data-l="Bought">${dShort(fromDay(l.day))}</span><span class="num" data-l="Qty">${num(l.qty, p.kind === 'mf' ? 3 : 0)}</span>
      <span class="num" data-l="At">${price(l.price)}</span><span class="num" data-l="Held">${l.days} days</span>
      <span data-l="Term">${l.term === 'long' ? html`<span class="tag">Long term</span>` : l.term === 'slab' ? html`<span class="tag">Slab rate</span>`
        : html`<span class="tag">Short term</span> <span class="tiny mute">long from ${dShort(fromDay(l.long_term_on))}</span>`}</span>
      <span class="num ${tone(l.gain)}" data-l="On paper">${l.gain == null ? '—' : inr(l.gain)}</span></div>`)}
  </div></div>`;
}

function behaviour(b) {
  if (!b.sales) return '';
  const longerLosers = b.avg_days_losers != null && b.avg_days_winners != null && b.avg_days_losers > b.avg_days_winners * 1.2;
  return html`${sect('How your sales went', `${plural(b.sales, 'sale')} · ${b.buys} buys, ${b.sells} sells in all`)}
    <div class="strip lens-strip">
      ${stat('Sold at a profit', b.win_rate == null ? '—' : `${b.win_rate.toFixed(0)}%`, `${b.wins} of ${b.sales}`)}
      ${stat('Average win', b.avg_win == null ? '—' : signed(b.avg_win, inr(b.avg_win)), b.avg_days_winners == null ? '' : `held ${b.avg_days_winners} days`)}
      ${stat('Average loss', b.avg_loss == null ? '—' : signed(b.avg_loss, inr(b.avg_loss)), b.avg_days_losers == null ? '' : `held ${b.avg_days_losers} days`)}
      ${stat('Win to loss', b.avg_win && b.avg_loss ? (b.avg_win / -b.avg_loss).toFixed(2) : '—', 'average win ÷ average loss')}
    </div>
    ${longerLosers ? html`<p class="small" data-note="losers-longer">You held your losing sales <b>${Math.round(b.avg_days_losers / b.avg_days_winners * 10) / 10}×</b> as long as your winners.</p>` : ''}
    <div class="lens-two">
      <div class="col" style="gap:8px;min-width:0">${sect('Best sales', '', null, false)}${b.best.filter(s => s.pnl > 0).map(s => sale(s)) || ''}</div>
      <div class="col" style="gap:8px;min-width:0">${sect('Worst sales', '', null, false)}${b.worst.filter(s => s.pnl < 0).map(s => sale(s)) || ''}</div>
    </div>`;
}
const sale = s => html`<div class="lens-sale small"><b>${s.symbol}</b><span class="mute">${dShort(fromDay(s.day))} · ${s.days} days</span><span class="num ${tone(s.pnl)} mono">${inr(s.pnl)}${s.pct != null ? html` <small>${pct(s.pct, 1)}</small>` : ''}</span></div>`;

function reconcile(list, err) {
  if (err) return html`${sect('Do the trades add up?')}<p class="small mute">Kite isn’t connected, so the trades can’t be set against what you hold now (${err}).</p>`;
  if (!list.length) return html`${sect('Do the trades add up?')}<p class="small" data-recon="ok">Yes: every holding Kite shows matches what these trades leave.</p>`;
  return html`${sect('Do the trades add up?', `${plural(list.length, 'holding')} where they don’t`)}
    <div class="lens-recon">${list.map(r => html`<div class="lens-recon-i small" data-recon="${r.symbol}">
      <b>${r.symbol}</b><span class="mono">trades leave ${num(r.trades_qty, r.kind === 'mf' ? 3 : 0)} · Kite holds ${num(r.held_qty, r.kind === 'mf' ? 3 : 0)}</span>
      <span class="mute">${r.why}</span></div>`)}</div>
    <p class="tiny mute lens-note">A tradebook has no bonuses, splits, IPO allotments or transfers. Where those happened, or a file is missing, the trades won’t add up, and the figures above are short by those shares.</p>`;
}

export function render(st, ui) {
  if (st.tErr) return empty('Couldn’t read your trades', st.tErr.message);
  if (!st.t) return loading(6);
  const t = st.t, a = t.analysis;
  const imports = t.imports || [];
  if (!a) {
    return html`${sect('Your trades', 'From Zerodha Console tradebook files')}
      <p class="small">Kite gives what you hold today, not how you got there. Your tradebook does: every buy and sell, with its date and price. Import it and LinckLens works out your return a year, your shares against the same money in Nifty, gains by financial year (short and long term), what turns long term soon, and how your sales went.</p>
      ${how()}${importer(st)}`;
  }
  const all = a.totals.all || {}, eq = a.totals.equity, mf = a.totals.mf;
  const netIn = a.months.length ? a.months[a.months.length - 1].net_in : 0;
  return html`
    <div class="strip lens-strip">
      ${stat('Money in now', inr0(netIn), `bought less sold, ${dShort(fromDay(a.first_day))} to ${dShort(fromDay(a.last_day))}`)}
      ${stat('Return a year (XIRR)', all.xirr == null ? '—' : signed(all.xirr, pct(all.xirr, 1)),
        [eq && eq.xirr != null ? `shares ${pct(eq.xirr, 1)}` : '', mf && mf.xirr != null ? `funds ${pct(mf.xirr, 1)}` : ''].filter(Boolean).join(' · ') || 'money-weighted')}
      ${stat('Booked', signed(all.realized, inr(all.realized)), 'from what you sold')}
      ${stat('On paper', signed(all.unrealized, inr(all.unrealized)), `${plural(all.open || 0, 'holding')} from these trades`)}
    </div>

    ${sect('Your shares against the same money in Nifty 50', 'Day by day, from your trades')}
    ${curveChart(t.curve)}

    ${months(a.months)}

    ${sect('Gains booked, by financial year', 'Short term: held 12 months or less')}
    ${years(a.by_year)}

    ${soon(a.long_term_soon)}

    ${sect('Each holding, from your trades', `${plural(a.holdings.length, 'holding')} · click one for its lots`)}
    ${holdingsTable(a.holdings, ui)}

    ${behaviour(a.behaviour)}

    ${reconcile(a.reconcile, t.holdings_error)}

    ${sect('Files imported', `${plural(a.count, 'trade')}${a.other.count ? ` · ${a.other.count} in ${a.other.segments.join(', ')} kept, not counted here` : ''}`)}
    <div class="lens-files">${imports.map(i => html`<div class="lens-file small" data-import="${i.id}">
      <b>${i.filename || 'file'}</b><span class="mute">${plural(i.rows, 'row')}, ${i.added} new${i.first_day ? ` · ${dShort(fromDay(i.first_day))} to ${dShort(fromDay(i.last_day))}` : ''}</span>
      <button type="button" class="btn btn-sm btn-ghost" data-act="removeImport" data-v="${i.id}" aria-label="Remove ${i.filename || 'this file'}">Remove</button></div>`)}</div>
    ${importer(st)}
    <details class="small"><summary>How to get the files</summary>${how()}</details>`;
}
