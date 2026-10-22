// Stall repairs applied to a deployment (GET /v2/deployments/:id/repairs,
// written by custom_scripts/repair_stall.py): one note per repair on the
// deployment page — the window replayed, what it did, the P&L against
// leaving it as the stall left it, the paths chosen, and its trades; each
// opens its case on the Repair page (pages/repair-case.js), where it's undone.

import { html } from '../core/dom.js';
import { inr, price, tone, dShort, hhmm, ist, fromDay, MONTHS } from '../core/format.js';
import { fixTag } from './components.js';

const WORDS = { up_first: 'up first', down_first: 'down first' };
const PHASE = { open: 'candle open', close: 'candle close', up: 'candle high', down: 'candle low' };

export function repairsNote(list, dep) {
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
        <p class="small mute" style="margin:0">The app missed ticks in this window. The strategy's own code was replayed over it on 1-minute candles and its trades written in, marked <span class="fix-tag">Repaired</span> wherever they appear.${paths.length ? ` Paths chosen where a candle couldn't tell: ${paths.join(', ')}.` : ''}</p>
        <a class="small" href="#/repair/${r.day}/${dep}">Open the repair: its proof, and undo ›</a>
        ${r.trades.length ? html`<div class="fix-trades">${r.trades.map(t => html`<div><span class="mono tiny">${t.minute} ${PHASE[t.phase] || t.phase}</span>
          <span class="small">${t.action === 'sell' ? 'Sold' : 'Bought'} ${t.qty} ${t.symbol} @ ${price(t.price)}</span>
          <span class="tiny mute">${t.reason}</span><span class="mono tiny ${tone(t.realized)}">${t.realized != null ? inr(t.realized) : ''}</span></div>`)}</div>` : ''}
      </div>
    </details>`;
  })}</div>`;
}

// The repair marks on every page, from the book (data/book.js: v.repairs),
// by CYCLE — intraday a day, positional an entry through its adjustments to
// its exit. A deployment is marked Repaired only while the repaired cycle is
// its current one (until the next cycle begins); the cycle itself keeps the
// mark wherever it's listed. A day the end-of-day check flagged is marked
// everywhere until it's repaired or dismissed from its case. Each opens its
// case on the Repair page; inside a link of its own (`{ link: false }`)
// it's a plain mark.
const dayName = d => { const p = ist(fromDay(d)); return `${p.d} ${MONTHS[p.mo]}`; };
const cyc = x => x.cycle || { from: x.day, to: x.day, current: false };
// A cycle by its days: one day, or first – last ("now" while it's open).
export const cycleName = c => (!c ? '' : c.from === c.to ? dayName(c.from) : `${dayName(c.from)} – ${c.to ? dayName(c.to) : 'now'}`);

const flagChip = (v, open, link) => {
  const f = open[open.length - 1];
  const tip = `Flagged by the end-of-day check\n${open.map(x => `${dayName(x.day)}: ${x.headline || x.status}`).join('\n')}\nIts numbers there aren’t what the strategy would have done — review it ›`;
  return link ? html`<a class="fix-flag" href="#/repair/${f.day}/${v.id}" data-tip="${tip}">Flagged</a>`
    : html`<span class="fix-flag" data-tip="${tip}">Flagged</span>`;
};

export function repairChip(v, { link = true } = {}) {
  const r = v && v.repairs;
  if (!r) return '';
  if (r.open.length) return flagChip(v, r.open, link);
  const cur = r.done.filter(x => cyc(x).current);
  if (!cur.length) return '';
  const last = cur[cur.length - 1];
  return fixTag(last.day, link ? v.id : null, `Its current cycle (${cycleName(cyc(last))}) was repaired. Marked until the next cycle begins`);
}

// The repaired cycles of a deployment whose repair falls in [from, to):
// one each, oldest first, with the day to open.
export function repairedCycles(v, from = '0000-00-00', to = '9999-99-99') {
  const r = v && v.repairs, seen = new Map();
  for (const x of (r ? r.done : [])) {
    if (x.day < from || x.day >= to) continue;
    const c = cyc(x);
    if (!seen.has(c.from)) seen.set(c.from, { ...c, day: x.day });
  }
  return [...seen.values()];
}

// A ranking row (Leaderboard): any flagged day, and the period's repaired cycles.
export function cyclesMark(v, from, to) {
  const r = v && v.repairs;
  if (!r) return '';
  const cs = repairedCycles(v, from, to);
  const last = cs[cs.length - 1];
  return html`${r.open.length ? flagChip(v, r.open, true) : ''}${cs.length ? html`<a class="fix-tag" href="#/repair/${last.day}/${v.id}" data-tip="${`Repaired in this period — ${cs.map(cycleName).join(', ')}\nOpen the latest ›`}">${cs.length === 1 ? 'Repaired cycle' : `${cs.length} repaired cycles`}</a>` : ''}`;
}

// The days from `from` to `to` (YYYY-MM-DD) of a deployment: a trading
// day, or a cycle (Days, the session sheet).
export function spanMark(v, from, to = from) {
  const r = v && v.repairs;
  if (!r) return '';
  const f = r.open.find(x => x.day >= from && x.day <= to);
  if (f) return html`<a class="fix-flag" href="#/repair/${f.day}/${v.id}" data-tip="${`Flagged by the end-of-day check: ${dayName(f.day)}\n${f.headline || f.status}\nReview it ›`}">Flagged</a>`;
  const done = r.done.filter(x => x.day >= from && x.day <= to);
  if (!done.length) return '';
  const last = done[done.length - 1];
  return fixTag(last.day, v.id, `Repaired cycle: ${cycleName(cyc(last))}`);
}

// Under figures built from many cycles (Performance, Reports, Compare): the
// cycles among them in [from, to) that were repaired, and the days the
// end-of-day check flagged that nothing has repaired — each opening its case.
export function repairsLine(vs, from = '0000-00-00', to = '9999-99-99') {
  const items = [];
  for (const v of vs) {
    const r = v && v.repairs;
    if (!r) continue;
    for (const f of r.open) if (f.day >= from && f.day < to) items.push({ v, day: f.day, flag: true, tip: f.headline || f.status, name: dayName(f.day) });
    for (const c of repairedCycles(v, from, to)) items.push({ v, day: c.day, flag: false, name: cycleName(c) });
  }
  if (!items.length) return '';
  items.sort((a, b) => (b.flag - a.flag) || a.day.localeCompare(b.day));
  const nf = items.filter(x => x.flag).length, nr = items.length - nf, many = new Set(items.map(x => x.v.id)).size > 1;
  const cycles = n => (n === 1 ? 'a repaired cycle' : `${n} repaired cycles`);
  return html`<p class="fix-line small" data-repairs-line><span class="mute">${nf
    ? `${nr ? `Includes ${cycles(nr)}, and ` : ''}${nf === 1 ? 'a day' : `${nf} days`} the end-of-day check flagged and nothing has repaired yet — ${nf === 1 ? 'its' : 'their'} numbers aren’t what the strategy would have done:`
    : `Includes ${cycles(nr)}, rebuilt from Kite’s 1-minute candles:`}</span>
    ${items.map(x => html`<a class="${x.flag ? 'fix-flag' : 'fix-tag'}" href="#/repair/${x.day}/${x.v.id}" data-tip="${`${x.v.name} · ${x.name}\n${x.flag ? `${x.tip}\nReview it ›` : 'Repaired cycle — open it ›'}`}">${many ? `${x.v.name} · ` : ''}${x.name}</a>`)}</p>`;
}
