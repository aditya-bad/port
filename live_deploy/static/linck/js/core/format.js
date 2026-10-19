// Number, money and time formatting — the spec's "Data rules".
//   Rupees, full:  +₹1,23,456 · −₹1,230   (Indian grouping, signed, true minus)
//   Rupees, short: ₹2.4L · ₹3.1Cr          (below ₹1L stays full)
//   Percent:       +6.23% · −0.86%
//   Prices:        25,184.30                (no currency symbol)
//   Time:          11:42:07 IST, 24-hour
//   Dates:         16 Oct '26 · Fri 16 Oct

export const MINUS = '−';
const grp0 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const grp2 = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const sgn = (n, sign) => (n < 0 ? MINUS : sign && n > 0 ? '+' : '');
const isNum = v => v != null && v !== '' && Number.isFinite(Number(v));

// ₹ full. sign: prefix + on positives (P&L is always signed).
export function inr(v, { sign = true, dp = 0 } = {}) {
  if (!isNum(v)) return '—';
  let n = Number(v);
  if (dp === 0) n = Math.round(n);
  const a = Math.abs(n);
  return `${sgn(n, sign)}₹${dp ? grp2.format(a) : grp0.format(a)}`;
}
// ₹ short for summaries and tight cells.
export function inrS(v, { sign = true } = {}) {
  if (!isNum(v)) return '—';
  const n = Number(v), a = Math.abs(n), s = sgn(Math.round(n), sign);
  if (a >= 1e7) return `${s}₹${trim(a / 1e7, a >= 1e9 ? 0 : 2)}Cr`;
  if (a >= 1e5) return `${s}₹${trim(a / 1e5, a >= 1e7 ? 0 : 1)}L`;
  return `${s}₹${grp0.format(Math.round(a))}`;
}
const trim = (x, dp) => String(Number(x.toFixed(dp)));
// Values ≥ ₹1Cr switch to short form in tight cells.
export const inrFit = (v, opts) => (isNum(v) && Math.abs(v) >= 1e7 ? inrS(v, opts) : inr(v, opts));
// Axis labels: ₹45k, ₹1.2L
export function inrAxis(v) {
  if (!isNum(v)) return '';
  const n = Number(v), a = Math.abs(n), s = n < 0 ? MINUS : '';
  if (a >= 1e7) return `${s}₹${trim(a / 1e7, 1)}Cr`;
  if (a >= 1e5) return `${s}₹${trim(a / 1e5, 1)}L`;
  if (a >= 1e3) return `${s}₹${trim(a / 1e3, a >= 1e4 ? 0 : 1)}k`;
  return `${s}₹${Math.round(a)}`;
}

export function pct(v, dp = 2, sign = true) {
  if (!isNum(v)) return '—';
  const n = Number(v);
  return `${sgn(Number(n.toFixed(dp)), sign)}${Math.abs(n).toFixed(dp)}%`;
}
export function price(v) {
  if (!isNum(v)) return '—';
  const n = Number(v);
  return (n < 0 ? MINUS : '') + grp2.format(Math.abs(n));
}
export function num(v, dp = 2) {
  if (!isNum(v)) return '—';
  const n = Number(v);
  return (n < 0 ? MINUS : '') + Math.abs(n).toLocaleString('en-IN', { maximumFractionDigits: dp, minimumFractionDigits: dp });
}
// NIFTY26NOV25600CE → "25600 CE · 26NOV" (strike first, for narrow screens)
const OPT_RE = /^([A-Z&]+?)(\d{2}(?:[A-Z]{3}|[1-9OND]\d{2}))(\d+(?:\.\d+)?)(CE|PE)$/;
export function shortSymbol(sym) {
  const m = OPT_RE.exec(sym || '');
  return m ? `${m[3]} ${m[4]} · ${m[2]}` : sym;
}
// NIFTY26NOV22400PE → { und: 'NIFTY', exp: '26NOV', strike: '22400', type: 'PE' }; null if not an option.
export function symbolParts(sym) {
  const m = OPT_RE.exec(sym || '');
  return m ? { und: m[1], exp: m[2], strike: m[3], type: m[4] } : null;
}
export const tone = v => (!isNum(v) ? 'flat' : v > 0.5 ? 'pos' : v < -0.5 ? 'neg' : 'flat');
export const toneVar = v => ({ pos: 'var(--g)', neg: 'var(--r)', flat: 'var(--mute)' }[tone(v)]);
export const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// ── Time, always IST ──────────────────────────────────────────────
const TZ = 'Asia/Kolkata';
const fParts = new Intl.DateTimeFormat('en-GB', {
  timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', weekday: 'short', hourCycle: 'h23',
});
export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const LONG_DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const LONG_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

export const toDate = v => (v instanceof Date ? v : new Date(v));
// IST calendar parts of an instant.
export function ist(v = new Date()) {
  const p = Object.fromEntries(fParts.formatToParts(toDate(v)).map(x => [x.type, x.value]));
  const y = +p.year, mo = +p.month - 1, d = +p.day;
  return { y, mo, d, h: +p.hour % 24, mi: +p.minute, s: +p.second, wd: WEEKDAYS.indexOf(p.weekday), key: `${p.year}-${p.month}-${p.day}` };
}
export const dayKey = v => ist(v).key;
export const hhmm = v => { const p = ist(v); return `${String(p.h).padStart(2, '0')}:${String(p.mi).padStart(2, '0')}`; };
export const hhmmss = v => { const p = ist(v); return `${hhmm(v)}:${String(p.s).padStart(2, '0')}`; };
export const dShort = v => { const p = ist(v); return `${p.d} ${MONTHS[p.mo]} '${String(p.y).slice(2)}`; };
export const dDay = v => { const p = ist(v); return `${WEEKDAYS[p.wd]} ${p.d} ${MONTHS[p.mo]}`; };
export const dFull = v => { const p = ist(v); return `${WEEKDAYS[p.wd]} ${p.d} ${MONTHS[p.mo]} ${p.y}`; };
export const dLong = v => { const p = ist(v); return `${LONG_DAYS[p.wd]} ${p.d} ${LONG_MONTHS[p.mo]}`; };
export const dMonthYear = v => { const p = ist(v); return `${MONTHS[p.mo]} ${p.y}`; };
export const dStamp = v => `${dShort(v)} ${hhmm(v)}`;
// "YYYY-MM-DD" (IST date string from the server) -> Date at IST noon
export const fromDay = s => new Date(`${s}T12:00:00+05:30`);

export function ago(v) {
  if (!v) return 'never';
  const days = Math.round((startOfIstDay(new Date()) - startOfIstDay(toDate(v))) / 864e5);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  return `${days}d ago`;
}
export function startOfIstDay(v) {
  const p = ist(v);
  return new Date(`${p.key}T00:00:00+05:30`).getTime();
}
export function duration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '—';
  const m = Math.round(ms / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h ${m % 60}m` : `${Math.floor(h / 24)}d ${h % 24}h`;
}

// NSE/BSE derivatives session, 09:15–15:30 IST. Holidays aren't modelled.
export const OPEN_MIN = 9 * 60 + 15, CLOSE_MIN = 15 * 60 + 30, SESSION_MIN = CLOSE_MIN - OPEN_MIN;
export function sessionFrac(now = new Date()) {
  const p = ist(now);
  if (p.wd === 0 || p.wd === 6) return 1;
  const m = p.h * 60 + p.mi + p.s / 60;
  return Math.max(0, Math.min(1, (m - OPEN_MIN) / SESSION_MIN));
}
export function sessionLeft(now = new Date()) {
  const p = ist(now);
  if (p.wd === 0 || p.wd === 6) return null;
  const m = p.h * 60 + p.mi;
  if (m < OPEN_MIN || m >= CLOSE_MIN) return null;
  const left = CLOSE_MIN - m;
  return `${Math.floor(left / 60)}h ${left % 60}m`;
}
export function isSessionOpen(now = new Date()) { return sessionLeft(now) !== null; }
