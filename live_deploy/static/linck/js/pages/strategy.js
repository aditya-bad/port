// One strategy (#/s/:name): what it is and how it has done across every
// deployment running it. All deployments are in view by default; tick any
// out (or pick Running / Not stopped) and every figure, chart and table
// follows: combined as one portfolio, the way Compare combines a group
// (data/groups.js). Then each deployment side by side, the shared
// performance stack, how trades ended, and the settings each one uses.

import { html, mount, actions } from '../core/dom.js';
import { cycleText, cycleTone, cycleTip, closedNote } from '../ui/cycle.js';
import { inr, inrS, pct, dFull, dShort, dMonthYear, plural, tone } from '../core/format.js';
import { pnlOf } from '../core/basis.js';
import { ui, listen } from '../core/store.js';
import { go, href } from '../core/router.js';
import * as book from '../data/book.js';
import { combine } from '../data/groups.js';
import { tradeMetrics, dailySeries, analytics, ddSeries, verdictRdd, fmtRatio, periodMetrics } from '../data/metrics.js';
import { deploymentsOf, selected, presets, presetOf, toggleOff, deploymentRows, settingsSpread } from '../data/strategy.js';
import { line } from '../ui/charts.js';
import { registerScrub } from '../ui/tooltip.js';
import { perfStack, insightsList } from '../ui/perfstack.js';
import { status, empty, check, seg } from '../ui/components.js';
import { say } from '../ui/toast.js';
import { newDeployment } from '../actions.js';
import { alertPrefs, flipKind } from '../ui/alertprefs.js';

const st = ui('strategy', { off: {}, each: false, hm: {}, cal: {}, setAll: false });

export default {
  title: () => 'Strategy',
  render(main, params) {
    const name = params.name;
    const key = `strat:${name}`;
    const off = () => st.get().off[name] || [];
    const setOff = ids => { const s = st.get(); st.set({ off: { ...s.off, [name]: ids } }); paint(); };

    function head(s, deps) {
      return html`
        <div class="dep-head pad-x">
          <a class="small b mute back" href="${href('strategies')}">‹ All strategies</a>
          <div class="row end wrap" style="gap:20px">
            <div class="col" style="gap:8px;min-width:0;max-width:820px">
              <div class="row wrap" style="gap:14px"><h1 class="dep-title">${s.label}</h1>${s.enabled === false ? html`<span class="tag">disabled</span>` : ''}</div>
              <div class="row wrap mute" style="gap:8px 16px">
                <span class="mono tiny">${s.name}</span><span>${s.mode === 'intraday' ? 'Intraday' : 'Positional'}</span><span>${s.index}</span>
                ${s.risk ? html`<span>Risk: ${s.risk}</span>` : ''}${s.hold ? html`<span>Hold: ${s.hold}</span>` : ''}
                <span>${deps.length ? `${plural(deps.length, 'deployment')} · ${deps.filter(v => v.rawStatus === 'active').length} running` : 'not deployed yet'}</span>
              </div>
              <p class="small mute pretty" style="margin:0">${s.description}</p>
            </div>
            <span class="grow"></span>
            <div class="row wrap" style="gap:8px">
              <button type="button" class="btn btn-primary" data-act="deploy" ${s.enabled === false ? html`aria-disabled="true" data-reason="This strategy is disabled on the server."` : ''}>Deploy</button>
              ${deps.length ? html`<button type="button" class="btn" data-act="compare">Compare these</button>` : ''}
              ${deps.length ? html`<a class="btn btn-quiet" href="${href(`deployments?strat=${encodeURIComponent(s.name)}`)}">In Deployments</a>` : ''}
            </div>
          </div>
        </div>`;
    }

    // Which deployments are in view.
    function picker(deps, sel) {
      const o = off(), cur = presetOf(deps, o);
      const cap = sel.reduce((a, v) => a + v.cap, 0);
      return html`<div class="st-pick">
        <div class="row wrap" style="gap:10px">
          <b>${sel.length === deps.length ? `All ${plural(deps.length, 'deployment')}` : `${sel.length} of ${plural(deps.length, 'deployment')}`}</b>
          <span class="mute small">${inrS(cap, { sign: false })} of capital, combined as one</span>
          <span class="grow"></span>
          <div class="chips">${presets(deps).map(([k, l]) => html`<button type="button" class="chip ${cur === k ? 'on' : ''}" data-act="preset" data-k="${k}" aria-pressed="${cur === k}">${l}</button>`)}</div>
        </div>
        <div class="pick-chips">${deps.map(v => {
          const on = !o.includes(v.id);
          return html`<button type="button" class="pick-chip ${on ? 'on' : ''}" data-act="pick" data-id="${v.id}" role="checkbox" aria-checked="${on}" title="${on ? 'In view: tap to leave it out' : 'Left out: tap to include'}">
            <i class="box ${on ? 'on' : ''}" aria-hidden="true"></i><span class="ell">${v.name}</span></button>`;
        })}</div>
      </div>`;
    }

    function strip(rec, sel) {
      const t = book.totals(sel), cap = rec.cap || 1;
      const m = periodMetrics({ trades: rec.trades, daily: rec.daily, cap: rec.cap, pnlOf, period: 'all' });
      const tm = tradeMetrics(rec.trades.map(pnlOf));
      const cells = [
        ['Overall P&L', inrS(t.overall), tone(t.overall), 'realized + open'],
        ['Cycle', inr(t.cycle), tone(t.cycle), 'current cycles, all in view'],
        ['Unrealized', inr(t.unreal), tone(t.unreal), plural(t.legs, 'open leg')],
        ['Return on capital', pct(t.overall / cap * 100), tone(t.overall), `on ${inrS(rec.cap, { sign: false })}`],
        ['Return / drawdown', m.ddp < 0 ? fmtRatio(m.rdd) : '—', '', m.ddp < 0 ? verdictRdd(m.rdd) : 'no drawdown yet'],
        ['Win rate', tm.n ? `${tm.win.toFixed(1)}%` : '—', '', plural(tm.n, 'closed trade')],
      ];
      return html`<div class="stats st-strip">${cells.map(([l, v, c, s]) => html`<div class="stat top"><span class="l">${l}</span><span class="v ${c}">${v}</span><span class="s">${s}</span></div>`)}</div>`;
    }

    function equity(rec, sel) {
      const from = rec.created, each = st.get().each;
      const days = dailySeries(rec.daily, pnlOf, from);
      if (!days.length) return empty('', 'Appears once a trade closes.');
      const cum = days.map(d => d.cum), dd = ddSeries(cum);
      const per = each ? sel.map(v => ({ v, d: dailySeries(v.daily, pnlOf, from) })) : [];
      const all = [...cum, ...per.flatMap(x => x.d.map(d => d.cum))];
      const top = Math.max(1, ...all), low = Math.min(0, ...all), ddLow = Math.min(-1, ...dd);
      registerScrub('steq', f => {
        const i = Math.round(f * (days.length - 1)), q = days[i];
        if (!q) return null;
        const lines = [dFull(q.date), `Combined  ${inr(q.cum)}`, `That day  ${inr(q.day)}`, `Below peak  ${inr(dd[i])}`];
        per.forEach(x => { const p = x.d[i]; if (p) lines.push(`${x.v.name}  ${inr(p.cum)}`); });
        return { t: lines.join('\n'), ys: [1 - (q.cum - low) / ((top - low) || 1)] };
      });
      registerScrub('stdd', f => {
        const i = Math.round(f * (days.length - 1)), q = days[i];
        return q ? { t: `${dFull(q.date)}\nBelow peak  ${inr(dd[i])}\nOf capital  ${pct(dd[i] / rec.cap * 100)}`, ys: [dd[i] / ddLow], cs: ['var(--r)'] } : null;
      });
      return html`
        ${line({ series: [...per.map(x => ({ values: x.d.map(d => d.cum), color: 'var(--line2)', width: 1.2, label: x.v.name })),
          { values: cum, color: 'var(--ink)', fill: 'var(--gs)', fillNeg: 'var(--rs)', fillTo: 0, label: 'Combined' }], h: 220, key: 'steq', lo: low, hi: top, zero: true })}
        ${line({ series: [{ values: dd, color: 'var(--r)', fill: 'var(--rs)', fillTo: 0, width: 1.5 }], h: 70, key: 'stdd', lo: ddLow, hi: 0, rule: false })}
        <div class="axis-row"><span>${dMonthYear(days[0].date)}</span><span>drawdown from peak, low ${inr(ddLow)}</span><span>${dMonthYear(new Date())}</span></div>`;
    }

    // Every deployment, included or not, with its own all-time figures;
    // the totals row is for the ones in view.
    function table(deps, sel, rec) {
      const o = off(), R = deploymentRows(deps, pnlOf, book.live);
      const t = book.totals(sel), tm = periodMetrics({ trades: rec.trades, daily: rec.daily, cap: rec.cap, pnlOf, period: 'all' });
      const row = ({ v, m, x }) => {
        const on = !o.includes(v.id);
        return html`<div class="tr ${on ? '' : 'out'}">
          <button type="button" class="box ${on ? 'on' : ''}" data-act="pick" data-id="${v.id}" role="checkbox" aria-checked="${on}" aria-label="${on ? 'Leave out' : 'Include'} ${v.name}"></button>
          <a class="cell-name" href="${href(`d/${v.id}`)}"><b>${v.name}</b><span>${v.mode} · since ${dShort(v.created)}</span></a>
          ${status(v.status)}
          <span class="num tiny">${inrS(v.cap, { sign: false })}</span>
          <span class="num tiny">${m.n || '—'}</span>
          <span class="num tiny">${m.n ? `${m.win.toFixed(0)}%` : '—'}</span>
          <span class="num col" style="gap:1px;align-items:flex-end" data-tip="${cycleTip(v, x)}"><span class="${cycleTone(x)}">${cycleText(x)}</span>${closedNote(x)}</span>
          <span class="num ${tone(x.overall)}">${inrS(x.overall)}</span>
          <span class="num tiny ${tone(x.ret)}">${pct(x.ret, 1)}</span>
          <span class="num tiny ${m.ddp < 0 ? 'neg' : ''}">${m.ddp < 0 ? pct(m.ddp, 1) : '—'}</span>
          <span class="num tiny">${m.ddp < 0 ? fmtRatio(m.rdd) : '—'}</span>
        </div>`;
      };
      return html`
        <div class="cards only-phone">${R.map(({ v, m, x }) => {
          const on = !o.includes(v.id);
          return html`<div class="card-row st-card ${on ? '' : 'out'}">
            <button type="button" class="box ${on ? 'on' : ''}" data-act="pick" data-id="${v.id}" role="checkbox" aria-checked="${on}" aria-label="${on ? 'Leave out' : 'Include'} ${v.name}"></button>
            <a class="col" style="gap:0;min-width:0;text-decoration:none" href="${href(`d/${v.id}`)}"><b class="ell">${v.name}</b><span class="tiny mute">${v.mode} · ${plural(m.n, 'trade')}${m.n ? ` · ${m.win.toFixed(0)}% won` : ''}</span></a>
            <span class="col right" style="gap:0"><span class="mono ${tone(x.overall)}">${inrS(x.overall)}</span><span class="tiny mute">${pct(x.ret, 1)}</span></span>
          </div>`;
        })}</div>
        <div class="hide-phone scroll-x">
          <div class="tbl st-tbl">
            <div class="tr th"><span></span><span>Deployment</span><span>Status</span><span class="right">Capital</span><span class="right">Trades</span><span class="right">Won</span>
              <span class="right">Cycle</span><span class="right">Overall</span><span class="right">Return</span><span class="right">Max DD</span><span class="right">R/DD</span></div>
            ${R.map(row)}
            <div class="tr tot"><span></span><b>${sel.length === deps.length ? 'All' : `${sel.length} in view`}, combined</b><span></span>
              <span class="num tiny">${inrS(rec.cap, { sign: false })}</span><span class="num tiny">${tm.n || '—'}</span><span class="num tiny">${tm.n ? `${tm.win.toFixed(0)}%` : '—'}</span>
              <span class="num ${tone(t.cycle)}">${inr(t.cycle)}</span><span class="num ${tone(t.overall)}">${inrS(t.overall)}</span>
              <span class="num tiny ${tone(t.overall)}">${pct(t.overall / (rec.cap || 1) * 100, 1)}</span>
              <span class="num tiny ${tm.ddp < 0 ? 'neg' : ''}">${tm.ddp < 0 ? pct(tm.ddp, 1) : '—'}</span><span class="num tiny">${tm.ddp < 0 ? fmtRatio(tm.rdd) : '—'}</span></div>
          </div>
        </div>
        <p class="tiny mute" style="margin:8px 0 0">Combined: P&L added up by day (positional cycles count on the day they close) and trades pooled, on the capital of the ones in view together, so its drawdown is the portfolio’s own. Ticked ones are in view; tap a box to leave one out.</p>`;
    }

    function exits(rec) {
      const er = {};
      rec.trades.forEach(t => { const r = t.exit_reason || 'unrecorded'; er[r] = er[r] || { n: 0, p: 0 }; er[r].n++; er[r].p += pnlOf(t); });
      const E = Object.entries(er).sort((p, q) => q[1].p - p[1].p);
      if (!E.length) return empty('', 'Appears once a trade closes.');
      const emax = Math.max(1, ...E.map(([, q]) => Math.abs(q.p)));
      return E.map(([r, q]) => html`
        <div class="exit-row" data-tip="${r}\nTrades  ${q.n} (${Math.round(q.n / (rec.trades.length || 1) * 100)}%)\nTotal  ${inr(q.p)}\nAverage  ${inr(q.p / q.n)}">
          <div class="row"><b class="grow">${r} <span class="mono mute" style="font-size:11px;font-weight:400">${plural(q.n, 'trade')}</span></b><span class="mono ${tone(q.p)}">${inr(q.p)}</span></div>
          <div class="meter"><i class="grow-x" style="width:${Math.abs(q.p) / emax * 100}%;background:${q.p >= 0 ? 'var(--pos)' : 'var(--r)'}"></i></div>
        </div>`);
    }

    // Each setting: its default and what the deployments in view use.
    function settings(s, sel) {
      const all = st.get().setAll;
      const S = settingsSpread(s.fields, sel), shown = all ? S : S.filter(x => x.differs || x.mixed);
      const fmt = (x, f) => `${f.prefix}${x}${f.suffix ? ` ${f.suffix}` : ''}`;
      return html`
        <div class="row wrap" style="gap:10px;padding-bottom:6px">
          <span class="mute small">${S.filter(x => x.differs).length ? `${plural(S.filter(x => x.differs).length, 'setting')} changed from the default by at least one deployment` : sel.length ? 'Every deployment in view uses the defaults' : ''}</span>
          <span class="grow"></span>${seg([['diff', 'Changed only'], ['all', 'All settings']], all ? 'all' : 'diff', 'setAll')}
        </div>
        ${shown.length ? html`<div class="tbl st-set">
          <div class="tr th"><span>Setting</span><span>Default</span><span>In use</span></div>
          ${shown.map(f => html`<div class="tr ${f.differs ? 'chg' : ''}">
            <span class="col" style="gap:0;min-width:0"><b>${f.label}</b><span class="tiny mute pretty">${f.help}</span></span>
            <span class="mono small st-def">${fmt(f.def, f)}</span>
            <span class="col" style="gap:2px;min-width:0">${f.values.map(x => html`<span class="small"><b class="mono ${x.v !== f.def ? 'warn' : ''}">${fmt(x.v, f)}</b> <span class="mute tiny" title="${x.names.join('\n')}">${sel.length > 1 ? (x.n === sel.length ? 'all' : `${x.n} of ${sel.length}: ${x.names.join(', ')}`) : ''}</span></span>`)}</span>
          </div>`)}
        </div>` : empty('', 'Nothing changed from the defaults. Show all settings to see them.')}`;
    }

    // Which alerts its deployments raise, set for all of them at once.
    const live = deps => deps.filter(v => v.rawStatus !== 'stopped');
    function alertsBlock(deps) {
      const L = live(deps);
      return html`<div><div class="sect ruled"><h3 class="t-h3">Alerts</h3><span class="mute small">${L.length ? `which ones its ${L.length > 1 ? `${L.length} deployments` : 'deployment'} raise · a switch sets all of them; each can differ on its own Settings tab` : 'nothing deployed and not stopped'}</span></div>
        ${L.length ? alertPrefs(L, { act: 'alertKind' }) : ''}</div>`;
    }

    function paint() {
      if (!book.isLoaded('strategies', 'deployments')) { mount(main, html`<div class="page"><div class="skel" style="height:300px"></div></div>`); return; }
      const s = book.strategy(name);
      if (!s) { mount(main, html`<div class="page">${empty('No such strategy', html`<a href="${href('strategies')}">See every strategy</a>`)}</div>`); return; }
      const deps = deploymentsOf(book.deployments(), name);
      // forget left-out ids that no longer exist
      const o = off().filter(id => deps.some(v => v.id === id));
      if (o.length !== off().length) { const x = st.get(); st.set({ off: { ...x.off, [name]: o } }); }
      if (!deps.length) {
        mount(main, html`${head(s, deps)}<div class="pad-x tab-body stack" style="gap:30px">
          ${empty('Not deployed yet', 'Deploy it to see its results here, combined across every deployment.', html`<button type="button" class="btn btn-primary" data-act="deploy">Deploy ${s.label}</button>`)}
          <div><div class="sect ruled"><h3 class="t-h3">Settings</h3><span class="mute small">the defaults a new deployment starts from</span></div>${settings(s, [])}</div>
        </div>`);
        return;
      }
      const sel = selected(deps, o);
      const rec = combine(sel, s.label);
      const x = st.get(), hm = x.hm[key] || 'pct', cal = x.cal[key] || 'last';
      const days = dailySeries(rec.daily, pnlOf, rec.created);
      const a = analytics(days, rec.trades.map(t => ({ date: new Date(t.closed_at), pnl: pnlOf(t) })), { cap: rec.cap, pctMode: hm === 'pct', calRange: cal });
      mount(main, html`
        ${head(s, deps)}
        <div class="pad-x tab-body stack" style="gap:30px">
          ${picker(deps, sel)}
          ${strip(rec, sel)}
          <div>
            <div class="sect"><h3 class="t-h3">Equity${sel.length > 1 ? ', combined' : ''}</h3><span class="mute small">cumulative realized since ${dShort(rec.created)}</span>
              <span class="grow"></span>${sel.length > 1 ? check(x.each, 'Show each deployment', 'each') : ''}</div>
            ${equity(rec, sel)}
          </div>
          <div><div class="sect ruled"><h3 class="t-h3">Deployments</h3><span class="mute small">each on its own, all-time · open one for its full page</span></div>${table(deps, sel, rec)}</div>
          ${rec.trades.length || rec.daily.length ? perfStack(a, { key, hm, cal }) : ''}
          <div class="grid-two">
            <div><div class="sect"><h3 class="t-h3">How trades ended</h3></div>${exits(rec)}</div>
            <div><div class="sect"><h3 class="t-h3">What stands out</h3></div>${insightsList(a.insights)}</div>
          </div>
          <div><div class="sect ruled"><h3 class="t-h3">Settings in use</h3><span class="mute small">the default, and what the deployments in view use</span></div>${settings(s, sel)}</div>
          ${alertsBlock(deps)}
        </div>`);
    }

    const offA = actions(main, {
      deploy: (d, el) => (el.dataset.reason ? say(el.dataset.reason) : newDeployment(name)),
      compare: () => {
        const sel = selected(deploymentsOf(book.deployments(), name), off());
        if (sel.length < 2) return say('Compare needs at least 2 deployments in view.');
        // up to 5 side by side; more than that, compare them as one against nothing else isn't useful
        if (sel.length > 5) return say('Compare takes up to 5. Leave some out here first, or use Compare’s Combine.');
        go(`compare?ids=${sel.map(v => v.id).join(',')}`);
      },
      pick: d => setOff(toggleOff(deploymentsOf(book.deployments(), name), off(), d.id)),
      preset: d => setOff((presets(deploymentsOf(book.deployments(), name)).find(p => p[0] === d.k) || [, , []])[2]),
      each: () => { st.set({ each: !st.get().each }); paint(); },
      setAll: d => { st.set({ setAll: d.v === 'all' }); paint(); },
      hm: d => { const s = st.get(); st.set({ hm: { ...s.hm, [key]: d.v } }); paint(); },
      cal: d => { const s = st.get(); st.set({ cal: { ...s.cal, [key]: d.v } }); paint(); },
      alertKind: d => flipKind(live(deploymentsOf(book.deployments(), name)), d.kind),
    });
    paint();
    const offs = [listen('data', paint)];
    return () => { offA(); offs.forEach(f => f()); };
  },
};
