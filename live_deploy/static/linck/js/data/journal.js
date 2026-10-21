// The trade journal, pure: tags as the server keeps them, notes grouped
// by day, and how each tag's days went. Tested on its own.

export const MAX_TAGS = 10, MAX_TAG = 30;

// "Event Day, news,  event day" -> ['event day', 'news'] (lower case,
// single spaces, no duplicates; the server does the same).
export function parseTags(input) {
  const out = [];
  for (const raw of String(input || '').split(',')) {
    const t = raw.trim().toLowerCase().split(/\s+/).filter(Boolean).join(' ');
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}
export const tagsText = tags => (tags || []).join(', ');
export const addTag = (input, tag) => tagsText([...parseTags(input), ...parseTags(tag)].filter((t, i, a) => a.indexOf(t) === i));

// What's wrong with a note before it's sent, or ''.
export function problem(body, tags) {
  if (!String(body || '').trim()) return 'Write something in the note.';
  if (String(body).length > 4000) return 'A note can be at most 4,000 characters.';
  if (tags.length > MAX_TAGS) return `At most ${MAX_TAGS} tags on a note.`;
  const long = tags.find(t => t.length > MAX_TAG);
  if (long) return `A tag can be at most ${MAX_TAG} characters: “${long}”.`;
  return '';
}

// [[day, [notes]]] newest day first, as the server sends them.
export function byDay(entries) {
  const out = [];
  for (const e of entries) {
    const last = out[out.length - 1];
    if (last && last[0] === e.day) last[1].push(e); else out.push([e.day, [e]]);
  }
  return out;
}

// Notes for one day, as tooltip lines.
export function dayNotes(entries, day, max = 3) {
  const N = entries.filter(e => e.day === day);
  if (!N.length) return [];
  const lines = N.slice(0, max).map(e => `Note  ${e.body.length > 60 ? `${e.body.slice(0, 59)}…` : e.body}`);
  if (N.length > max) lines.push(`and ${N.length - max} more note${N.length - max === 1 ? '' : 's'}`);
  return lines;
}
