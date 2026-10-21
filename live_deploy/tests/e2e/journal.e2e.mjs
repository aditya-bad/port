// The trade journal: write, tag, find, edit and delete notes on the
// Journal page; notes on a deployment from Geek mode, on one day (a
// straddle) and on one trade; and a day's notes in the Overview's daily
// P&L tooltip. Each note sits next to what was booked that day.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });
const uniq = () => `n${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
const api = async (ctx, path) => (await ctx.request.get(`${BASE}${path}`)).json();

for (const [width, label] of [[1440, 'desktop'], [390, 'phone']]) {
  test(`the Journal page: write, tag, find, edit and delete a note (${label})`, async () => {
    const ctx = await context(b, { width, height: 900 });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/journal`);
    await p.waitForSelector('[data-jr-body]');
    const text = `Kite dropped at 11:02 ${uniq()}`;
    // an empty note is refused before it's sent
    await p.click('.jr-new button[type=submit]');
    await p.waitForSelector('.jr-new [role=alert]');
    assert.match(await p.innerText('.jr-new [role=alert]'), /Write something/);
    await p.fill('[data-jr-body]', text);
    await p.fill('[data-jr-tags]', 'Data Gap');
    await p.click('.jr-new [data-act="jrTag"][data-t="kite down"]');
    assert.equal(await p.inputValue('[data-jr-tags]'), 'data gap, kite down', 'a suggested tag is added, what was typed is kept');
    assert.equal(await p.inputValue('[data-jr-body]'), text);
    await p.click('.jr-new button[type=submit]');
    const note = p.locator('.jr-note', { hasText: text });
    await note.waitFor();
    // by default a note is attached to nothing: no deployment, no day's P&L
    assert.match(await note.innerText(), /Not attached/);
    assert.doesNotMatch(await note.innerText(), /₹|no trades closed/);
    assert.deepEqual(await note.locator('.jr-tag').allInnerTexts(), ['data gap', 'kite down']);
    assert.equal(await p.inputValue('[data-jr-body]'), '', 'the box is cleared');
    // a note on the whole day carries that day's P&L
    const day = `Whole market gapped ${uniq()}`;
    await p.fill('[data-jr-body]', day);
    await p.selectOption('[data-jr-dep]', 'day');
    await p.click('.jr-new button[type=submit]');
    const dayNote = p.locator('.jr-note', { hasText: day });
    await dayNote.waitFor();
    assert.match(await dayNote.innerText(), /Whole day/);
    assert.match(await dayNote.innerText(), /₹|no trades closed/);
    // the filter for notes attached to nothing
    await p.selectOption('[data-f="dep"]', 'none');
    await p.waitForFunction(([a, b]) => { const t = [...document.querySelectorAll('.jr-note')].map(n => n.innerText).join('|'); return t.includes(a) && !t.includes(b); }, [text, day]);
    await p.selectOption('[data-f="dep"]', '');
    await p.waitForFunction(d => [...document.querySelectorAll('.jr-note')].some(n => n.innerText.includes(d)), day);
    await dayNote.locator('[data-act="jrAsk"]').click();
    await dayNote.locator('[data-act="jrDelete"]').click();
    await p.waitForFunction(d => ![...document.querySelectorAll('.jr-note')].some(n => n.innerText.includes(d)), day);
    // the tag list counts it; picking the tag filters to its notes
    await p.waitForSelector('.jr-tagrow[data-t="kite down"]');
    await p.click('.jr-tagrow[data-t="kite down"]');
    await p.waitForFunction(() => [...document.querySelectorAll('.jr-note .jr-tag')].length && [...document.querySelectorAll('.jr-note')].every(n => n.innerText.includes('kite down')));
    await p.click('.jr-tagrow[data-t="kite down"]');   // and off again
    // search finds it
    await p.fill('[data-f="q"]', text.split(' ').pop());
    await p.waitForFunction(t => document.querySelectorAll('.jr-note').length === 1 && document.querySelector('.jr-note').innerText.includes(t), text);
    // edit
    await note.locator('[data-act="jrEdit"]').click();
    await p.fill('[data-edit-body]', `${text} — back at 11:20`);
    await p.fill('[data-edit-tags]', 'kite down');
    await p.click('[data-act="jrSave"]');
    await p.waitForSelector(`.jr-note:has-text("back at 11:20")`);
    assert.deepEqual(await p.locator('.jr-note', { hasText: text }).locator('.jr-tag').allInnerTexts(), ['kite down']);
    // delete asks once more
    await p.locator('.jr-note', { hasText: text }).locator('[data-act="jrAsk"]').click();
    await p.locator('.jr-note', { hasText: text }).locator('[data-act="jrDelete"]').click();
    await p.waitForFunction(t => ![...document.querySelectorAll('.jr-note')].some(n => n.innerText.includes(t)), text);
    const sw = await p.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(sw <= width, `page ${sw}px wide at ${width}px`);
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  });
}

test('notes on a deployment (Geek mode), on a straddle’s day and on one trade', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  const DD = IDS['BankNifty DTT Plain'], ST = IDS['Pivot Supertrend Options'];
  // Geek › Journal: a note on this deployment, for today
  await p.goto(`${BASE}/linck/#/d/${DD}/geek/journal`);
  await p.waitForSelector('.gk-rail a.on[href$="/geek/journal"]');
  await p.waitForSelector('#gkView [data-jr-body]');
  const own = `Stepped in by hand ${uniq()}`;
  await p.fill('#gkView [data-jr-body]', own);
  await p.fill('#gkView [data-jr-tags]', 'manual intervention');
  await p.click('#gkView .jr-new button[type=submit]');
  await p.waitForSelector(`#gkView .jr-note:has-text("${own}")`);
  // on one straddle: the note is that straddle's day
  const all = await api(ctx, `/v2/deployments/${DD}/geek/straddles`);
  const s = all.straddles.find(r => !r.open);
  await p.goto(`${BASE}/linck/#/d/${DD}/geek/straddle?s=${s.id}`);
  await p.waitForSelector('#gkView [data-notes-slot] [data-jr-body]');
  const onDay = `Expiry-day gamma ${uniq()}`;
  await p.fill('#gkView [data-jr-body]', onDay);
  await p.click('#gkView .jr-new button[type=submit]');
  await p.waitForSelector(`#gkView .jr-note:has-text("${onDay}")`);
  const got = (await api(ctx, `/v2/journal?deployment_id=${DD}&q=${encodeURIComponent(onDay)}`)).entries;
  assert.equal(got.length, 1);
  assert.equal(got[0].day, s.day, 'filed under the straddle’s own day');
  assert.equal(got[0].day_pnl, s.pnl, 'next to what it booked that day');
  // the Overview's daily P&L tooltip for that day carries the note
  await p.goto(`${BASE}/linck/#/d/${DD}/geek/overview`);
  await p.waitForFunction(t => [...document.querySelectorAll('#gkView [data-tip]')].some(e => e.getAttribute('data-tip').includes(t.slice(0, 20))), onDay, { timeout: 20000 });
  // on one trade
  const tr = (await api(ctx, `/v2/deployments/${ST}/geek/signals`)).trades[1];
  await p.goto(`${BASE}/linck/#/d/${ST}/geek/trade?p=${tr.id}`);
  await p.waitForSelector('#gkView [data-notes-slot] [data-jr-body]');
  const onTrade = `Should have waited for the close ${uniq()}`;
  await p.fill('#gkView [data-jr-body]', onTrade);
  await p.fill('#gkView [data-jr-tags]', 'lesson');
  await p.click('#gkView .jr-new button[type=submit]');
  await p.waitForSelector(`#gkView .jr-note:has-text("${onTrade}")`);
  assert.match(await p.locator('#gkView .jr-note', { hasText: onTrade }).innerText(), new RegExp(tr.symbol));
  // all three in the Journal, each with its deployment
  await p.goto(`${BASE}/linck/#/journal`);
  for (const [t, dep] of [[own, 'BankNifty DTT Plain'], [onDay, 'BankNifty DTT Plain'], [onTrade, 'Pivot Supertrend Options']]) {
    const n = p.locator('.jr-note', { hasText: t });
    await n.waitFor();
    assert.match(await n.innerText(), new RegExp(dep));
  }
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
