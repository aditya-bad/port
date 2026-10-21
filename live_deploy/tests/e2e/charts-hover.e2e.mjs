// Every chart in Geek mode is interactable: hovering it shows what's
// under the pointer. A line chart has a guide (data-scrub) that gives
// every series' value; a chart of separate marks puts a tip (data-tip)
// on each one. This walks every view of every kind of deployment, so a
// chart added later without either fails here.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context } from './helper.mjs';

const KINDS = {
  'Nifty Monthly Strangle': ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'map', 'cycles', 'cycle', 'adjustments', 'premium', 'entries', 'settings'],
  'Pivot Supertrend Options': ['overview', 'engine', 'decisions', 'signals', 'trade', 'exits', 'days', 'settings'],
  'BankNifty DTT Plain': ['overview', 'engine', 'decisions', 'straddles', 'straddle', 'timing', 'settings'],
  'Sensex Credit Spread': ['overview', 'engine', 'decisions', 'settings'],
  'BankNifty DTT Adjusted': ['engine', 'sessions', 'session', 'moves'],
  'BankNifty DTT Advanced': ['engine', 'sessions', 'session', 'moves'],
};
let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

// Charts in the view: SVGs drawn as charts (not icons), and every one
// either scrubs, carries tips on its marks, or sits in an element that does.
const audit = p => p.evaluate(() => [...document.querySelectorAll('#gkView svg')]
  .filter(s => s.getBoundingClientRect().width > 120 && s.getBoundingClientRect().height > 40)
  .map((s, i) => {
    s.dataset.audit = String(i);
    const scrub = s.hasAttribute('data-scrub') || !!s.closest('[data-scrub]');
    const tips = s.querySelectorAll('[data-tip]').length + (s.closest('[data-tip]') ? 1 : 0);
    return { i, label: s.getAttribute('aria-label') || s.closest('.gk-panel')?.querySelector('h3')?.innerText?.split('\n')[0] || '(unlabelled)', scrub, tips };
  }));

for (const [name, views] of Object.entries(KINDS)) {
  test(`every chart is interactable: ${name}`, async () => {
    const ctx = await context(b, { width: 1440, height: 900 });
    const p = await ctx.newPage();
    let charts = 0;
    for (const v of views) {
      await p.goto(`${BASE}/linck/#/d/${IDS[name]}/geek/${v}`);
      await p.waitForFunction(() => {
        const el = document.querySelector('#gkView');
        return el && el.children.length && !el.querySelector('.skel');
      }, null, { timeout: 20000 });
      await p.waitForTimeout(400);
      const found = await audit(p);
      for (const c of found) {
        assert.ok(c.scrub || c.tips, `${name} / ${v}: “${c.label}” shows nothing on hover`);
        // hover it: the scrubbed point, or its first mark
        const sel = `#gkView svg[data-audit="${c.i}"]`;
        await p.locator(sel).scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => {});
        await p.waitForTimeout(80);
        if (c.scrub) {
          const box = await p.locator(sel).boundingBox({ timeout: 3000 }).catch(() => null)
            || await p.evaluate(i => { const all = [...document.querySelectorAll('#gkView svg[data-scrub]')]; const r = all[i] && all[i].getBoundingClientRect(); return r ? { x: r.left, y: r.top, width: r.width, height: r.height } : null; }, found.filter(f => f.scrub).indexOf(c));
          await p.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5);
        } else {
          // (views redraw on a timer: aim at where the mark is, don't hold on to it)
          const at = await p.evaluate(([q, i]) => {
            // redrawn since the audit: the same chart is the i-th one again
            const all = [...document.querySelectorAll('#gkView svg')].filter(x => x.getBoundingClientRect().width > 120 && x.getBoundingClientRect().height > 40);
            const svg = document.querySelector(q) || all[i];
            if (!svg) return null;
            const m = svg.querySelector('[data-tip]') || svg, r = m.getBoundingClientRect();
            // a point on the mark that nothing else covers
            for (const [fx, fy] of [[0.5, 0.5], [0.25, 0.3], [0.75, 0.7], [0.5, 0.2], [0.5, 0.8]]) {
              const x = r.left + r.width * fx, y = r.top + r.height * fy, e = document.elementFromPoint(x, y);
              if (e && e.closest('[data-tip]')) return [x, y];
            }
            return [r.left + r.width / 2, r.top + r.height / 2];
          }, [sel, c.i]);
          assert.ok(at, `${name} / ${v}: “${c.label}” disappeared`);
          await p.mouse.move(at[0], at[1]);
        }
        const shown = await p.waitForFunction(() => {
          const t = document.querySelector('.tip');
          return t && t.style.display !== 'none' && t.innerText.trim().length > 0 ? t.innerText : false;
        }, null, { timeout: 2500 }).then(h => h.jsonValue()).catch(() => '');
        assert.ok(shown, `${name} / ${v}: hovering “${c.label}” showed no details`);
        assert.doesNotMatch(shown, /undefined|NaN|\[object/, `${name} / ${v}: “${c.label}” tip: ${shown}`);
        await p.mouse.move(2, 2);
        charts++;
      }
    }
    assert.ok(charts >= 1, `${name}: found ${charts} charts`);
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  });
}
