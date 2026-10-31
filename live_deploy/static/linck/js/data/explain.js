// Explain (app/geek/explain.py): playing a replay's script. The pieces
// that don't need a browser, for tests: sentences to speak one by one
// (some voices stop after about 15 seconds of one utterance), how long a
// beat reads without a voice, and the frames the playhead glides through
// on its way to a beat.

// One sentence at a time; a decimal point or "₹12,34,567" never splits one.
export function sentences(text) {
  const out = [];
  let cur = '';
  const s = String(text || '');
  for (let k = 0; k < s.length; k += 1) {
    cur += s[k];
    if ((s[k] === '.' || s[k] === '?' || s[k] === '!') && (k + 1 === s.length || s[k + 1] === ' ') && !/\d$/.test(s[k + 1] || '')) {
      if (cur.trim()) out.push(cur.trim());
      cur = '';
    }
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

// Reading time without a voice: about 2.6 words a second, at least 2.5 s.
export const readMs = text => Math.max(2500, Math.round(String(text || '').split(/\s+/).filter(Boolean).length / 2.6 * 1000));

// The frames between `from` and `to`, at most `max` of them (the last is `to`).
export function glide(from, to, max = 40) {
  if (from === to) return [to];
  const n = Math.min(max, Math.abs(to - from));
  return Array.from({ length: n }, (_, j) => Math.round(from + (to - from) * (j + 1) / n));
}

// The voice to use: English (India) first, then any English, then the default.
export function pickVoice(voices) {
  const v = voices || [];
  return v.find(x => /^en[-_]IN/i.test(x.lang)) || v.find(x => /^en[-_]GB/i.test(x.lang)) || v.find(x => /^en/i.test(x.lang)) || null;
}
