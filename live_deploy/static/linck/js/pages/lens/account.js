// LinckLens › Account: the money in the trading account (kite.margins),
// and how LinckLens reads what it shows.

import { html } from '../../core/dom.js';
import { inr, hhmm } from '../../core/format.js';
import { sect, stat } from '../../ui/components.js';
import { inr0 } from './parts.js';

export function render(st) {
  const h = st.h, a = h.account;
  return html`
    ${sect('Money in the trading account', 'The equity segment, from Kite', null, false)}
    ${a ? html`<div class="strip lens-strip">
        ${stat('Free to use', inr0(a.net), 'cash and collateral, less what’s in use')}
        ${stat('Cash', inr0(a.cash), a.opening != null && a.opening !== a.cash ? `opened the day at ${inr0(a.opening)}` : 'in the account')}
        ${stat('From pledged holdings', inr0(a.collateral), 'margin your pledged shares and funds give')}
        ${stat('In use', inr0(a.used), a.payout ? `${inr0(a.payout)} being paid out` : 'margin blocked now')}
      </div>
      ${a.commodity_net ? html`<p class="small mute">Commodity segment: ${inr0(a.commodity_net)} free.</p>` : ''}
      <p class="tiny mute lens-note">LinckTrade’s deployments are paper: none of their trades touch this money. This is your real account.</p>`
      : html`<p class="small mute">Kite didn’t give the account’s funds this time.</p>`}

    ${sect('How LinckLens reads it')}
    <ul class="lens-how small">
      <li><b>Holdings, funds, SIPs and the account</b> come from Kite as you open LinckLens, then again after a minute or when you press Refresh${h.as_of ? ` (last read ${hhmm(h.as_of)})` : ''}. Nothing is stored and nothing is traded.</li>
      <li><b>A share’s year</b> is Kite’s daily prices for the last 365 days, read once a day, one request a share. Returns run from each period’s last close to the price now.</li>
      <li><b>Your shares against Nifty</b> is today’s shares at each past day’s close: Kite keeps no record of what you held on those days. Shares listed within the year are left out of that line.</li>
      <li><b>Funds</b> are at their last NAV. Kite gives no NAV history, so funds have no yearly line, and no day’s move.</li>
      <li><b>What each holding is</b> (stock, ETF, gold, debt, REIT) is read from its symbol and Kite’s name for it; a fund’s kind, plan and option come from Kite’s fund list.</li>
      <li><b>Swings</b> are the yearly volatility of daily moves; <b>beta</b> how much a share moves with Nifty (1 = in step). <b>Spread like</b> is 1 ÷ the sum of each holding’s squared share of the whole.</li>
    </ul>
    ${h.missing && h.missing.length ? html`${sect('What Kite didn’t give this time')}<ul class="lens-how small">${h.missing.map(m => html`<li class="mono tiny">${m}</li>`)}</ul>` : ''}`;
}
