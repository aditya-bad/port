// What a tradebook upload came to, in words: shown where it was done
// (Trades, Inspector) until dismissed. `files` is what
// POST /v2/lens/trades/import answered (app/lens.py import_trades), or
// `err` what went wrong before anything was stored. Pure.

import { plural, dShort, fromDay } from '../core/format.js';

function fileLine(f) {
  const name = f.name || 'file';
  if (!f.rows) return `${name}: no trades found${f.problems && f.problems.length ? ` (${f.problems[0]})` : ''}`;
  const range = f.first_day ? ` · ${dShort(fromDay(f.first_day))} to ${dShort(fromDay(f.last_day))}` : '';
  const bad = f.problems && f.problems.length
    ? ` · ${plural(f.problems.length, 'row')} not read: ${f.problems.slice(0, 3).join('; ')}${f.problems.length > 3 ? '…' : ''}` : '';
  return `${name}: ${plural(f.rows, 'trade')}, ${f.added} new${f.skipped ? `, ${f.skipped} already here` : ''}${range}${bad}`;
}

// {tone: good|warn|bad, title, lines}
export function importSummary(files, err, names = []) {
  if (err) {
    const what = names.length ? names.join(', ') : 'The file';
    return { tone: 'bad', title: 'Upload failed: nothing was imported',
      lines: [`${what}: ${err.message || 'the server didn’t answer'}. Try again; if it keeps failing, check it’s the CSV from Console › Reports › Tradebook.`] };
  }
  const list = files || [];
  const added = list.reduce((n, f) => n + (f.added || 0), 0);
  const read = list.reduce((n, f) => n + (f.rows || 0), 0);
  const empty = list.filter(f => !f.rows);
  const bad = list.some(f => f.problems && f.problems.length);
  const lines = list.map(fileLine);
  if (!read) {
    return { tone: 'bad', title: `Uploaded, but no trades found in ${list.length === 1 ? 'it' : 'any of them'}`,
      lines: [...lines, 'It needs the tradebook CSV from Console › Reports › Tradebook, with its columns as Console writes them.'] };
  }
  const title = added
    ? `Uploaded: ${plural(added, 'new trade')} imported`
    : `Uploaded: nothing new, all ${plural(read, 'trade')} were already here`;
  if (empty.length || bad) return { tone: 'warn', title: `${title}; some of it couldn’t be read`, lines };
  return { tone: 'good', title, lines };
}
