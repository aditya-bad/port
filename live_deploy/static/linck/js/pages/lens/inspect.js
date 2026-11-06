// LinckLens Inspector (#/lens/inspect/:view): the whole screen, like
// LinckTrade's Geek mode (its shell and styles), over app/lens_inspect.py:
// your own money against what "invested" shows, the yearly tax-free
// long-term gain, your figures without some holdings, and what each
// holding did. Nothing here trades; tradebook files can be uploaded
// (as on Trades), and what the upload came to is said here.

import { api } from '../../core/api.js';
import { html, mount, $, $$, on, actions } from '../../core/dom.js';
import { inr, pct, num, plural, tone, dShort, fromDay } from '../../core/format.js';
import { go, href } from '../../core/router.js';
import { empty } from '../../ui/components.js';
import { line, yOf, centreBar } from '../../ui/charts.js';
import { registerScrub } from '../../ui/tooltip.js';
import { head, tiles } from '../geek/ui.js';
import { inr0, signed, pctOr, importBanner, importButton } from './parts.js';
import { lens, onLens, importFiles, dismissImportNote } from '../../data/lens.js';
import { say, fail } from '../../ui/toast.js';

// [key, label, group, keyboard key]
export const VIEWS = [
  ['todo', 'What to do', 'Your money', 'A'],
  ['own', 'Your own money', 'Your money', 'O'],
  ['ideas', 'Tax opportunities', 'Your money', 'H'],
  ['tax', 'Tax-free gains', 'Your money', 'T'],
  ['whatif', 'Remove holdings', 'What if', 'W'],
  ['drag', 'What each holding did', 'What if', 'D'],
];
const GROUPS = ['Your money', 'What if'];

// what's been read this visit: kept while Inspector is open
const st = { data: null, err: null, without: new Set(), wi: null, wiKey: null, wiErr: null, auto: false, picked: false };

// Remove holdings starts from the three held that cost you most, until you pick
function autoPick() {
  if (st.picked || st.without.size || !st.data) return;
  const worst = (st.data.contributions || []).filter(r => r.held && r.rupees != null && r.rupees < 0).sort((a, b) => a.rupees - b.rupees).slice(0, 3);
  if (!worst.length) return;
  worst.forEach(r => st.without.add(r.isin));
  st.auto = true;
}

const reduced = () => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; } };
const store = (k, v) => { try { sessionStorage.setItem(k, v); } catch (e) { /* private mode */ } };
const read = k => { try { return sessionStorage.getItem(k); } catch (e) { return null; } };

// From LinckLens: the page falls away and a circle opens from the button.
export function enterInspector(from) {
  const r = from && from.getBoundingClientRect ? from.getBoundingClientRect() : null;
  store('geekEnter', JSON.stringify(r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : { x: innerWidth - 80, y: 140 }));
  store('inspectBack', location.hash);
  st.data = null; st.wi = null; st.wiKey = null;
  if (reduced()) { go('lens/inspect'); return; }
  document.body.classList.add('geek-leaving');
  setTimeout(() => go('lens/inspect'), 300);
}

function leave(shell, from) {
  const back = read('inspectBack');
  const to = back && back.startsWith('#/lens') && !back.includes('/inspect') ? back.slice(2) : 'lens';
  if (!shell || reduced()) { go(to); return; }
  if (from && from.getBoundingClientRect) {
    const r = from.getBoundingClientRect();
    shell.style.setProperty('--ox', `${r.left + r.width / 2}px`);
    shell.style.setProperty('--oy', `${r.top + r.height / 2}px`);
  }
  shell.classList.add('gk-out');
  setTimeout(() => go(to), 380);
}

function takeEntry() {
  const v = read('geekEnter');
  try { sessionStorage.removeItem('geekEnter'); } catch (e) { /* ignore */ }
  try { return v ? JSON.parse(v) : null; } catch (e) { return null; }
}

// ── What to do ─────────────────────────────────────────────────────────
// The few things worth doing now, each in rupees, from the other views:
// lots about to turn long term, the tax-free room, losses against gains
// booked, and what's held you back most.

const q3 = v => num(v, v % 1 ? 3 : 0);

export function todos(d) {
  const out = [];
  const o = d.ideas;
  const card = k => (o && o.cards || []).find(c => c.key === k);
  const fyEnd = o ? `31 Mar ${Number(o.fy.slice(0, 4)) + 1}` : '';
  const w = card('wait');
  if (w) {
    // each holding's soonest date: the lots turning long term that day (a
    // later lot of the same holding is a later date, not added in)
    const by = {};
    for (const i of w.items) {
      const x = by[i.symbol];
      if (!x || i.days < x.days) by[i.symbol] = { symbol: i.symbol, save: i.save, qty: i.qty, on: i.long_term_on, days: i.days };
      else if (i.days === x.days) { x.save += i.save; x.qty += i.qty; }
    }
    for (const x of Object.values(by).sort((a, b) => a.days - b.days || b.save - a.save).slice(0, 2)) {
      out.push({ key: 'wait', save: x.save, view: 'ideas',
        title: html`Don’t sell <b>${x.symbol}</b> before ${dShort(fromDay(x.on))}`,
        why: `${q3(x.qty)} of it turns long term in ${plural(x.days, 'day')}: sold after that, the gain is taxed at 12.5% (or not at all within the tax-free room) instead of 20%.` });
    }
  }
  const tf = card('book_tax_free');
  if (tf) out.push({ key: 'book_tax_free', save: tf.saving, view: 'tax',
    title: html`Book <b>${inr0(tf.items.reduce((a, i) => a + i.result, 0))}</b> of long-term gain tax-free before ${fyEnd}`,
    why: `Sell and buy back the same day: ${tf.items.map(i => `${q3(i.qty)} ${i.symbol}`).join(', ')}. You keep the same holdings at a higher cost, so this gain is never taxed. The room doesn’t carry over.` });
  const bl = card('book_losses');
  if (bl) out.push({ key: 'book_losses', save: bl.saving, view: 'ideas',
    title: html`Book a loss on <b>${bl.items.map(i => i.symbol).join(', ')}</b> before ${fyEnd}`,
    why: `It cancels tax on gains you’ve already booked this year. You can buy them back if you still want them.` });
  const drags = (d.contributions || []).filter(r => r.held && r.rupees != null && r.rupees < 0).sort((a, b) => a.rupees - b.rupees);
  if (drags.length) {
    const x = drags[0];
    out.push({ key: 'drag', save: null, view: 'drag',
      title: html`Look again at <b>${x.symbol}</b>: it’s cost you <span class="neg">${inr(x.rupees)}</span>`,
      why: `Your biggest loss of what you still hold${x.weight != null ? `, ${x.weight.toFixed(1)}% of everything` : ''}. Not a reason to sell on its own: check why you hold it.` });
  }
  return out.sort((a, b) => (b.save || 0) - (a.save || 0));
}

function todoView(d) {
  const list = todos(d);
  const total = list.reduce((a, t) => a + (t.save || 0), 0);
  return html`${head('What to do', 'The few things worth doing now, each with roughly what it saves you. Each opens the view it comes from.', d.ideas ? `${d.ideas.fy} · ${d.ideas.days_left} days left` : '')}
    ${!d.ideas && !(d.contributions || []).length ? noTrades()
      : !list.length ? html`<p class="ins-headline" data-todo="none">Nothing needs doing now: no lot about to turn long term in profit, no tax-free room worth using, no taxable gains a loss would cancel.</p>`
      : html`${total ? html`<p class="ins-headline" data-todo-total>Doing these saves you about <b class="pos">${inr0(total)}</b> in tax this year.</p>` : ''}
      <div class="ins-todo" role="list">${list.map((t, i) => html`<a class="ins-todo-i" role="listitem" href="${href(`lens/inspect/${t.view}`)}" data-todo="${t.key}">
        <span class="n mono">${i + 1}</span>
        <span class="t"><span class="h">${t.title}</span><span class="w small">${t.why}</span></span>
        <span class="v">${t.save ? html`<span class="tiny mute">saves about</span><b class="pos mono">${inr0(t.save)}</b>` : html`<span class="tiny mute">open ›</span>`}</span></a>`)}</div>`}
    <p class="gk-note">Rates for listed equity: 20% short term, 12.5% long term above ₹1.25 lakh a year, before cess and surcharge. Brokerage, STT, stamp duty and exit loads cut what you save. Prices move: check before you act. A guide, not tax advice.</p>`;
}

// ── Your own money ─────────────────────────────────────────────────────

function ownView(d) {
  const o = d.own;
  if (!o) return noTrades();
  const extra = o.cost_now - o.own;
  const chartH = 220;
  const ms = o.months;
  let chart = '';
  if (ms.length > 1) {
    // what differs month by month: profit put back in (at cost less your own
    // money), and sale money left unused; your own money itself is the tile
    const back = ms.map(m => m.cost - m.own), pot = ms.map(m => m.pot);
    const all = [...back, ...pot, 0];
    const lo = Math.min(...all), hi0 = Math.max(...all), pad = (hi0 - lo) * 0.08 || 1, hi = hi0 + pad;
    registerScrub('ins-own', f => {
      const i = Math.round(f * (ms.length - 1)), m = ms[i];
      return { t: `${m.month}\nBooked profit (or loss) in what you hold  ${inr(back[i])}\nSale money not reused  ${inr0(m.pot)}\nYour own money in  ${inr0(m.own)}`,
        ys: [yOf(back[i], chartH, lo - pad, hi) / chartH, yOf(pot[i], chartH, lo - pad, hi) / chartH], cs: ['var(--g)', 'var(--b)'] };
    });
    chart = html`<div class="gk-panel"><h3>Month by month <span class="s">what you hold at cost, less your own money: booked profit put back in (below the line, losses)</span></h3>
      <div class="lens-legend small"><span><i class="key g"></i>Booked profit (or loss) in what you hold</span><span><i class="key b"></i>Sale money not reused</span></div>
      <div class="lens-plot">${line({ series: [{ values: pot, color: 'var(--b)', width: 1.5, label: 'Not reused' },
        { values: back, color: 'var(--g)', width: 2, label: 'Profit put back in' }],
        h: chartH, key: 'ins-own', lo: lo - pad, hi, refs: [0] })}
        <div class="lens-axis tiny mute mono"><span>${ms[0].month}</span><span>${ms[ms.length - 1].month}</span></div></div></div>`;
  }
  return html`${head('Your own money', 'Every sale’s money goes into a pot; a buy is paid from the pot first, and only the rest is new money of yours. Reusing a sale (or selling and buying back for the tax-free gain) raises what “invested” shows, not your own money.', `${plural(d.trades, 'trade')} since ${dShort(fromDay(o.first_day))}`)}
    <p class="ins-headline" data-own>You hold <b>${inr0(o.cost_now)}</b> at cost. <b>${inr0(o.own)}</b> of that is your own money${o.pot > 0 ? html`, <b>${inr0(o.pot)}</b> of sale money sits unused` : ''};
      the other <b class="${tone(extra)}">${inr0(Math.abs(extra))}</b> is ${extra >= 0 ? 'profit you booked and put back in' : 'losses you booked'}.</p>
    ${tiles([
      ['Worth now', inr0(o.value), o.unpriced.length ? `${o.unpriced.length} at cost: no price` : `from ${inr0(o.own)} of your own`],
      ['Gain on your own money', signed(o.gain, inr(o.gain)), `${pctOr(o.gain_pct, 1)} on what you put in`],
      ['Each year, on your money', o.xirr == null ? '—' : signed(o.xirr, pct(o.xirr, 1)), 'the yearly rate, counting when each rupee went in'],
      ['Sale money reused', inr0(o.reused), `of ${inr0(o.sold)} you sold for`],
    ])}
    ${chart}
    <div class="gk-two even">
      <div class="gk-panel"><h3>By financial year</h3>
        <div class="tbl gk-tbl" role="table" data-ins="years">
          <div class="tr th ins-yr" role="row"><span>Year</span><span class="num">New money</span><span class="num">Sale money reused</span><span class="num">Booked</span></div>
          ${o.years.slice().reverse().map(y => html`<div class="tr ins-yr" role="row" data-fy="${y.fy}"><span><b>${y.fy}</b></span>
            <span class="num" data-l="New money">${inr0(y.fresh)}</span><span class="num" data-l="Reused">${inr0(y.reused)}</span>
            <span class="num ${tone(y.booked)}" data-l="Booked">${inr(y.booked)}</span></div>`)}
        </div></div>
      <div class="gk-panel"><h3>Sold and bought back <span class="s">within ${7} days: gains (or losses) booked on purpose</span></h3>
        ${o.harvests.length ? html`<div class="ins-harv">${o.harvests.map(h => html`<div class="ins-harv-i small" data-harvest="${h.symbol}">
          <b>${h.symbol}</b><span class="mono">${num(h.qty, h.qty % 1 ? 3 : 0)} sold ${dShort(fromDay(h.sold_on))}, ${num(h.bought_qty, h.bought_qty % 1 ? 3 : 0)} back ${dShort(fromDay(h.bought_on))}</span>
          <span class="mono ${tone(h.booked)}">${inr(h.booked)} ${h.kind === 'gain' ? 'booked' : 'loss booked'}</span></div>`)}</div>`
          : html`<p class="small mute">None in these trades.</p>`}
      </div>
    </div>
    <p class="gk-note">A tradebook doesn’t show money added to or taken out of the account. This assumes a sale’s money stayed there to be reused${o.pot > 0 ? '; what wasn’t reused shows as “not reused”, whether it’s in the account or you took it out' : ''}. If you took sale money out and later added new money, your own money reads lower than it was. Charges aren’t in a tradebook.${o.unmatched ? ` ${inr0(o.unmatched)} was sold beyond what the trades bought (a bonus or a transfer in): counted at no gain.` : ''}</p>`;
}

// ── The tax-free long-term gain ────────────────────────────────────────

function taxView(d) {
  const x = d.tax;
  if (!x) return noTrades();
  return html`${head('Tax-free long-term gains', `Each financial year, long-term gains on listed shares, equity ETFs, equity funds and REITs are tax-free up to ₹${(x.exemption / 100000).toFixed(2)} lakh. Booking them (and buying back) uses the room before the year ends.`, `${x.fy} · ${x.days_left} days left`)}
    ${tiles([
      ['Booked long term this year', signed(x.booked, inr(x.booked)), x.fy],
      ['Tax-free room left', inr0(x.room), `of ${inr0(x.exemption)}`],
      ['The lots below would book', signed(x.plan_gain, inr(x.plan_gain)), x.plan.length ? 'long term, within the room' : 'nothing long term in profit'],
      ['Year ends in', `${x.days_left} days`, '31 March'],
    ])}
    <div class="gk-panel"><h3>To use the room <span class="s">oldest units first, as a sale takes them; most gain for each rupee sold first</span></h3>
      ${x.plan.length ? html`<div class="tbl gk-tbl" role="table" data-ins="plan">
        <div class="tr th ins-plan" role="row"><span>Holding</span><span class="num">Sell</span><span class="num">For about</span><span class="num">Long-term gain</span></div>
        ${x.plan.map(p => html`<div class="tr ins-plan" role="row" data-plan="${p.symbol}"><span><b>${p.symbol}</b>${p.maybe ? html` <span class="tag warn" data-tip="A hybrid fund is covered only if it’s equity-oriented (65% or more in Indian shares)">if equity-oriented</span>` : ''}</span>
          <span class="num" data-l="Sell">${num(p.qty, p.qty % 1 ? 3 : 0)}</span><span class="num" data-l="For about">${inr0(p.sale)}</span>
          <span class="num pos" data-l="Gain">${inr(p.gain)}</span></div>`)}
      </div>` : html`<p class="small mute">Nothing held long term (over 12 months) is in profit right now.</p>`}
      <p class="gk-note">Buying back the same day leaves you holding the same thing at a higher cost, so that gain is never taxed. The units bought back start their own 12 months. Brokerage, STT and stamp duty apply to both legs (and an exit load to some funds). Prices move: check the gain before you sell. A guide, not tax advice.</p>
    </div>
    ${(() => {
      const gains = x.available.filter(a => a.gain > 0), losses = x.available.filter(a => a.gain <= 0);
      const rows = (list, key) => html`<div class="tbl gk-tbl" role="table" data-ins="${key}">
        <div class="tr th ins-av" role="row"><span>Holding</span><span class="num">Held long term</span><span class="num">Worth</span><span class="num">${key === 'available' ? 'Gain' : 'Loss'}</span></div>
        ${list.map(a => html`<div class="tr ins-av" role="row"><span><b>${a.symbol}</b></span>
        <span class="num" data-l="Held long term">${num(a.qty, a.qty % 1 ? 3 : 0)}</span><span class="num" data-l="Worth">${inr0(a.value)}</span><span class="num ${tone(a.gain)}" data-l="Gain">${inr(a.gain)}</span></div>`)}</div>`;
      return html`${gains.length ? html`<div class="gk-panel"><h3>Long-term gains you could book <span class="s">tax-free up to the room left, by gain</span></h3>${rows(gains, 'available')}</div>` : ''}
        ${losses.length ? html`<div class="gk-panel"><h3>Held long term at a loss <span class="s">not covered by the exemption: a long-term loss cancels long-term gains above the ₹1.25 lakh, or carries forward 8 years</span></h3>${rows(losses, 'lt-losses')}</div>` : ''}`;
    })()}
    <div class="gk-panel"><h3>Year by year</h3>
      <div class="tbl gk-tbl" role="table" data-ins="taxyears">
        <div class="tr th ins-ty" role="row"><span>Year</span><span class="num">Long term</span><span class="num">Tax-free limit</span><span class="num">Above it</span><span class="num">Short term</span></div>
        ${x.years.slice().reverse().map(y => html`<div class="tr ins-ty" role="row"><span><b>${y.fy}</b></span><span class="num ${tone(y.long)}" data-l="Long term">${inr(y.long)}</span>
          <span class="num" data-l="Limit">${inr0(y.exemption)}</span><span class="num ${y.over ? 'neg' : ''}" data-l="Above it">${y.over ? inr0(y.over) : '—'}</span>
          <span class="num ${tone(y.short)}" data-l="Short term">${inr(y.short)}</span></div>`)}
      </div></div>`;
}

// ── Tax opportunities ──────────────────────────────────────────────────

const IDEA_HEAD = { book_tax_free: ['Sell', 'Long-term gain'], book_losses: ['Sell', 'Loss booked'], keep_losses: ['Units', 'Loss now'], wait: ['Units', 'Gain now'] };

// "Wait before selling": one block per holding, each lot with when it was
// bought and when it turns long term (a holding bought twice showed twice,
// with nothing to tell the lots apart)
function waitTable(c) {
  const by = [];
  for (const i of c.items) { let g = by.find(x => x.symbol === i.symbol); if (!g) by.push(g = { symbol: i.symbol, lots: [] }); g.lots.push(i); }
  return html`<div class="tbl gk-tbl" role="table" data-ins="wait"><div class="tr th ins-wait" role="row"><span>Holding</span><span>Bought</span><span class="num">Units</span><span class="num">Gain now</span><span>Long term from</span><span class="num">Waiting saves</span></div>
    ${by.map(g => g.lots.map((i, k) => html`<div class="tr ins-wait ${k ? 'more' : ''}" role="row" data-item="${i.symbol}">
      <span>${k ? '' : html`<b>${i.symbol}</b>`}</span>
      <span data-l="Bought">${i.bought ? dShort(fromDay(i.bought)) : '—'}</span>
      <span class="num" data-l="Units">${num(i.qty, i.qty % 1 ? 3 : 0)}</span>
      <span class="num ${tone(i.result)}" data-l="Gain now">${inr(i.result)}</span>
      <span data-l="Long term from">${i.long_term_on ? html`<b>${dShort(fromDay(i.long_term_on))}</b> <span class="tiny mute">in ${plural(i.days, 'day')}</span>` : i.note}</span>
      <span class="num pos" data-l="Waiting saves">${inr0(i.save)}</span></div>`))}
  </div>`;
}

function ideasView(d) {
  const o = d.ideas;
  if (!o) return noTrades();
  const fyEnd = `31 Mar ${Number(o.fy.slice(0, 4)) + 1}`;
  return html`${head('Tax opportunities', 'Ways to pay less tax this financial year with what you hold, each with roughly what it saves and by when. Listed shares, equity ETFs, equity funds and REITs.', `${o.fy} · ${o.days_left} days left`)}
    ${tiles([
      ['Less tax, about', o.saving ? html`<span class="pos">${inr0(o.saving)}</span>` : '—', o.cards.length ? `${plural(o.cards.length, 'idea')} below` : 'nothing to do now'],
      ['Short-term gain booked', signed(o.short_booked, inr(o.short_booked)), `${o.fy}, taxed ${Math.round(o.rates.short * 100)}%`],
      ['Long-term gain booked', signed(o.long_booked, inr(o.long_booked)), `tax-free up to ${inr0(o.exemption)}, then ${o.rates.long * 100}%`],
      ['Tax-free room left', inr0(o.room), `until ${fyEnd}`],
    ])}
    ${o.cards.length ? o.cards.map(c => html`<div class="ins-idea ${c.saving ? '' : 'quiet'}" data-idea="${c.key}">
      <div class="ins-idea-h"><h3>${c.title}</h3>
        ${c.saving ? html`<span class="ins-save">about <b>${inr0(c.saving)}</b> less tax</span>` : ''}
        ${c.by ? html`<span class="tiny mute mono">by ${dShort(fromDay(c.by))}</span>` : ''}</div>
      <p class="small">${c.what}</p>
      ${c.key === 'wait' ? waitTable(c) : html`<div class="tbl gk-tbl" role="table"><div class="tr th ins-ii" role="row"><span>Holding</span><span class="num">${IDEA_HEAD[c.key][0]}</span><span class="num">Worth</span><span class="num">${IDEA_HEAD[c.key][1]}</span><span></span></div>
        ${c.items.map(i => html`<div class="tr ins-ii" role="row" data-item="${i.symbol}"><span><b>${i.symbol}</b></span>
          <span class="num" data-l="${IDEA_HEAD[c.key][0]}">${num(i.qty, i.qty % 1 ? 3 : 0)}</span><span class="num" data-l="Worth">${inr0(i.amount)}</span>
          <span class="num ${tone(i.result)}" data-l="${IDEA_HEAD[c.key][1]}">${inr(i.result)}</span>
          <span class="small mute">${i.note}</span></div>`)}
      </div>`}</div>`) : html`<p class="small mute" data-idea="none">Nothing to do this year with what you hold: no tax-free room to fill with gains, no taxable gains for a loss to cancel, nothing about to turn long term.</p>`}
    <p class="gk-note">Rates for listed equity since 23 July 2024: ${Math.round(o.rates.short * 100)}% short term, ${o.rates.long * 100}% long term above ${inr0(o.exemption)} a year; before cess and surcharge, and your other income and gains aren’t known here. Gains are from your imported trades, first in first out. Brokerage, STT, stamp duty and any exit load cut what you save. Prices move: check before you sell. India has no rule against buying back straight away, but trades made only to save tax can be questioned. A guide, not tax advice.</p>`;
}

// ── Remove holdings ────────────────────────────────────────────────────

const ROWS = [
  ['trades', 'xirr', 'XIRR, everything', v => pctOr(v, 1), 'pct'],
  ['trades', 'own_gain', 'Gain on your own money', v => (v == null ? '—' : inr(v)), 'inr'],
  ['trades', 'own_xirr', 'A year, on your own money', v => pctOr(v, 1), 'pct'],
  ['trades', 'booked', 'Booked', v => (v == null ? '—' : inr(v)), 'inr'],
  ['trades', 'on_paper', 'On paper', v => (v == null ? '—' : inr(v)), 'inr'],
  ['trades', 'vs_nifty', 'Ahead of the same money in Nifty', v => (v == null ? '—' : inr(v)), 'inr'],
];
const NOW_ROWS = [
  ['now', 'value', 'Worth now', v => inr0(v), 'inr'],
  ['now', 'count', 'Holdings', v => String(v), 'n'],
  ['now', 'effective', 'Spread like', v => (v == null ? '—' : `${v} equal holdings`), 'n'],
  ['now', 'top_weight', 'Largest holding', (v, s) => (v == null ? '—' : `${v.toFixed(1)}% ${s.now.top}`), 'pct'],
  ['year', 'ret', 'Today’s shares over the year', v => pctOr(v, 1), 'pct'],
  ['year', 'vol', 'Swings (volatility)', v => (v == null ? '—' : `${v.toFixed(1)}%`), 'pctlow'],
  ['year', 'drawdown', 'Worst fall', v => pctOr(v, 1), 'pct'],
];

function cmpRows(rows, a, b) {
  return rows.map(([part, k, label, fmt, kind]) => {
    const x = a[part] ? a[part][k] : null, y = b[part] ? b[part][k] : null;
    const ch = x == null || y == null || kind === 'n' ? null : y - x;
    const good = ch == null ? 'flat' : kind === 'pctlow' ? tone(-ch) : tone(ch);
    return html`<div class="tr ins-cmp" role="row" data-row="${k}"><span>${label}</span><span class="num" data-l="Now">${fmt(x, a)}</span>
      <span class="num" data-l="Without them">${fmt(y, b)}</span>
      <span class="num ${good}" data-l="Change">${ch == null ? '' : kind === 'inr' ? inr(ch) : `${ch >= 0 ? '+' : '−'}${Math.abs(ch).toFixed(1)} pts`}</span></div>`;
  });
}

function whatifView(d) {
  if (d.holdings_error && !d.holdings.length) return html`${head('Remove holdings')}${empty('Kite isn’t connected', 'Today’s holdings are read from Kite. Connect it, then come back.')}`;
  const picked = st.without;
  const r = st.wi;
  return html`${head('Remove holdings', 'Pick holdings to see your figures without them: had you never bought them (from your trades), and today’s holdings without them over the last year (from the daily prices).')}
    <div class="ins-picks" role="group" aria-label="Holdings to remove">${d.holdings.map(h => html`<button type="button" class="chip ${picked.has(h.isin) ? 'on' : ''}" aria-pressed="${picked.has(h.isin)}" data-act="pick" data-v="${h.isin}">
      ${h.symbol}${h.kind === 'mf' ? html` <span class="n">fund</span>` : ''} <span class="n">${(h.weight || 0).toFixed(1)}%</span></button>`)}
      ${picked.size ? html`<button type="button" class="btn btn-sm btn-ghost" data-act="clear">Clear</button>` : ''}</div>
    ${st.auto && picked.size ? html`<p class="small" data-whatif="auto">Starting with the ${plural(picked.size, 'holding')} that cost you most: ${[...picked].map(i => (d.holdings.find(h => h.isin === i) || {}).symbol).filter(Boolean).join(', ')}. Pick others to change it.</p>` : ''}
    ${!picked.size ? html`<p class="small mute" data-whatif="none">Pick one or more above. “What each holding did” ranks them all, one at a time.</p>`
      : st.wiErr ? html`<p class="small warn">${st.wiErr.message}</p>`
      : !r || st.wiKey !== [...picked].sort().join(',') ? html`<div class="skel" style="height:260px"></div>`
      : r.error ? html`<p class="small mute">${r.error}</p>`
      : html`<div class="gk-two even" data-whatif="ready">
        ${r.with.trades ? html`<div class="gk-panel"><h3>Had you never bought them <span class="s">from your trades</span></h3>
          <div class="tbl gk-tbl" role="table"><div class="tr th ins-cmp" role="row"><span></span><span class="num">With them</span><span class="num">Without</span><span class="num">Change</span></div>${cmpRows(ROWS, r.with, r.without_them)}</div></div>`
          : html`<div class="gk-panel"><h3>Had you never bought them</h3><p class="small mute">Import your tradebook (Trades) to see this.</p></div>`}
        <div class="gk-panel"><h3>Today’s holdings without them <span class="s">now, and over the last year</span></h3>
          <div class="tbl gk-tbl" role="table"><div class="tr th ins-cmp" role="row"><span></span><span class="num">With them</span><span class="num">Without</span><span class="num">Change</span></div>${cmpRows(NOW_ROWS, r.with, r.without_them)}</div>
          ${r.with.year ? html`<p class="gk-note">Nifty 50 over the same year: ${pctOr(r.with.year.nifty, 1)}. Funds have no daily prices from Kite, so the year is shares only${r.left_out_of_year.length ? `; listed within the year, left out: ${r.left_out_of_year.join(', ')}` : ''}.</p>` : ''}</div>
      </div>`}`;
}

// ── What each holding did ──────────────────────────────────────────────

function dragView(d) {
  const rows = (d.contributions || []).filter(r => r.rupees != null).sort((a, b) => b.rupees - a.rupees);
  if (!rows.length) return html`${head('What each holding did')}${empty('Nothing to rank yet', d.holdings_error ? 'Kite isn’t connected, so today’s holdings can’t be read.' : 'Import your tradebook (Trades) to rank by return.')}`;
  const mx = Math.max(1, ...rows.map(r => Math.abs(r.rupees)));
  const ups = rows.filter(r => r.rupees > 0).slice(0, 3), downs = rows.filter(r => r.rupees < 0).reverse().slice(0, 3);
  const total = rows.reduce((a, r) => a + r.rupees, 0);
  const names = xs => xs.map(r => html`<b>${r.symbol}</b> <span class="${tone(r.rupees)} mono">${inr(r.rupees)}</span>`).reduce((a, x, i) => (i ? html`${a}, ${x}` : x), '');
  return html`${head('What each holding did', 'What each one made or cost you in rupees: booked from what you sold plus on paper for what you hold, from your trades.')}
    <p class="ins-sum" data-drag-sum>Altogether <b class="${tone(total)}">${inr(total)}</b>.${ups.length ? html` Carried you: ${names(ups)}.` : ''}${downs.length ? html` Held you back: ${names(downs)}.` : ''}</p>
    <div class="tbl gk-tbl" role="table" data-ins="drag">
      <div class="tr th ins-drag" role="row"><span>Holding</span><span class="num">Weight</span><span>Made or cost you</span><span class="num">Your return without it</span></div>
      ${rows.map(r => html`<div class="tr ins-drag ${r.held ? '' : 'off'}" role="row" data-drag="${r.symbol}">
        <span class="cell-name"><b>${r.symbol}</b><span>${r.kind === 'mf' ? 'fund' : 'share'}${r.held ? '' : ' · sold out'}</span></span>
        <span class="num" data-l="Weight">${r.weight == null ? '—' : `${r.weight.toFixed(1)}%`}</span>
        <span data-l="Made or cost you">${centreBar(r.rupees, mx)}<b class="mono tiny ${tone(r.rupees)}">${inr(r.rupees)}</b></span>
        <span class="num" data-l="Your return without it" data-tip="${r.xirr_without == null ? 'No trades for it to take out' : `Your yearly return (XIRR) is ${pctOr(r.xirr_without + r.xirr_change, 1)} with it, ${pctOr(r.xirr_without, 1)} without`}">${r.xirr_without == null ? '—' : html`${pctOr(r.xirr_without, 1)}<small class="${tone(r.xirr_change)}">${r.xirr_change >= 0 ? 'it adds' : 'it costs'} ${Math.abs(r.xirr_change).toFixed(1)}%</small>`}</span></div>`)}
    </div>
    <p class="gk-note">Made or cost you: what you booked selling it plus what what you hold of it is up or down now, from your imported trades. Your return without it: your yearly return (XIRR) with its trades taken out, everything else as it was.</p>`;
}

const noTrades = () => html`${empty('No trades imported', 'This needs your Console tradebook: Console › Reports › Tradebook, the CSV for each financial year (equity, and mutual funds).')}
  <p class="small ins-up">${importButton(lens.tBusy, 'Upload tradebook files', 'btn btn-sm btn-primary')} or <a href="${href('lens/trades')}">see Trades →</a></p>`;

const VIEW = { todo: todoView, own: ownView, ideas: ideasView, tax: taxView, whatif: whatifView, drag: dragView };

export default {
  title: p => `${(VIEWS.find(v => v[0] === p.view) || VIEWS[0])[1]} · Inspector · LinckLens`,
  render(main, params) {
    const view = VIEWS.find(v => v[0] === params.view) || VIEWS[0];
    const entry = takeEntry();

    function top() {
      const o = st.data && st.data.own;
      return html`<img class="mark gk-mark" src="/icons/linck-mark.svg" alt="" width="24" height="24"><img class="mark gk-mark mark-paper" src="/icons/linck-mark-paper.svg" alt="" width="24" height="24">
        <span class="gk-badge">INSPECTOR</span><a class="gk-name ins-name" href="${href('lens')}">LinckLens</a>
        <span class="grow"></span>
        ${o ? html`<span class="gk-kv"><span>Own money</span><b>${inr0(o.own)}</b></span>
          <span class="gk-kv hide-m"><span>At cost</span><b>${inr0(o.cost_now)}</b></span>
          <span class="gk-kv hide-m"><span>Worth</span><b>${inr0(o.value)}</b></span>
          <span class="gk-kv hide-m"><span>Gain on yours</span><b class="${tone(o.gain)}">${pctOr(o.gain_pct, 1)}</b></span>` : ''}
        ${importButton(lens.tBusy, html`<span aria-hidden="true">↑</span><span class="hide-m">Upload tradebook</span>`, 'gk-exit ins-upload')}
        <button type="button" class="gk-exit" data-act="exit">Exit <kbd>Esc</kbd></button>`;
    }
    const rail = () => html`${GROUPS.map(gr => html`<div class="gk-grp"><h4>${gr}</h4>${VIEWS.filter(v => v[2] === gr).map(([k, l, , key]) => html`
      <a href="${href(`lens/inspect/${k}`)}" class="${k === view[0] ? 'on' : ''}" ${k === view[0] ? html`aria-current="page"` : ''}>${l}<span class="k">${key}</span></a>`)}</div>`)}
      <div class="foot">Keys switch views · Esc leaves<br>Read-only: nothing here trades</div>`;

    function body() {
      const note = importBanner(lens.importNote);
      if (st.err) return html`${note}${empty(st.err.status === 409 ? 'Kite isn’t connected' : 'Couldn’t read it', st.err.message)}`;
      if (!st.data) return html`${note}<div class="skel" style="height:300px"></div>`;
      return html`${note}${VIEW[view[0]](st.data)}`;
    }
    function paintView() {
      const el = $('#gkView', main);
      if (!el) return;
      mount(el, body());
      $$(':scope > *', el).forEach((c, i) => c.style.setProperty('--i', i));
      const t = $('.gk-top', main);
      if (t) mount(t, top());
    }
    mount(main, html`<div class="gk ins ${entry ? 'gk-in' : ''}" ${entry ? html`style="--ox:${entry.x}px;--oy:${entry.y}px"` : ''}>
      <div class="gk-top">${top()}</div>
      <nav class="gk-rail" aria-label="Inspector views">${rail()}</nav>
      <main class="gk-main"><div class="gk-view" id="gkView"></div></main>
    </div>`);
    if (entry) setTimeout(() => { const s = $('.gk', main); if (s) s.classList.remove('gk-in'); }, 1500);
    paintView();

    async function loadWhatif() {
      autoPick();
      const key = [...st.without].sort().join(',');
      if (!key || key === st.wiKey) { paintView(); return; }
      st.wiErr = null; st.wi = null; st.wiKey = key; paintView();
      try {
        const r = await api.lensWhatif(key);
        if (st.wiKey === key) { st.wi = r; paintView(); }
      } catch (e) { if (st.wiKey === key) { st.wiErr = e; paintView(); } }
    }

    function load() {
      api.lensInspect().then(d => { st.data = d; st.err = null; paintView(); if (view[0] === 'whatif') loadWhatif(); })
        .catch(e => { st.err = e; paintView(); });
    }
    if (!st.data) load();
    else if (view[0] === 'whatif') loadWhatif();

    // an upload: busy, then what it came to, and the views read again
    let busy = lens.tBusy, note = lens.importNote;
    const offLens = onLens(s => {
      if (s.tBusy === busy && s.importNote === note) return;
      busy = s.tBusy; note = s.importNote;
      paintView();
    });
    const offFiles = on(main, 'change', '[data-f="files"]', async (e, el) => {
      const files = [...(el.files || [])];
      el.value = '';
      if (!files.length) return;
      try {
        await importFiles(files);
        say(lens.importNote.title);
        st.data = null; st.wi = null; st.wiKey = null;
        paintView();
        load();
      } catch (err) { fail(err, 'Upload failed: nothing was imported'); }
    });

    const offActs = actions(main, {
      exit: (x, el) => leave($('.gk', main), el),
      pick: x => { st.picked = true; st.auto = false; st.without.has(x.v) ? st.without.delete(x.v) : st.without.add(x.v); loadWhatif(); },
      clear: () => { st.picked = true; st.auto = false; st.without.clear(); st.wi = null; st.wiKey = null; paintView(); },
      dismissImport: () => dismissImportNote(),
    });
    const offKeys = on(document, 'keydown', 'body', e => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest('input, textarea, select, [contenteditable]')) return;
      if (e.key === 'Escape') { e.preventDefault(); leave($('.gk', main), $('[data-act="exit"]', main)); return; }
      const hit = VIEWS.find(v => v[3].toLowerCase() === e.key.toLowerCase());
      if (hit && hit[0] !== view[0]) { e.preventDefault(); go(`lens/inspect/${hit[0]}`); }
    });
    return () => { offActs(); offKeys(); offLens(); offFiles(); };
  },
};
