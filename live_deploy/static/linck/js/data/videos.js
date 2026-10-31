// Videos (pages/geek/videos.js): the pieces that don't need a browser,
// for tests: a length as m:ss, and the time left from the pace so far.

export const working = v => v.status !== 'done' && v.status !== 'failed';
export const mmss = s => (s == null ? '—' : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`);

// Time left, from how fast it has gone so far; nothing until it has a pace.
export function eta(v, now = Date.now()) {
  if (!working(v) || !v.started_at || !(v.progress >= 0.04)) return null;
  const spent = (now - new Date(v.started_at).getTime()) / 1000;
  const left = spent / v.progress * (1 - v.progress);
  return left < 60 ? 'under a minute left' : `about ${Math.round(left / 60)} min left`;
}
