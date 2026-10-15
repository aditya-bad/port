// Small shared view pieces.
import { html, money, pct, tone, time, date, isSameIstDay } from './util.js';

export const pnlText = (v, opts = {}) =>
  html`<span class="${tone(v)}">${money(v, { sign: true, ...opts })}</span>`;

export const statusBadge = s => html`<span class="badge ${s}">${s[0].toUpperCase() + s.slice(1)}</span>`;

// Reason → family (mirrors the server's own trigger classifier).
export function reasonKind(reason) {
  const r = (reason || '').toLowerCase();
  if (!r) return 'other';
  if (/entry|initial|open/.test(r) && !/reentry_after/.test(r)) return 'entry';
  if (/profit|target|checkpoint|decay/.test(r)) return 'profit';
  if (/stop|force|backstop|spike|flatten|exit/.test(r)) return 'stop';
  return 'adjust';
}
const KIND_LABEL = { entry: 'Entry', profit: 'Target', stop: 'Exit', adjust: 'Adjust', other: 'Fill' };
export const reasonBadge = reason => {
  const k = reasonKind(reason);
  return html`<span class="badge ${k}">${KIND_LABEL[k]}</span>`;
};

export const kpi = (label, value, meta) => html`<div class="kpi"><div class="label">${label}</div><div class="value">${value}</div>${meta ? html`<div class="meta">${meta}</div>` : ''}</div>`;

export const empty = (title, text) => html`<div class="empty"><b>${title}</b>${text || ''}</div>`;

export const skeleton = (rows = 3) => html`<div class="loading-page">
  <div class="skeleton" style="height:120px"></div>
  ${Array.from({ length: rows }, () => html`<div class="skeleton" style="height:64px"></div>`)}</div>`;

// Raw fills → readable events: one per deployment per ~2 minutes.
export function groupFills(lots, gapMs = 120000) {
  const sorted = [...lots].sort((a, b) => new Date(b.executed_at) - new Date(a.executed_at));
  const groups = [];
  for (const l of sorted) {
    const t = new Date(l.executed_at).getTime();
    const g = groups.find(x => x.deployment_id === l.deployment_id && Math.abs(x.t - t) <= gapMs);
    if (g) g.lots.push(l);
    else groups.push({ deployment_id: l.deployment_id, deployment_name: l.deployment_name, t, lots: [l] });
  }
  return groups.map(g => {
    const kinds = g.lots.map(l => reasonKind(l.reason));
    const kind = kinds.includes('stop') ? 'stop' : kinds.includes('profit') ? 'profit' : kinds.every(k => k === 'entry') ? 'entry' : 'adjust';
    const reasons = [...new Set(g.lots.map(l => l.reason).filter(Boolean))];
    const verb = { entry: 'Entered', stop: 'Exited', profit: 'Took profit on', adjust: 'Adjusted' }[kind];
    return { ...g, kind, reasons, title: `${verb} ${g.lots.length} leg${g.lots.length === 1 ? '' : 's'}` };
  });
}

export const when = v => (isSameIstDay(v, new Date()) ? time(v) : date(v));
export const winRate = (w, l) => (w + l ? (w / (w + l)) * 100 : null);
export const pctText = v => (v == null ? '—' : pct(v, { dp: 0 }));
