// The open-legs table (Desk's open positions, a deployment's Now tab):
// instrument with its index, side and expiry; quantity; entry; live LTP;
// P&L. Prices update in place on every tick and flash on a change.

import { html, $, setText, setTone } from '../core/dom.js';
import { symbolParts, inr, price, hhmm, tone } from '../core/format.js';
import * as book from '../data/book.js';
import { prices } from '../data/live.js';

const FRESH_MS = 20000;   // a tick this recent counts as streaming

function instrument(p) {
  const s = symbolParts(p.symbol);
  const side = p.side === 'short' ? 'Short' : 'Long';
  if (!s) return html`<span class="leg-ins"><b class="ell">${p.symbol}</b><span class="leg-sub ell"><span class="${p.side}">${side}</span></span></span>`;
  // phones: a side badge and the strike first, index and expiry below,
  // so nothing truncates
  return html`<span class="leg-ins" title="${p.symbol}"><b class="ell"><i class="side-badge only-phone-in ${p.side}">${p.side === 'short' ? 'S' : 'L'}</i><span class="hide-phone">${s.und} </span>${s.strike} ${s.type}</b>
    <span class="leg-sub ell"><span class="hide-phone"><span class="${p.side}">${side}</span> · </span><span class="only-phone-in">${s.und} · </span>${s.exp}</span></span>`;
}

export function legHead() {
  return html`<div class="tr th legs-tbl"><span>Instrument</span><span class="right">Qty</span><span class="right">Entry</span><span class="right">LTP</span><span class="right">P&L</span></div>`;
}

export function legRow(p) {
  const pnl = book.legPnl(p);
  return html`<div class="tr legs-tbl leg mono">${instrument(p)}
    <span class="right">${Number(p.qty)}</span>
    <span class="right">${price(p.avg_entry_price)}</span>
    <span class="right ltp" data-ltp="${p.id}">${price(book.legPrice(p))}</span>
    <span class="right ${tone(pnl)}" data-pnl="${p.id}">${inr(pnl)}</span></div>`;
}

// "Live" while ticks for these legs are arriving; otherwise when the
// shown prices are from.
export function liveState(legs) {
  const ats = legs.map(p => prices.get(p.instrument_token)).filter(Boolean).map(t => t.at);
  const last = ats.length ? Math.max(...ats) : null;
  if (last && Date.now() - last < FRESH_MS) return { live: true, text: 'Live' };
  if (last) return { live: false, text: `Last tick ${hhmm(last)}` };
  if (!legs.length) return { live: false, text: '' };
  return { live: false, text: legs.some(p => book.legPrice(p) != null) ? 'Not streaming · last known prices' : 'No prices yet' };
}
export function liveTag(legs) {
  const s = liveState(legs);
  return s.text ? html`<span class="live-tag ${s.live ? 'on' : ''}" data-live><i></i><span>${s.text}</span></span>` : '';
}

// Update every leg's LTP and P&L in place; flash the ones that moved.
export function tickLegs(root, legs) {
  for (const p of legs) {
    const a = $(`[data-ltp="${p.id}"]`, root);
    if (a) {
      const next = price(book.legPrice(p));
      if (a.textContent !== next) {
        const up = parseFloat(next.replace(/[^\d.-]/g, '')) > parseFloat(a.textContent.replace(/[^\d.-]/g, ''));
        a.textContent = next;
        a.classList.remove('flash-up', 'flash-dn');
        void a.offsetWidth;   // restart the animation
        a.classList.add(up ? 'flash-up' : 'flash-dn');
      }
    }
    const pnl = book.legPnl(p), b = $(`[data-pnl="${p.id}"]`, root);
    if (b) { setText(b, inr(pnl)); setTone(b, tone(pnl)); }
  }
  const t = $('[data-live]', root);
  if (t) {
    const s = liveState(legs);
    t.classList.toggle('on', s.live);
    setText(t.lastElementChild, s.text);
  }
}
