// Reports — statement-style, realized only. Step through days, weeks,
// months, years or all-time against the previous period; then who made
// the money, account-wide analytics, equity, capital use and exposure.
// Deployments set not to count in reports are left out.

import { html, raw, mount, actions } from '../core/dom.js';
import { inr, inrS, pct, MONTHS, WEEKDAYS, ist, dayKey, fromDay, dMonthYear, plural, tone } from '../core/format.js';
import { pnlOf } from '../core/basis.js';
import { ui, listen } from '../core/store.js';
import { go, href } from '../core/router.js';
import * as book from '../data/book.js';
import { dailySeries, analytics } from '../data/metrics.js';
import { seg, utabs, empty } from '../ui/components.js';
import { line, centreBar } from '../ui/charts.js';
import { registerScrub } from '../ui/tooltip.js';
import { perfStack, insightsList } from '../ui/perfstack.js';
import { repairsLine } from '../ui/repairs.js';

const st = ui('reports', { step: 'month', off: 0, group: 'dep', hm: 'abs', cal: 'last' });
const SEG = ['var(--ink)', 'var(--b)', 'var(--wr)', 'var(--c4)', 'var(--c5)', 'var(--mute)'];
const key = d => dayKey(d);
const addDays = (k, n) => key(new Date(fromDay(k).getTime() + n * 864e5));

// [startKey, endKey) in IST calendar days, plus a label.
function range(step, off) {
  const today = key(new Date()), t = ist(new Date());
  if (step === 'all') return off ? null : { a: '0000-00-00', b: '9999-99-99', label: 'All-time' };
  if (step === 'year') { const y = t.y - off; return { a: `${y}-01-01`, b: `${y + 1}-01-01`, label: `${y}${off ? '' : ' to date'}` }; }
  if (step === 'month') {
    const mo = t.mo - off, y = t.y + Math.floor(mo / 12), m = ((mo % 12) + 12) % 12;
    const a = `${y}-${String(m + 1).padStart(2, '0')}-01`;
    const ny = m === 11 ? y + 1 : y, nm = m === 11 ? 0 : m + 1;
    return { a, b: `${ny}-${String(nm + 1).padStart(2, '0')}-01`, label: `${MONTHS[m]} ${y}` };
  }
  if (step === 'week') {
    const mon = addDays(today, -((t.wd + 6) % 7) - 7 * off), end = addDays(mon, 7), p = ist(fromDay(mon)), q = ist(fromDay(addDays(mon, 4)));
    return { a: mon, b: end, label: `${p.d} ${MONTHS[p.mo]} – ${q.d} ${MONTHS[q.mo]}` };
  }
  const weekend = k => [0, 6].includes(ist(fromDay(k)).wd);
  let k = today;
  while (weekend(k)) k = addDays(k, -1);
  for (let n = 0; n < off; n++) { k = addDays(k, -1); while (weekend(k)) k = addDays(k, -1); }
  const p = ist(fromDay(k));
  return { a: k, b: addDays(k, 1), label: `${WEEKDAYS[p.wd]} ${p.d} ${MONTHS[p.mo]}` };
}

export default {
  title: 'Reports',
  render(main) {
    function paint() {
      if (!book.isLoaded('deployments', 'book')) { mount(main, html`<div class="page"><div class="skel" style="height:300px"></div></div>`); return; }
      const s = st.get();
      const all = book.deployments().filter(v => v.inReports);
      const cur = range(s.step, s.off), prev = range(s.step, s.off + 1);
      const inR = (k, r) => r && k >= r.a && k < r.b;
      const sumOf = (v, r) => {
        let p = 0, n = 0;
        v.daily.forEach(d => { if (inR(d.day, r)) p += pnlOf(d); });
        v.trades.forEach(t => { if (inR(key(t.closed_at), r)) n++; });
        return { p, n };
      };
      const per = all.map(v => ({ v, c: sumOf(v, cur), pr: sumOf(v, prev) }));
      const groups = s.group === 'strat'
        ? Object.values(per.reduce((acc, x) => {
          const k = x.v.strategy;
          acc[k] = acc[k] || { n: (book.strategy(k) || { label: k }).label, k, c: { p: 0, n: 0 }, pr: { p: 0, n: 0 } };
          acc[k].c.p += x.c.p; acc[k].c.n += x.c.n; acc[k].pr.p += x.pr.p;
          return acc;
        }, {}))
        : per.map(x => ({ n: x.v.name, id: x.v.id, c: x.c, pr: x.pr }));
      groups.sort((p, q) => q.c.p - p.c.p);
      const tot = groups.reduce((a, g) => ({ c: { p: a.c.p + g.c.p, n: a.c.n + g.c.n }, pr: { p: a.pr.p + g.pr.p } }), { c: { p: 0, n: 0 }, pr: { p: 0 } });
      const winD = per.filter(x => x.c.p > 0).length;
      const best = per.reduce((m, x) => (x.c.p > (m ? m.c.p : 0) ? x : m), null);
      const hasPrev = !!prev;

      // Who made the money
      const CB = per.filter(x => x.c.p).sort((a, b) => b.c.p - a.c.p);
      const cbL = CB.length > 12 ? [...CB.slice(0, 8), ...CB.slice(-4)] : CB;
      const cmax = Math.max(1, ...CB.map(x => Math.abs(x.c.p))), cneg = CB.some(x => x.c.p < 0), split = cneg ? 30 : 0;
      const posSum = CB.filter(x => x.c.p > 0).reduce((a, x) => a + x.c.p, 0), top3 = CB.slice(0, 3).reduce((a, x) => a + Math.max(0, x.c.p), 0);

      // Account-wide analytics
      const first = all.length ? new Date(Math.min(...all.map(v => v.created.getTime()))) : new Date();
      const allDaily = all.flatMap(v => v.daily);
      const days = dailySeries(allDaily, pnlOf, first);
      const capAll = all.reduce((a, v) => a + v.cap, 0) || 1;
      const trades = all.flatMap(v => v.trades.map(t => ({ date: new Date(t.closed_at), pnl: pnlOf(t) }))).sort((a, b) => a.date - b.date);
      const a = analytics(days, trades, { cap: capAll, pctMode: s.hm === 'pct', calRange: s.cal });
      const eq = days.map(d => d.cum);
      registerScrub('acct', f => {
        const q = days[Math.round(f * (days.length - 1))];
        return q ? { t: `${q.date.getDate ? `${ist(q.date).d} ${MONTHS[ist(q.date).mo]} ${ist(q.date).y}` : ''}\nAccount equity  ${inr(q.cum)}\nClosed that day  ${inr(q.day)}`, ys: [1 - (q.cum - Math.min(0, ...eq)) / ((Math.max(1, ...eq) - Math.min(0, ...eq)) || 1)] } : null;
      });

      // Capital and exposure (live)
      const held = all.filter(v => v.legs.length);
      const dep = held.reduce((x, v) => x + v.cap, 0);
      const idle = all.filter(v => !v.legs.length && v.rawStatus === 'active').reduce((x, v) => x + v.cap, 0);
      const off = capAll - dep - idle;
      const byIx = {};
      held.forEach(v => { (byIx[v.index] = byIx[v.index] || []).push(v); });
      const expo = Object.entries(byIx).map(([ix, L]) => {
        const c = L.reduce((x, v) => x + v.cap, 0), sh = c / (dep || 1) * 100;
        const shorts = L.filter(v => v.legs.some(l => l.side === 'short')).length;
        return { ix, L, c, sh, warn: sh > 45 && shorts >= 3, shorts };
      }).sort((x, y) => y.c - x.c);

      mount(main, html`
        <div class="page-head">
          <div class="col"><h1 class="t-h1">Reports</h1><span class="mute">Realized P&L only. Open positions count when they close.</span></div>
          <span class="grow"></span>
          ${seg([['day', 'Day'], ['week', 'Week'], ['month', 'Month'], ['year', 'Year'], ['all', 'All-time']], s.step, 'step', 'lg')}
          <div class="row" style="gap:6px">
            <button type="button" class="btn btn-sm" data-act="prev" aria-label="Previous period" ${hasPrev && s.off < 60 ? '' : html`aria-disabled="true"`}>‹</button>
            <span class="t-h4 center" style="min-width:170px">${cur.label}</span>
            <button type="button" class="btn btn-sm" data-act="next" aria-label="Next period" ${s.off > 0 ? '' : html`aria-disabled="true"`}>›</button>
          </div>
        </div>
        ${(() => { const l = repairsLine(all, cur.a, cur.b); return l ? html`<div style="margin:0 var(--px) 12px">${l}</div>` : ''; })()}
        <div class="strip" style="grid-template-columns:var(--g-4);margin:0 var(--px)">
          <div class="stat"><span class="l">Realized P&L</span><span class="v ${tone(tot.c.p)}">${inr(tot.c.p)}</span><span class="s">${hasPrev ? `vs ${inr(tot.pr.p)} previous` : 'since the first deployment'}</span></div>
          <div class="stat"><span class="l">Change</span><span class="v ${hasPrev ? tone(tot.c.p - tot.pr.p) : ''}">${hasPrev ? inr(tot.c.p - tot.pr.p) : '—'}</span><span class="s">${hasPrev && tot.pr.p ? `${pct((tot.c.p - tot.pr.p) / Math.abs(tot.pr.p) * 100, 0)} on previous` : ''}</span></div>
          <div class="stat"><span class="l">Trades closed</span><span class="v">${tot.c.n}</span><span class="s">${winD} of ${plural(all.length, 'deployment')} in profit</span></div>
          <div class="stat"><span class="l">Best deployment</span><span class="v pos">${best ? inr(best.c.p) : '—'}</span><span class="s">${best ? best.v.name : 'no profitable deployment'}</span></div>
        </div>
        <div class="pad-x row" style="padding-top:22px;padding-bottom:10px"><h3 class="t-h3 grow">Statement</h3><span class="label">Group by</span>${utabs([['dep', 'Deployment'], ['strat', 'Strategy']], s.group, 'group')}</div>
        <div class="pad-x scroll-x">
          <div class="tr th stmt"><span>${s.group === 'strat' ? 'Strategy' : 'Deployment'}</span><span class="right">${cur.label}</span><span class="right">${hasPrev ? 'Previous' : '—'}</span><span class="right">Change</span><span class="right">Trades</span></div>
          ${groups.map(g => html`<a class="tr click stmt mono small" href="${g.id ? href(`d/${g.id}/performance`) : href(`deployments?strat=${encodeURIComponent(g.k)}`)}">
            <span class="b" style="font-family:var(--f-body);font-size:14px">${g.n}</span><span class="right ${tone(g.c.p)}">${inr(g.c.p)}</span>
            <span class="right mute">${hasPrev ? inr(g.pr.p) : '—'}</span><span class="right ${hasPrev ? tone(g.c.p - g.pr.p) : ''}">${hasPrev ? inr(g.c.p - g.pr.p) : '—'}</span><span class="right mute">${g.c.n}</span></a>`)}
          <div class="tr tot stmt mono"><span style="font-family:var(--f-body);font-weight:800">Total</span><span class="right ${tone(tot.c.p)}">${inr(tot.c.p)}</span>
            <span class="right mute">${hasPrev ? inr(tot.pr.p) : '—'}</span><span class="right ${hasPrev ? tone(tot.c.p - tot.pr.p) : ''}">${hasPrev ? inr(tot.c.p - tot.pr.p) : '—'}</span><span class="right">${tot.c.n}</span></div>
        </div>
        <div class="pad-x rule stack" style="margin-top:24px;padding-top:30px;gap:36px">
          <div class="grid-two">
            <div>
              <div class="sect"><h3 class="t-h3">Who made the money</h3><span class="mute small">${cur.label} · realized, by deployment</span></div>
              ${cbL.length ? cbL.map(x => html`<a class="contrib" href="${href(`d/${x.v.id}/performance`)}" data-tip="${x.v.name}\nRealized  ${inr(x.c.p)}\nTrades  ${x.c.n}${x.c.p > 0 && posSum ? `\nShare of gains  ${Math.round(x.c.p / posSum * 100)}%` : ''}">
                <span class="small ell" style="font-weight:600">${x.v.name}</span>${centreBar(x.c.p, cmax, { split })}<span class="mono small right ${tone(x.c.p)}">${inrS(x.c.p)}</span></a>`)
                : empty('', 'No closed trades in this period.')}
              <p class="small mute" style="margin:10px 0 0">${CB.length > 12 ? `Top 8 and bottom 4 of ${CB.length} deployments. ` : ''}${posSum ? `The top 3 made ${Math.round(top3 / posSum * 100)}% of this period’s gains.` : ''}</p>
            </div>
            <div><div class="sect"><h3 class="t-h3">What stands out</h3></div>${a.insights.length ? insightsList(a.insights) : empty('', 'Nothing yet.')}</div>
          </div>
          ${days.length ? perfStack(a, { key: 'rep', hm: s.hm, cal: s.cal, title: 'Account quality, all-time' }) : ''}
        </div>
        <div class="pad-x rule grid-side" style="margin-top:36px;padding-top:30px;padding-bottom:var(--px)">
          <div>
            <div class="sect"><h3 class="t-h3">Account equity</h3><span class="mute small">all deployments combined · ${inrS(eq.length ? eq[eq.length - 1] : 0)}</span></div>
            ${eq.length > 1 ? line({ series: [{ values: eq, color: 'var(--ink)', fill: 'var(--gs)', fillNeg: 'var(--rs)', fillTo: 0 }], h: 220, key: 'acct', zero: true }) : empty('', 'No closed trades yet.')}
            <div class="axis-row"><span>${days.length ? dMonthYear(days[0].date) : ''}</span><span>${dMonthYear(new Date())}</span></div>
            <h3 class="t-h3" style="padding:28px 0 10px">Capital</h3>
            <div class="capbar">${[[dep, 'var(--ink)', 'Holding positions'], [idle, 'var(--y)', 'Running, flat'], [off, 'var(--soft)', 'Paused or stopped']].map(([v, bg, l]) => html`<i class="grow-x" data-tip="${l}\n${inrS(v, { sign: false })}" style="width:${v / capAll * 100}%;background:${bg}"></i>`)}</div>
            <div class="row wrap" style="gap:24px;padding-top:10px">${[[dep, 'var(--ink)', 'Holding positions'], [idle, 'var(--y)', 'Running, flat'], [off, 'var(--soft)', 'Paused or stopped']].map(([v, bg, l]) => html`
              <span class="row small" style="gap:8px"><i style="width:12px;height:12px;background:${bg};border:1px solid var(--ink)"></i><b>${l}</b><span class="mono mute">${inrS(v, { sign: false })} · ${Math.round(v / capAll * 100)}%</span></span>`)}</div>
          </div>
          <div>
            <div class="sect ruled"><h3 class="t-h3">Index exposure</h3><span class="mute small">capital holding positions, by index</span></div>
            ${expo.length ? expo.map(e => html`<div class="expo">
              <div class="row base"><b class="mono small">${e.ix}</b><span class="tiny mute">${plural(e.L.length, 'deployment')} · ${inrS(e.c, { sign: false })}</span><span class="grow"></span><span class="mono small">${e.sh.toFixed(0)}% of deployed</span></div>
              <div class="expo-bar">${e.L.map((v, i) => html`<i data-tip="${v.name}\n${inrS(v.cap, { sign: false })}" style="width:${v.cap / e.c * 100}%;background:${SEG[i % SEG.length]}"></i>`)}</div>
              ${e.warn ? html`<span class="err">${e.shorts} deployments are short ${e.ix} options at once. A sharp move hits them together.</span>` : ''}
            </div>`) : empty('', 'Nothing is open right now.')}
          </div>
        </div>`);
    }
    const off = actions(main, {
      step: d => { st.set({ step: d.v, off: 0 }); paint(); },
      prev: () => { st.set({ off: st.get().off + 1 }); paint(); },
      next: () => { const s = st.get(); if (s.off > 0) { st.set({ off: s.off - 1 }); paint(); } },
      group: d => { st.set({ group: d.v }); paint(); },
      hm: d => { st.set({ hm: d.v }); paint(); },
      cal: d => { st.set({ cal: d.v }); paint(); },
    });
    paint();
    const offs = [listen('data', paint)];
    return () => { off(); offs.forEach(f => f()); };
  },
};
