// LinckLens › Habits: how you trade, from your tradebook (app/lens_habits.py).
// The strongest findings first, each in rupees and naming the stocks;
// then the detail behind each: what trading cost, how long you hold,
// what sold shares did next, buying after run-ups, against Nifty, where
// in the day you trade, averaging down, round trips, same-day trades,
// how spread out you are, your funds, and when you place orders.

import { html } from '../../core/dom.js';
import { inr, num, price, plural, tone, dShort, fromDay } from '../../core/format.js';
import { sect, stat, empty, loading } from '../../ui/components.js';
import { inr0, signed, importButton, importBanner } from './parts.js';

const P = v => (v == null ? '—' : `${v < 0 ? '−' : v > 0 ? '+' : ''}${Math.abs(v).toFixed(1)}%`);
const R = v => (v == null ? '—' : inr(v));
const day = d => dShort(fromDay(d));

function findings(list) {
  if (!list.length) return html`<p class="small mute" data-habits="none">Nothing stands out yet: each finding needs enough trades behind it (at least five sales of a kind, or three stocks in a group). Import more of your tradebook to see more.</p>`;
  return html`<div class="hb-finds" data-habits="findings">${list.map(f => html`<a class="hb-find ${f.tone}" href="#hb-${f.section}" data-finding="${f.key}">
    <span class="hb-rs mono">${inr0(f.rupees)}</span><b>${f.title}</b><span class="small">${f.text}</span></a>`)}</div>`;
}

function costs(c) {
  const ys = c.years.filter(y => y.turnover || y.booked);
  if (!ys.length) return '';
  return html`<div id="hb-costs">${sect('What trading cost you', 'Charges estimated from your trades; tax on what you booked, by financial year')}
    <div class="strip lens-strip">
      ${stat('Booked', signed(c.booked, inr(c.booked)), 'gains less losses, all years')}
      ${stat('Charges', inr0(c.charges), 'STT, stamp duty, exchange, DP, GST')}
      ${stat('Tax on gains', inr0(c.tax), 'listed shares, after the yearly exemption')}
      ${stat('Kept', signed(c.kept, inr(c.kept)), 'booked less both')}
    </div>
    <div class="tbl hb-tbl hb-cost" role="table" data-habits="costs">
      <div class="tr th" role="row"><span>Year</span><span class="num">Traded</span><span class="num">Charges</span><span class="num">Booked</span><span class="num">Tax</span><span class="num">Charges of booked</span></div>
      ${ys.slice().reverse().map(y => html`<div class="tr" role="row" data-fy="${y.fy}"
          data-tip="${`STT ${inr(y.stt)} · stamp duty ${inr(y.stamp)} · exchange ${inr(y.exchange)} · SEBI ${inr(y.sebi)} · GST ${inr(y.gst)} · DP ${inr(y.dp)}${y.brokerage ? ` · brokerage ${inr(y.brokerage)}` : ''}\n${plural(y.orders, 'order')}`}">
        <span><b>${y.fy}</b></span><span class="num" data-l="Traded">${inr0(y.turnover)}</span><span class="num" data-l="Charges">${inr0(y.charges)}</span>
        <span class="num ${tone(y.booked)}" data-l="Booked">${R(y.booked)}</span>
        <span class="num" data-l="Tax">${y.tax ? html`${inr0(y.tax.tax)}${y.tax.exempt_used ? html`<small>${inr0(y.tax.exempt_used)} long-term tax-free</small>` : ''}` : '—'}</span>
        <span class="num" data-l="Charges of booked">${y.charges_pct_of_booked == null ? '—' : `${y.charges_pct_of_booked}%`}</span></div>`)}
    </div>
    <p class="tiny mute lens-note">Estimates: a tradebook has no contract notes. Charges at Zerodha’s published rates: STT ${c.rates.stt_delivery_pct}% on each buy and sell held overnight, stamp duty ${c.rates.stamp_buy_pct}% on buys, ₹${c.rates.dp_per_sale} DP charge a share sold a day, NSE ${c.rates.nse_exchange_pct}%, SEBI and GST; same-day trades at intraday rates; funds 0.005% stamp duty. Tax: listed shares at 15%/10% before 23 July 2024 and 20%/12.5% from it, losses set off within the year, the long-term exemption (₹1 lakh, ₹1.25 lakh from 2024-25) applied; losses carried from earlier years, funds at slab rate and same-day trades (business income) aren’t in it. A guide, not your tax computation.</p></div>`;
}

function holding(h) {
  if (!h.buckets.some(b => b.sales)) return '';
  return html`<div id="hb-holding">${sect('How long you held, and how it went', 'Your sales, by days from buying to selling')}
    <div class="tbl hb-tbl hb-hold" role="table" data-habits="holding">
      <div class="tr th" role="row"><span>Held</span><span class="num">Sales</span><span class="num">Sold at a profit</span><span class="num">Booked</span><span class="num">Return</span></div>
      ${h.buckets.map(b => html`<div class="tr ${b.enough ? '' : 'off'}" role="row" data-bucket="${b.label}">
        <span><b>${b.label}</b>${b.label === h.best ? html` <span class="tag">best</span>` : b.label === h.worst ? html` <span class="tag">worst</span>` : ''}</span>
        <span class="num" data-l="Sales">${b.sales}</span><span class="num" data-l="Sold at a profit">${b.win_rate == null ? '—' : `${b.win_rate}%`}</span>
        <span class="num ${tone(b.pnl)}" data-l="Booked">${R(b.pnl)}</span><span class="num ${tone(b.return_pct)}" data-l="Return">${P(b.return_pct)}</span></div>`)}
    </div><p class="tiny mute lens-note">Return: booked over what those shares cost. Greyed: fewer than 5 sales, too few to say.</p></div>`;
}

function afterSale(a) {
  if (!a || !a.sales.length) return '';
  const side = (s, word) => (s ? stat(word, signed(-s.missed_3m, inr(s.missed_3m)), `${s.kept_rising} of ${s.sales} kept rising · median ${P(s.median_move_3m)} in 3 months`) : '');
  return html`<div id="hb-after_sale">${sect('After you sold', 'What each share did next: the rupees you’d have now on what you sold')}
    <div class="strip lens-strip">${side(a.winners, 'Sold at a profit, 3 months on')}${side(a.losers, 'Sold at a loss, 3 months on')}
      ${a.missed_12m != null ? stat('A year on', signed(-a.missed_12m, inr(a.missed_12m)), `${plural(a.with_12m, 'sale')} a year old or more`) : ''}</div>
    <div class="tbl hb-tbl hb-after" role="table" data-habits="after_sale">
      <div class="tr th" role="row"><span>Sold</span><span class="num">At</span><span class="num">Booked</span><span class="num">1 month</span><span class="num">3 months</span><span class="num">12 months</span></div>
      ${a.sales.slice(0, 15).map(s => html`<div class="tr" role="row" data-sale="${s.symbol}">
        <span><b>${s.symbol}</b><small class="mute"> ${day(s.day)} · ${num(s.qty, 0)}</small></span><span class="num" data-l="At">${price(s.price)}</span>
        <span class="num ${tone(s.booked)}" data-l="Booked">${R(s.booked)}</span>
        ${['m1', 'm3', 'm12'].map(m => html`<span class="num" data-l="${{ m1: '1 month', m3: '3 months', m12: '12 months' }[m]}"
          ${s[m] && s[m].nifty_pct != null ? html`data-tip="Nifty 50 over the same days: ${P(s[m].nifty_pct)}"` : ''}>${s[m] ? html`<span class="${tone(s[m].move_pct)}">${P(s[m].move_pct)}</span><small>${inr(s[m].missed)}</small>` : html`<span class="mute">not yet</span>`}</span>`)}
      </div>`)}
    </div><p class="tiny mute lens-note">Each column: the share’s move from your sale price, and the rupees that move is on what you sold (positive: it kept rising after you sold). Biggest first${a.sales.length > 15 ? `; ${a.sales.length - 15} more not shown` : ''}.</p></div>`;
}

function buyTiming(b) {
  if (!b || !b.buys) return '';
  const rows = [['After a 10%+ rise', b.run_up], ['After a 10%+ fall', b.fall], ['Neither', b.flat], ['Near the year’s high', b.near_high], ['Near the year’s low', b.near_low]].filter(r => r[1]);
  return html`<div id="hb-buy_timing">${sect('When you bought', 'The month before each buy, and where it was in its year; how those buys went to now or the sale')}
    <div class="tbl hb-tbl hb-buy" role="table" data-habits="buy_timing">
      <div class="tr th" role="row"><span>Bought</span><span class="num">Buys</span><span class="num">Put in</span><span class="num">Since</span><span class="num">Return</span></div>
      ${rows.map(([l, g]) => html`<div class="tr ${g.enough ? '' : 'off'}" role="row" data-group="${l}"><span><b>${l}</b></span><span class="num" data-l="Buys">${g.buys}</span>
        <span class="num" data-l="Put in">${inr0(g.amount)}</span><span class="num ${tone(g.result)}" data-l="Since">${R(g.result)}</span><span class="num ${tone(g.return_pct)}" data-l="Return">${P(g.return_pct)}</span></div>`)}
    </div>
    ${b.chased.length ? html`<p class="small">Bought after a run-up, worst since: ${b.chased.map(c => html`<b>${c.symbol}</b> ${day(c.day)} (${P(c.before_pct)} the month before, ${P(c.after_pct)} since)`).reduce((a, x, i) => i ? html`${a}; ${x}` : x, '')}.</p>` : ''}
    <p class="tiny mute lens-note">A run-up or fall: the close the day before against 20 market days earlier. Near the high or low: in the top or bottom fifth of the 52 weeks before. Greyed: fewer than 5 buys.</p></div>`;
}

function vsNifty(v) {
  if (!v || !v.trades) return '';
  return html`<div id="hb-vs_nifty">${sect('Your picks against Nifty 50', 'Each closed trade against the same money in Nifty, bought and sold the same days')}
    <div class="strip lens-strip" data-habits="vs_nifty">
      ${stat('Your trades made', signed(v.pnl, inr(v.pnl)), plural(v.trades, 'closed lot'))}
      ${stat('Nifty 50 would have', signed(v.nifty, inr(v.nifty)), 'same days, same money')}
      ${stat('Ahead or behind', signed(v.vs, inr(v.vs)), `${v.beat} of ${v.trades} beat it`)}
    </div>
    ${v.best.length || v.worst.length ? html`<p class="small">${v.best.length ? html`Most ahead: ${v.best.map(x => `${x.symbol} ${inr(x.vs)}`).join(', ')}. ` : ''}${v.worst.length ? html`Most behind: ${v.worst.map(x => `${x.symbol} ${inr(x.vs)}`).join(', ')}.` : ''}</p>` : ''}</div>`;
}

function inDay(d) {
  if (!d || !(d.buys + d.sells + (d.left_out || 0))) return '';
  return html`<div id="hb-in_day">${sect('Where in the day', 'Your price within each day’s range: 0% its low, 100% its high')}
    <div class="strip lens-strip" data-habits="in_day">
      ${d.buys ? stat('Your buys, median', `${d.buy_median_pct}%`, plural(d.buys, 'buy')) : ''}
      ${d.sells ? stat('Your sells, median', `${d.sell_median_pct}%`, plural(d.sells, 'sell')) : ''}
      ${d.buys + d.sells === 0 ? '' : stat('Against the close', signed(-d.vs_close, inr(d.vs_close)), 'paid over the close on buys, less got over it on sells')}
    </div>${d.left_out ? html`<p class="tiny mute lens-note">${plural(d.left_out, 'trade')} left out: its price isn’t inside that day’s range in Kite’s prices, which are adjusted for later splits and bonuses.</p>` : ''}</div>`;
}

function averaging(a) {
  if (!a.added.length) return '';
  const s1 = a.added_summary, s2 = a.never_summary;
  return html`<div id="hb-averaging">${sect('Averaging down', 'Holdings you bought more of at least 5% below what you’d paid')}
    <div class="strip lens-strip">${stat('Added lower', signed(s1.result, inr(s1.result)), `${plural(s1.count, 'holding')} · ${s1.winners} up · median ${P(s1.median_pct)}`)}
      ${s2 ? stat('Never added', signed(s2.result, inr(s2.result)), `${plural(s2.count, 'holding')} · median ${P(s2.median_pct)}`) : ''}</div>
    <div class="hb-list" data-habits="averaging">${a.added.slice(0, 10).map(r => html`<div class="hb-li small"><b>${r.symbol}</b>
      <span class="mute">${r.adds.map(x => `${day(x.day)} at ${price(x.price)}, ${x.below_pct}% under ${price(x.avg)}`).join('; ')}</span>
      <span class="num ${tone(r.result)} mono">${inr(r.result)}${r.open ? html` <small>still held</small>` : ''}</span></div>`)}</div></div>`;
}

function roundTrips(r) {
  if (!r.trips.length) return '';
  return html`<div id="hb-round_trips">${sect('Sold, then bought back', `Within ${30} days: ${r.cheaper} cheaper, ${r.dearer} dearer`, html`<b class="${tone(r.effect)} mono">${inr(r.effect)}</b>`)}
    <div class="hb-list" data-habits="round_trips">${r.trips.slice(0, 10).map(t => html`<div class="hb-li small"><b>${t.symbol}</b>
      <span class="mute">sold ${day(t.sold)} at ${price(t.sold_at)}, back ${day(t.bought_back)} at ${price(t.back_at)} (${t.days} days, ${num(t.qty, 0)} shares)</span>
      <span class="num ${tone(t.effect)} mono">${inr(t.effect)}</span></div>`)}</div></div>`;
}

function intraday(d) {
  if (!d.count) return '';
  return html`<div id="hb-intraday">${sect('Bought and sold the same day', `${plural(d.count, 'time')} · ${d.wins} at a profit · taxed as business income, at your slab rate`, html`<b class="${tone(d.pnl)} mono">${inr(d.pnl)}</b>`)}
    <div class="hb-list" data-habits="intraday">${d.days.slice(0, 8).map(x => html`<div class="hb-li small"><b>${x.symbol}</b><span class="mute">${day(x.day)} · ${num(x.qty, 0)} shares</span><span class="num ${tone(x.pnl)} mono">${inr(x.pnl)}</span></div>`)}</div></div>`;
}

function spread(s) {
  if (!s.years.length) return '';
  return html`<div id="hb-spread">${sect('How spread out, and how much traded', s.peak ? `Most in one share: ${s.peak.largest}, ${s.peak.largest_pct}% in ${s.peak.month}` : '')}
    <div class="tbl hb-tbl hb-spread" role="table" data-habits="spread">
      <div class="tr th" role="row"><span>Year</span><span class="num">Held on average</span><span class="num">Sold</span><span class="num">Turnover</span><span class="num">Holdings at the end</span></div>
      ${s.years.slice().reverse().map(y => {
        const end = s.months.filter(m => m.month <= `${Number(y.fy.slice(0, 4)) + 1}-03`).pop();
        return html`<div class="tr" role="row" data-fy="${y.fy}"><span><b>${y.fy}</b></span><span class="num" data-l="Held on average">${inr0(y.avg_held)}</span>
          <span class="num" data-l="Sold">${inr0(y.sold)}</span><span class="num" data-l="Turnover">${y.turnover == null ? '—' : `${y.turnover.toFixed(1)}×`}</span>
          <span class="num" data-l="Holdings at the end">${end ? html`${end.holdings}${end.largest ? html`<small>largest ${end.largest}, ${end.largest_pct}%</small>` : ''}` : '—'}</span></div>`;
      })}
    </div><p class="tiny mute lens-note">At cost, from the trades. Turnover: what you sold in the year over what you held on average; 1× sells the whole portfolio once.</p></div>`;
}

function funds(f) {
  if (!f.funds.length) return '';
  return html`<div id="hb-funds">${sect('Your funds', `${plural(f.funds.length, 'fund')} bought${f.redemptions ? ` · ${plural(f.redemptions, 'redemption')}` : ''}`)}
    <div class="hb-list" data-habits="funds">${f.funds.map(x => html`<div class="hb-li small" data-fund="${x.fund}"><b>${x.fund}</b>
      <span class="mute">${x.sip ? `About ${inr0(x.usual)} a month, ${plural(x.months, 'month')}${x.missed.length ? `; skipped ${x.missed.join(', ')}` : ', none skipped'}${x.stopped ? '; looks stopped' : ''}` : `${plural(x.months, 'month')} with a purchase, no regular amount`}${x.lump_sums.length ? `; lump sums ${x.lump_sums.map(l => `${l.month} ${inr0(l.amount)}`).join(', ')}` : ''}</span>
      <span class="num mono">${inr0(x.total)}</span></div>`)}</div>
    <p class="tiny mute lens-note">A regular monthly buy: the same amount, within 10%, in at least three months. The current month isn’t counted as skipped.</p></div>`;
}

function orders(o) {
  if (!o.timed) return '';
  const mx = Math.max(...o.slots.map(s => s.buys + s.sells), 1);
  return html`<div id="hb-orders">${sect('When you place orders', `${plural(o.orders, 'order')}${o.split_orders ? `; ${o.split_orders} filled in parts (up to ${o.most_parts})` : ''}`)}
    <div class="hb-slots" data-habits="orders">${o.slots.map(s => html`<div class="hb-slot small"><span>${s.label}</span>
      <span class="hb-bar"><i style="width:${Math.round((s.buys + s.sells) / mx * 100)}%"></i></span><span class="mono">${s.buys} buys · ${s.sells} sells</span></div>`)}</div>
    ${o.open_share >= 30 ? html`<p class="small">${o.open_share}% of your trades are in the first 15 minutes, when prices swing most and the spread is widest.</p>` : ''}</div>`;
}

export function render(st) {
  if (st.tErr) return empty('Couldn’t read your trades', st.tErr.message);
  if (!st.t) return loading(6);
  const hb = st.t.habits;
  if (!hb) {
    return html`${sect('How you trade', 'From your Zerodha Console tradebook')}
      <p class="small">Import your tradebook on the Trades page (or here) and this page works out what your trading cost, how long you hold and how that went, what shares did after you sold them, whether buying after a run-up has worked for you, your picks against Nifty, averaging down, buying back, and your fund habits, in rupees and naming the stocks.</p>
      <div class="lens-import">${importButton(st.tBusy)}${importBanner(st.importNote)}</div>`;
  }
  const c = st.t.curve;
  return html`
    ${sect('What stands out', 'The strongest findings, biggest in rupees first; each links to its detail')}
    ${findings(hb.summary)}
    ${!hb.priced ? html`<p class="small mute lens-note" data-habits="unpriced">${c && c.state === 'loading'
      ? `Reading daily prices for every share you’ve traded (${c.done} of ${c.total}): what sold shares did next, buying after run-ups, against Nifty and where in the day come once they’re in.`
      : 'What sold shares did next, buying after run-ups, against Nifty and where in the day need daily prices from Kite: connect Kite to see them.'}</p>` : ''}
    ${costs(hb.costs)}
    ${holding(hb.holding)}
    ${afterSale(hb.after_sale)}
    ${buyTiming(hb.buy_timing)}
    ${vsNifty(hb.vs_nifty)}
    ${inDay(hb.in_day)}
    ${averaging(hb.averaging)}
    ${roundTrips(hb.round_trips)}
    ${intraday(hb.intraday)}
    ${spread(hb.spread)}
    ${funds(hb.funds)}
    ${orders(hb.orders)}`;
}
