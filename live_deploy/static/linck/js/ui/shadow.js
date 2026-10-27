// Shadow mode on a page (app/shadow.py): would Kite have taken a paper
// fill, and at what price? One chip per fill; its tooltip lists every
// rule that was checked.

import { html } from '../core/dom.js';
import { inr, price, dStamp, hhmmss, plural, tone } from '../core/format.js';
import { pick } from '../core/basis.js';
import { sect } from './components.js';
import { openDialog } from './dialog.js';

export const RULE = { lot: 'Lot size', freeze: 'Freeze limit', band: 'Price band', book: 'Order book', follow: 'Within 60 s',
  margin: 'Margin', legs: 'Leg by leg', rate: 'Order rate' };

// What the chip says ("(est.)": estimated later from stored data).
export function verdictLabel(s) {
  if (!s) return '';
  const t = label(s);
  return s.estimated ? `${t} (est.)` : t;
}
function label(s) {
  switch (s.verdict) {
    case 'fill': return 'Would fill';
    case 'worse': return `Would fill ${inr(s.shortfall, { sign: false })} worse`;
    case 'partial': return `Would fill ${Number(s.filled_qty)} of ${Number(s.qty)}`;
    case 'no_fill': return s.followup ? 'Wouldn’t fill in 60 s' : 'Resting at the limit…';
    case 'reject': {
      const c = (s.checks || []).find(x => x.status === 'reject');
      return `Would be rejected${c ? `: ${(RULE[c.rule] || c.rule).toLowerCase()}` : ''}`;
    }
    default: return 'Couldn’t check';
  }
}

const CLS = { fill: 'ok', worse: 'warn', partial: 'warn', no_fill: 'warn', reject: 'bad', unknown: 'unk' };

// Every rule and its finding, then the order live would have sent.
export function verdictTip(s) {
  const lines = (s.checks || []).map(c => `${RULE[c.rule] || c.rule}: ${c.detail}`);
  const order = [`${s.action === 'sell' ? 'Sell' : 'Buy'} ${Number(s.qty)} · paper ₹${price(s.paper_price)}`];
  if (s.limit_price != null) order.push(`limit ₹${price(s.limit_price)}`);
  if (s.est_price != null) order.push(`est. ₹${price(s.est_price)}`);
  return [order.join(' · '), ...lines, s.estimated ? 'Estimated later from stored data.' : 'Checked as the fill happened.'].join('\n');
}

// Every chip opens how its verdict was worked out (proofDialog): the
// checks drawn on the page are kept here by fill, for the click.
const SEEN = new Map();
export function shadowChip(s) {
  if (!s) return '';
  if (s.lot_id) SEEN.set(String(s.lot_id), s);
  return html`<button type="button" class="flag sh-chip ${CLS[s.verdict] || 'unk'}" data-shadow="${s.verdict}" data-shadow-lot="${s.lot_id || ''}"
    data-tip="${`${verdictTip(s)}\nClick for how it was worked out.`}">${verdictLabel(s)}</button>`;
}
if (typeof document !== 'undefined') {
  document.addEventListener('click', e => {
    const el = e.target.closest && e.target.closest('[data-shadow-lot]');
    const s = el && SEEN.get(el.dataset.shadowLot);
    if (!s) return;
    e.preventDefault(); e.stopPropagation();
    proofDialog(s);
  });
}

const STATUS = { ok: '✓', warn: '!', reject: '✗', unknown: '?' };
const q = n => Number(n).toLocaleString('en-IN');

// Where the book came from, and how near the fill it was.
function bookWhen(p, s) {
  if (!p.source) return 'The order book read for this check.';
  const off = p.after_fill_s;
  const rel = off == null ? '' : Math.abs(off) < 0.05 ? ', at the fill' : off < 0 ? `, ${Math.abs(off).toFixed(1)} s before the fill` : `, ${off.toFixed(1)} s after the fill`;
  return p.source === 'tick'
    ? `From the tick’s own order book${p.at ? ` at ${hhmmss(p.at)}` : ''}${rel}: what the order would have met.`
    : `From Kite’s quote${p.at ? ` at ${hhmmss(p.at)}` : ''}${rel}. No fresh book came with the ticks then, so this is a little after the fill.`;
}

// The verdict's working: the book level by level, the sum, the other rules.
export function proofBody(s) {
  const book = (s.checks || []).find(c => c.rule === 'book');
  const p = book && book.proof;
  const buy = s.action === 'buy', qty = Number(s.qty), paper = Number(s.paper_price);
  const taken = p ? p.levels.filter(l => l.take > 0) : [];
  const got = taken.reduce((a, l) => a + l.take, 0), cost = taken.reduce((a, l) => a + l.take * l.price, 0);
  const avg = got ? cost / got : null;
  const sum = avg == null ? '' : html`<div class="sh-sum">
      <div><span>Average of what it takes</span><b>${q(Math.round(cost * 100) / 100)} ÷ ${q(got)} = ₹${price(avg)}</b></div>
      <div><span>Against the paper fill</span><b>(${price(buy ? avg : paper)} − ${price(buy ? paper : avg)}) × ${q(got)} = ${inr((buy ? avg - paper : paper - avg) * got, { sign: false, dp: 2 })} ${(buy ? avg - paper : paper - avg) >= 0 ? 'worse' : 'better'}</b></div>
      ${got < qty ? html`<div><span>Not filled at or inside the limit</span><b>${q(qty - got)} of ${q(qty)}: it rests at ₹${price(s.limit_price)}</b></div>` : ''}
    </div>`;
  return html`<div class="sh-proof">
    <p class="small">Paper ${buy ? 'bought' : 'sold'} ${q(qty)} at <b class="mono">₹${price(paper)}</b>, the last traded price at the fill.
      A live ${buy ? 'buy' : 'sell'} meets the ${buy ? 'sellers (the asks)' : 'buyers (the bids)'}: Linck would send a limit at
      <b class="mono">₹${price(s.limit_price)}</b>, ${buy ? 'above' : 'below'} the price by the buffer, and take each level in turn while it’s at or inside it.</p>
    ${p ? html`<p class="tiny mute">${bookWhen(p, s)}${p.other_best != null ? ` The best ${buy ? 'bid' : 'ask'} then: ₹${price(p.other_best)}.` : ''}</p>
      <div class="sh-book" role="table">
        <div class="th" role="row"><span>${buy ? 'Ask' : 'Bid'}</span><span>On offer</span><span>Taken</span><span>Cost</span></div>
        ${p.levels.map(l => html`<div role="row" class="${l.take > 0 ? 'took' : ''} ${(buy ? l.price > s.limit_price : l.price < s.limit_price) ? 'past' : ''}">
          <span class="mono">₹${price(l.price)}</span><span class="mono">${q(l.qty)}</span><span class="mono">${l.take ? q(l.take) : '—'}</span>
          <span class="mono">${l.take ? `₹${q(Math.round(l.take * l.price * 100) / 100)}` : (buy ? l.price > s.limit_price : l.price < s.limit_price) ? 'past the limit' : ''}</span></div>`)}
      </div>${sum}`
      : html`<p class="small mute">This check was made before the order book was kept with it: only its result is stored${s.est_price != null ? ` (₹${price(s.est_price)} on average, limit ₹${price(s.limit_price)})` : ''}.</p>`}
    <h4 class="small" style="margin:14px 0 6px">Every rule it checked</h4>
    <ul class="sh-rules">${(s.checks || []).map(c => html`<li class="${c.status}"><i aria-hidden="true">${STATUS[c.status] || '·'}</i><b>${RULE[c.rule] || c.rule}</b><span>${c.detail}</span></li>`)}</ul>
    <p class="tiny mute" style="margin:8px 0 0">${s.estimated ? 'Estimated later from stored data.' : `Checked as the fill happened${s.checked_at ? `, ${dStamp(s.checked_at)}` : ''}.`}</p>
  </div>`;
}

export function proofDialog(s) {
  openDialog({
    kicker: 'SHADOW CHECK',
    title: `${s.action === 'sell' ? 'Sold' : 'Bought'} ${Number(s.qty)} ${s.symbol}: ${verdictLabel(s).toLowerCase()}`,
    cancelLabel: 'Close',
    render: () => proofBody(s),
  });
}

// "Ready for live?" from a deployment's shadow record (app/shadow.py card).
export function readyShort(c) {
  if (!c || c.state === 'none') return { text: 'No record', cls: 'unk', tip: 'No fills checked in shadow mode yet.' };
  const tip = c.rules.map(r => `${r.ok ? '✓' : '✗'} ${r.label}  ${r.value}`).join('\n');
  return c.ready ? { text: 'Ready', cls: 'ok', tip } : { text: 'Not yet', cls: 'warn', tip };
}

export function readyCard(c) {
  if (!c) return '';
  const r = readyShort(c);
  const head = c.state === 'none' ? 'No shadow record yet' : c.ready ? 'Ready for live' : 'Not ready for live yet';
  const left = c.rules.filter(x => !x.ok).length;
  const money = (o, what) => (o.n ? html`<div><span class="l">${what}</span><b class="mono ${tone(pick(o, 'pnl'))}">${inr(pick(o, 'pnl'))}</b><span class="s">${plural(o.n, 'trade')}${c.total && pick(c.total, 'pnl') ? ` · of ${inr(pick(c.total, 'pnl'))} in all` : ''}</span></div>` : '');
  return html`<div class="ready-card" data-ready="${c.state}">
    <div class="row base wrap" style="gap:10px"><span class="flag sh-chip ${r.cls}">${head}</span>
      ${c.state === 'not_yet' ? html`<span class="small mute">${left} of ${c.rules.length} still to clear</span>` : ''}</div>
    ${c.state === 'none' ? '' : html`<ul class="ready-rules">${c.rules.map(x => html`<li class="${x.ok ? 'ok' : 'no'}" data-rule="${x.key}"><i aria-hidden="true">${x.ok ? '✓' : '✗'}</i><span>${x.label}</span><b class="mono small">${x.value}</b></li>`)}</ul>
    <div class="ready-stats">
      <div><span class="l">Kite would accept</span><b class="mono">${c.accepted_pct == null ? '—' : `${c.accepted_pct}%`}</b><span class="s">of the checked orders</span></div>
      <div><span class="l">Filled within 60 s</span><b class="mono">${c.filled_pct == null ? '—' : `${c.filled_pct}%`}</b><span class="s">in full</span></div>
      <div><span class="l">Worse than paper</span><b class="mono">${c.shortfall_pct == null ? '—' : `${c.shortfall_pct.toFixed(2)}%`}</b><span class="s">${inr(c.shortfall, { sign: false })} in all${c.worst ? ` · worst ${inr(c.worst.shortfall, { sign: false })}` : ''}</span></div>
      ${money(c.refused, 'Made on trades Kite would refuse')}
      ${money(c.unfilled, 'Made on trades that wouldn’t fill')}
      ${c.margin ? html`<div><span class="l">Peak margin</span><b class="mono">${inr(c.margin.required, { sign: false })}</b><span class="s">${c.margin.pct == null ? '' : `${c.margin.pct}% of its capital`}${c.margin.placing ? ' · while placing legs' : ''}</span></div>` : ''}
      ${c.legs_cost ? html`<div><span class="l">Placing legs one by one</span><b class="mono ${tone(-c.legs_cost)}">${inr(-c.legs_cost)}</b><span class="s">against filling them at once</span></div>` : ''}
      ${c.splits ? html`<div><span class="l">Over the freeze limit</span><b class="mono">${c.splits}</b><span class="s">orders split</span></div>` : ''}
    </div>
    ${c.estimated ? html`<p class="tiny mute" style="margin:0" data-estimated>${plural(c.estimated, 'fill')} estimated later from stored data: in these figures, not in the record above.</p>` : ''}`}
  </div>`;
}

// The Now tab's panel: the latest fills and what Kite would have done
// with each. `sh` is /v2/deployments/{id}/shadow; null while loading.
export function shadowPanel(sh, { all = false, n = 8 } = {}) {
  const head = (sub, right = '') => sect('Would Kite take these?', sub, right);
  if (!sh) return html`<div data-shadow-panel>${head('checking…')}</div>`;
  if (!sh.enabled && !sh.checks.length) return html`<div data-shadow-panel>${head('shadow mode is off (config shadow.enabled)')}</div>`;
  const c = sh.counts || {};
  const total = Object.values(c).reduce((a, b) => a + b, 0);
  const sum = [['fill', 'would fill'], ['worse', 'worse'], ['partial', 'partly'], ['no_fill', 'wouldn’t fill'], ['reject', 'rejected'], ['unknown', 'unchecked']]
    .filter(([k]) => c[k]).map(([k, l]) => `${c[k]} ${l}`).join(' · ');
  const rows = all ? sh.checks : sh.checks.slice(0, n);
  return html`<div data-shadow-panel>
    ${head(total ? `${total} fills checked · ${sum}` : 'no fills checked yet',
      sh.checks.length > n ? html`<button type="button" class="linkish small" data-act="shadowAll">${all ? 'Show fewer' : `Show the last ${sh.checks.length}`}</button>` : '')}
    ${readyCard(sh.card)}
    ${rows.length ? rows.map(s => html`<div class="tr sh-row small" data-lot="${s.lot_id}">
        <span class="mono tiny mute">${dStamp(s.executed_at)}</span>
        <span class="mono tiny">${s.action === 'sell' ? 'Sold' : 'Bought'} ${Number(s.qty)} ${s.symbol} @ ${price(s.paper_price)}</span>
        <span class="right">${shadowChip(s)}</span></div>`)
      : html`<p class="small mute" style="margin:8px 0 0">Each fill is checked a few seconds after it happens: lot size, the freeze limit, today’s price band and the order book, for a limit order ${sh.buffer_pct}% past the price; and for a trade, its margin, its legs placed one after another and the order rate.</p>`}
  </div>`;
}

