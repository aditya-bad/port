// Small shared pieces of markup. Everything returns html`` fragments.

import { html } from '../core/dom.js';
import { tone } from '../core/format.js';

export const STATUS = {
  active: { label: 'Active', cls: '' },
  paused: { label: 'Paused', cls: 'st-paused' },
  error: { label: 'Error', cls: 'st-error' },
  down: { label: 'Not running', cls: 'st-error' },
  stopped: { label: 'Stopped', cls: 'st-stopped' },
};
export const status = (st, label) => {
  const s = STATUS[st] || STATUS.active;
  return html`<span class="st ${s.cls}"><i aria-hidden="true"></i>${label === false ? '' : label || s.label}</span>`;
};
// Marker only (lanes, cards)
export const marker = st => html`<span class="st ${(STATUS[st] || STATUS.active).cls}" title="${(STATUS[st] || STATUS.active).label}"><i aria-hidden="true"></i></span>`;

// Money/number cell with tone. text = formatted string, v = raw number.
export const signed = (text, v, cls = '') => html`<span class="${tone(v)} ${cls}">${text}</span>`;

export const seg = (items, value, act, extra = '') => html`<div class="seg ${extra}" role="group">${items.map(([k, l]) => html`
  <button type="button" class="${k === value ? 'on' : ''}" aria-pressed="${k === value}" data-act="${act}" data-v="${k}">${l}</button>`)}</div>`;

export const utabs = (items, value, act) => html`<div class="utabs">${items.map(([k, l]) => html`
  <button type="button" class="${k === value ? 'on' : ''}" aria-pressed="${k === value}" data-act="${act}" data-v="${k}">${l}</button>`)}</div>`;

// items: [key, label, count]
export const chips = (items, value, act, { strong = false } = {}) => html`${items.map(([k, l, n]) => html`
  <button type="button" class="chip ${strong ? 'strong' : ''} ${k === value ? 'on' : ''}" aria-pressed="${k === value}" data-act="${act}" data-v="${k}">${l}${n != null ? html` <span class="n">${n}</span>` : ''}</button>`)}`;

export const check = (on, label, act, data = '') => html`<button type="button" class="check" role="checkbox" aria-checked="${!!on}" data-act="${act}" ${data}><i></i>${label}</button>`;
export const toggle = (on, act, attrs = {}, label = '') => html`<button type="button" class="toggle ${attrs.sm ? 'sm' : ''}" role="switch" aria-checked="${!!on}" aria-label="${label}"
  data-act="${act}" ${Object.entries(attrs).filter(([k]) => k !== 'sm').map(([k, v]) => html`data-${k}="${v}" `)}></button>`;

export const stat = (l, v, s, { cls = '', vcls = '' } = {}) => html`<div class="stat ${cls}"><span class="l">${l}</span><span class="v ${vcls}">${v}</span>${s != null && s !== '' ? html`<span class="s">${s}</span>` : ''}</div>`;

// Marks a trade, cycle, session or day a repair wrote into (app/repair):
// its fills were replayed from 1-minute candles, not traded live. `on` is
// the day it repaired (the server's flag); with the deployment, the mark
// links to that repair on the Repair page.
export const REPAIRED_TIP = 'Repaired\nThese fills were replayed from Kite’s 1-minute candles\nby a repair, not traded live';
export const fixTag = (on, dep, what) => {
  if (!on) return '';
  const day = typeof on === 'string' ? on : '';
  if (day && dep) return html`<a class="fix-tag" href="#/repair/${day}/${dep}" data-tip="${`${REPAIRED_TIP}\n${what || `Repair of ${day}`} — open it ›`}">Repaired</a>`;
  return html`<span class="fix-tag" data-tip="${what ? `${REPAIRED_TIP}\n${what}` : REPAIRED_TIP}">Repaired</span>`;
};

export const sect = (title, sub, right, ruled = true) => html`<div class="sect ${ruled ? 'ruled' : ''}"><h3 class="t-h3">${title}</h3>${sub ? html`<span class="mute small">${sub}</span>` : ''}${right ? html`<span class="grow"></span>${right}` : ''}</div>`;

export const empty = (title, text, action) => html`<div class="empty">${title && html`<b>${title}</b>`}<span>${text}</span>${action || ''}</div>`;

export const tagChip = t => html`<span class="tag ${t === 'Strategy Lab' ? 'lab' : ''}">${t}</span>`;
export const dia = (color, title = '') => html`<span class="dia" style="background:${color}" ${title ? html`title="${title}"` : ''}></span>`;

// A button that may be unavailable. Disabled controls always say why.
export function btn(label, act, { cls = '', disabled = false, reason = '', attrs = '' } = {}) {
  return html`<button type="button" class="btn ${cls}" data-act="${act}" ${attrs ? html`${attrs}` : ''}
    ${disabled ? html`aria-disabled="true" title="${reason}" data-reason="${reason}"` : ''}>${label}</button>`;
}

export const loading = (rows = 4) => html`<div class="stack" style="gap:10px;padding:20px 0">${Array.from({ length: rows }, () => html`<div class="skel" style="height:18px"></div>`)}</div>`;
