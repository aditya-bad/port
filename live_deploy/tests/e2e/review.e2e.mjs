// Week in review: the week against the usual one, day by day and
// deployment by deployment; older and newer weeks; every bar shows its
// numbers; reachable from the header and, on a phone, from More.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, browser, context } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

for (const [width, label] of [[1440, 'desktop'], [390, 'phone']]) {
  test(`the week in review (${label})`, async () => {
    const ctx = await context(b, { width, height: 900 });
    const api = await (await ctx.request.get(`${BASE}/v2/review`)).json();
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/desk`);
    if (width > 900) await p.click('.hdr .nav a[href="#/review"]');
    else { await p.click('#bnav [data-act="more"]'); await p.getByText('Week in review', { exact: true }).click(); }
    await p.waitForURL(/#\/review/);
    await p.waitForSelector('.rv-stats');
    const text = await p.innerText('#main');
    assert.match(text, /Week in review/);
    assert.match(text, /P&L booked/);
    // the total is what the day bars add up to
    assert.equal(Math.round(api.days.reduce((a, d) => a + d.pnl, 0)), Math.round(api.totals.pnl));
    // each deployment that booked or traded is listed
    assert.equal(await p.locator('.rv-tbl a.tr').count(), api.deployments.length);
    // every bar has a tip; hovering one shows the day
    const tips = await p.$$eval('.rv-bars .hit', hs => hs.map(h => h.dataset.tip));
    assert.equal(tips.length, api.days.length + 9);
    const bar = p.locator('.rv-bars').first().locator('.hit').first();
    await bar.scrollIntoViewIfNeeded();
    const box = await bar.boundingBox();
    await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await p.waitForFunction(() => /P&L/.test(document.querySelector('.tip')?.innerText || ''));
    // an older week, then back
    if (api.prev) {
      await p.click('[data-act="wk"]:has-text("Older")');
      await p.waitForURL(new RegExp(`review\\?w=${api.prev}`));
      await p.waitForFunction(() => !document.querySelector('.skel') && document.querySelector('.rv-stats'));
      await p.click('[data-act="wk"]:has-text("Newer")');
      await p.waitForURL(new RegExp(`review\\?w=${api.week}`));
    }
    assert.equal(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll');
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  });
}
