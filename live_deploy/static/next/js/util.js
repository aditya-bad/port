// Shared helpers for the Desk experience: safe HTML templating, number
// and time formatting (always IST — every trade is an NSE/BSE event),
// market-session math, small DOM utilities.

// ── Safe templating ────────────────────────────────────────────────
// html`...` escapes every interpolated value unless it is itself the
// result of html`` / raw(). Arrays are joined. null/undefined/false
// render as nothing — so `${cond && html`...`}` works.
class Safe {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}
export const raw = s => new Safe(String(s));
export function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function part(v) {
  if (v == null || v === false) return '';
  if (v instanceof Safe) return v.s;
  if (Array.isArray(v)) return v.map(part).join('');
  return esc(v);
}
export function html(strings, ...vals) {
  let out = strings[0];
  for (let i = 0; i < vals.length; i++) out += part(vals[i]) + strings[i + 1];
  return new Safe(out);
}
export function mount(el, content) {
  el.innerHTML = String(content);
  return el;
}

// ── Numbers ────────────────────────────────────────────────────────
const MINUS = '−';
const inr2 = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const inr0 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const num2 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });

// −₹1,574.30 / +₹37.70 / ₹0.00. `sign`: show + on positives.
export function money(v, { sign = false, dp = 2 } = {}) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const abs = dp === 0 ? inr0.format(Math.abs(n)) : inr2.format(Math.abs(n));
  const s = n < -0.004 ? MINUS : (sign && n > 0.004 ? '+' : '');
  return `${s}₹${abs}`;
}
// Axis/compact: ₹1.2L, −₹45.3K, ₹2.1Cr
export function moneyCompact(v) {
  if (v == null) return '—';
  const n = Number(v), a = Math.abs(n), s = n < 0 ? MINUS : '';
  if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(a >= 1e8 ? 0 : 1)}Cr`;
  if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(a >= 1e6 ? 0 : 1)}L`;
  if (a >= 1e3) return `${s}₹${(a / 1e3).toFixed(a >= 1e4 ? 0 : 1)}K`;
  return `${s}₹${Math.round(a)}`;
}
export function num(v, dp = 2) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const f = dp === 2 ? num2.format(Math.abs(n)) : Math.abs(n).toLocaleString('en-IN', { maximumFractionDigits: dp });
  return (n < 0 ? MINUS : '') + f;
}
export function pct(v, { sign = false, dp = 2 } = {}) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  const s = n < -0.0049 ? MINUS : (sign && n > 0.0049 ? '+' : '');
  return `${s}${Math.abs(n).toFixed(dp)}%`;
}
export const tone = v => (v == null ? 'flat' : v > 0.004 ? 'pos' : v < -0.004 ? 'neg' : 'flat');

// ── Time (IST) ─────────────────────────────────────────────────────
const TZ = 'Asia/Kolkata';
const fTime = new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: TZ });
const fDate = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', timeZone: TZ });
const fDateY = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: TZ });
const fWeekday = new Intl.DateTimeFormat('en-IN', { weekday: 'short', day: 'numeric', month: 'short', timeZone: TZ });
const fParts = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: TZ });

export const toDate = v => (v instanceof Date ? v : new Date(v));
export const time = v => (v ? fTime.format(toDate(v)) : '—');
export const date = v => (v ? fDate.format(toDate(v)) : '—');
export const dateY = v => (v ? fDateY.format(toDate(v)) : '—');
export const weekday = v => (v ? fWeekday.format(toDate(v)) : '—');
export function dateTime(v) {
  if (!v) return '—';
  const d = toDate(v);
  return isSameIstDay(d, new Date()) ? `Today ${time(d)}` : `${date(d)}, ${time(d)}`;
}
export function istParts(d = new Date()) {
  const p = Object.fromEntries(fParts.formatToParts(toDate(d)).map(x => [x.type, x.value]));
  return { y: +p.year, mo: +p.month, d: +p.day, h: +p.hour % 24, mi: +p.minute, s: +p.second, key: `${p.year}-${p.month}-${p.day}` };
}
export const istKey = d => istParts(d).key;
export const isSameIstDay = (a, b) => istKey(a) === istKey(b);
export function ago(v) {
  if (!v) return '—';
  const s = Math.round((Date.now() - toDate(v).getTime()) / 1000);
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)}d ago`;
  return date(v);
}
export function duration(ms) {
  if (ms == null || ms < 0) return '—';
  const m = Math.round(ms / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

// NSE equity-derivatives session, IST. Holidays aren't modelled — a
// holiday simply reads as "open, no ticks", which the status strip shows.
export const SESSION_OPEN = 9 * 60 + 15;
export const SESSION_CLOSE = 15 * 60 + 30;
export function session(now = new Date()) {
  const p = istParts(now);
  const wd = new Date(Date.UTC(p.y, p.mo - 1, p.d)).getUTCDay();
  const mins = p.h * 60 + p.mi + p.s / 60;
  const weekend = wd === 0 || wd === 6;
  if (!weekend && mins >= SESSION_OPEN && mins < SESSION_CLOSE) {
    return { open: true, progress: (mins - SESSION_OPEN) / (SESSION_CLOSE - SESSION_OPEN), label: 'Market open' };
  }
  if (!weekend && mins < SESSION_OPEN) {
    const left = Math.round(SESSION_OPEN - mins);
    return { open: false, progress: 0, label: left <= 120 ? `Opens in ${duration(left * 60000)}` : 'Closed · opens today 9:15' };
  }
  const nextDay = wd === 5 && !weekend ? 'Mon' : wd === 6 ? 'Mon' : wd === 0 ? 'Mon' : 'tomorrow';
  return { open: false, progress: weekend ? 0 : 1, label: `Closed · opens ${nextDay} 9:15` };
}

// ── DOM ────────────────────────────────────────────────────────────
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
// Delegated listener: on(root, 'click', '[data-act]', (e, el) => ...)
export function on(root, type, selector, fn) {
  const h = e => {
    const el = e.target.closest(selector);
    if (el && root.contains(el)) fn(e, el);
  };
  root.addEventListener(type, h);
  return () => root.removeEventListener(type, h);
}
export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
export const humanize = k => k.replace(/_/g, ' ').replace(/\b(pct)\b/g, '%').replace(/^\w/, c => c.toUpperCase());

export const ICONS = {
  search: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  bell: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>',
  more: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>',
  user: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
  today: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>',
  list: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>',
  chart: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/></svg>',
  plus: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  menu: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
  back: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
  close: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
};
