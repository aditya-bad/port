// Shadow mode on a page (app/shadow.py): would Kite have taken a paper
// fill, and at what price? One chip per fill; its tooltip lists every
// rule that was checked.

import { html } from '../core/dom.js';
import { inr, price, dStamp, plural, tone } from '../core/format.js';
import { pick } from '../core/basis.js';
import { sect } from './components.js';

export const RULE = { lot: 'Lot size', freeze: 'Freeze limit', band: 'Price band', book: 'Order book', follow: 'Within 60 s' };

// What the chip says.
export function verdictLabel(s) {
  if (!s) return '';
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

export function shadowChip(s) {
  if (!s) return '';
  return html`<span class="flag sh-chip ${CLS[s.verdict] || 'unk'}" data-shadow="${s.verdict}" data-tip="${verdictTip(s)}">${verdictLabel(s)}</span>`;
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
      ${c.splits ? html`<div><span class="l">Over the freeze limit</span><b class="mono">${c.splits}</b><span class="s">orders split</span></div>` : ''}
    </div>`}
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
      : html`<p class="small mute" style="margin:8px 0 0">Each fill is checked a few seconds after it happens: lot size, the freeze limit, today’s price band and the order book, for a limit order ${sh.buffer_pct}% past the price.</p>`}
  </div>`;
}

