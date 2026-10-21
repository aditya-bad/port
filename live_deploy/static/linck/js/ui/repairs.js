// Stall repairs applied to a deployment (GET /v2/deployments/:id/repairs,
// written by custom_scripts/repair_stall.py): one note per repair on the
// deployment page — the window replayed, what it did, the P&L against
// leaving it as the stall left it, the paths chosen, and its trades.

import { html } from '../core/dom.js';
import { inr, price, tone, dShort, hhmm } from '../core/format.js';

const WORDS = { up_first: 'up first', down_first: 'down first' };
const PHASE = { open: 'candle open', close: 'candle close', up: 'candle high', down: 'candle low' };

export function repairsNote(list) {
  if (!list || !list.length) return html`<div data-repairs></div>`;
  return html`<div data-repairs class="fix-notes">${list.map(r => {
    const paths = Object.entries(r.choices || {}).sort().map(([m, o]) => `${m} ${WORDS[o] || o}`);
    return html`<details class="fix-note">
      <summary><span class="fix-tag">Repaired</span>
        <b>${dShort(r.from)} · ${hhmm(r.from)}–${hhmm(r.to)}</b>
        <span class="small">${r.fills ? `${r.fills} trade${r.fills === 1 ? '' : 's'} replayed from 1-minute candles` : 'replayed from 1-minute candles: nothing to trade'}</span>
        ${r.pnl != null ? html`<span class="small">window P&L <b class="${tone(r.pnl)}">${inr(r.pnl)}</b> · as left by the stall ${inr(r.left)}</span>` : ''}
      </summary>
      <div class="fix-body">
        <p class="small mute" style="margin:0">The app missed ticks in this window. The strategy's own code was replayed over it on 1-minute candles and its trades written in, marked <span class="fix-tag">Repaired</span> wherever they appear.${paths.length ? ` Paths chosen where a candle couldn't tell: ${paths.join(', ')}.` : ''} Undo: run the repair script again and press u on this deployment.</p>
        ${r.trades.length ? html`<div class="fix-trades">${r.trades.map(t => html`<div><span class="mono tiny">${t.minute} ${PHASE[t.phase] || t.phase}</span>
          <span class="small">${t.action === 'sell' ? 'Sold' : 'Bought'} ${t.qty} ${t.symbol} @ ${price(t.price)}</span>
          <span class="tiny mute">${t.reason}</span><span class="mono tiny ${tone(t.realized)}">${t.realized != null ? inr(t.realized) : ''}</span></div>`)}</div>` : ''}
      </div>
    </details>`;
  })}</div>`;
}
