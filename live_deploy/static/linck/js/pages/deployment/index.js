// One deployment: header with actions on every tab, a five-figure strip,
// and tabs Now · Performance · Cycles · Trades & replay · Variations · Settings.
// Each tab is its own module exporting render(el, v) → cleanup.

import { html, mount, $, actions, on, setText, setTone } from '../../core/dom.js';
import { inr, inrFit, pct, dShort, plural, tone } from '../../core/format.js';
import { current as basisNow, pnlOf } from '../../core/basis.js';
import { listen } from '../../core/store.js';
import { go, href } from '../../core/router.js';
import * as book from '../../data/book.js';
import { periodMetrics, verdictRdd, fmtRatio } from '../../data/metrics.js';
import { status, tagChip, empty } from '../../ui/components.js';
import { repairChip } from '../../ui/repairs.js';
import { cycleWhat, cycleTip, closedToday } from '../../ui/cycle.js';
import { say } from '../../ui/toast.js';
import * as A from '../../actions.js';
import { supports as geekOk, enter as enterGeek } from '../../data/geek.js';

export const TABS = v => [
  ['now', 'Now', () => import('./now.js')],
  ['performance', 'Performance', () => import('./performance.js')],
  ['cycles', 'Cycles', () => import('./days.js')],
  ['trades', 'Trades & replay', () => import('./trades.js')],
  ['variations', v.variations || v.variationOf ? `Variations · ${book.family(v.id).length - 1}` : 'Variations', () => import('./variations.js')],
  ['settings', 'Settings', () => import('./settings.js')],
];

function strip(v) {
  const x = book.live(v);
  const m = periodMetrics({ trades: v.trades, daily: v.daily, cap: v.cap, pnlOf, period: 'all' });
  return [
    ['Cycle', x.cycle == null ? '—' : inrFit(x.cycle), x.cycle == null ? 'flat' : tone(x.cycle),
      `${cycleWhat(v, x)}${closedToday(x) != null ? ` · closed today ${inr(closedToday(x))}` : ''} · ${basisNow().l.toLowerCase()}`, 'today', cycleTip(v, x)],
    ['Overall', inrFit(x.overall), tone(x.overall), plural(v.trades.length, 'trade'), 'overall'],
    ['Unrealized', inrFit(x.unreal), tone(x.unreal), plural(v.legs.length, 'open leg'), 'unreal'],
    ['Return on capital', pct(x.ret), tone(x.ret), 'all-time', 'ret'],
    ['Return / drawdown', m.ddp < 0 ? fmtRatio(m.rdd) : '—', '', m.ddp < 0 ? verdictRdd(m.rdd) : 'no drawdown yet', ''],
  ];
}

export default {
  title: () => 'Deployment',
  render(main, params) {
    const id = params.id;
    let menu = false, tabCleanup = null, tabKey = null;

    function header(v) {
      const [act] = A.primary(v);
      const stopped = v.rawStatus === 'stopped';
      return html`
        <div class="dep-head pad-x">
          <a class="small b mute back" href="${href('deployments')}">‹ All deployments</a>
          <div class="row end wrap" style="gap:20px">
            <div class="col" style="gap:8px;min-width:0">
              <div class="row wrap" style="gap:14px"><h1 class="dep-title">${v.name}</h1>${status(v.status)}${repairChip(v)}
                ${v.variations ? html`<a class="chip var-chip" href="${href(`d/${v.id}/variations`)}" data-var-chip>+${plural(v.variations, 'variation')}</a>` : ''}</div>
              ${v.variationOf ? html`<div class="small var-of" data-var-of>Variation of <a href="${href(`d/${v.variationOf}/variations`)}"><b>${(book.find(v.variationOf) || { name: 'its primary' }).name}</b></a> · ${v.variationLabel || ''}
                <button type="button" class="btn btn-xs" data-act="makePrimary">Make primary</button></div>` : ''}
              <div class="row wrap mute" style="gap:8px 16px">
                <a class="mono tiny" href="${href(`s/${v.strategy}`)}" title="${v.strategyLabel}: every deployment of it, combined">${v.strategy}</a><span>${v.mode}</span><span>${v.index}</span>
                <span class="mono small">${inr(v.cap, { sign: false })} capital</span>
                <span>${v.ageDays ? `running since ${dShort(v.created)}` : 'created today'}</span>
                ${v.tags.map(t => html`<button type="button" class="tag" data-act="untag" data-t="${t}" title="Remove tag ${t}">${t} ×</button>`)}
                <button type="button" class="tag add" data-act="tag">+ Tag</button>
              </div>
              ${v.raw.notes ? html`<p class="small mute pretty dep-notes" style="margin:0;max-width:760px">${v.raw.notes}</p>` : ''}
            </div>
            <span class="grow"></span>
            <div class="row wrap menu-wrap" style="gap:8px">
              <button type="button" class="btn btn-primary" data-act="primary">${act}</button>
              <button type="button" class="btn" data-act="close" ${v.legs.length && !stopped ? '' : html`aria-disabled="true" data-reason="${stopped ? 'Stopped deployments can’t place orders.' : 'No open legs to close.'}"`}>Close positions…</button>
              <button type="button" class="btn" data-act="stop" ${stopped ? html`aria-disabled="true" data-reason="Already stopped."` : ''}>Stop…</button>
              <button type="button" class="btn btn-quiet hide-1180" data-act="edit">Edit settings</button>
              ${!stopped ? html`<button type="button" class="btn btn-quiet" data-act="copy">Copy as new</button>` : ''}
              ${geekOk(v) ? html`<button type="button" class="btn btn-quiet btn-geek" data-act="geek" title="Everything about this strategy, full screen (G)">Geek mode <kbd class="hide-phone">G</kbd></button>` : ''}
              <button type="button" class="btn btn-quiet" style="font-weight:800" data-act="menu" aria-haspopup="menu" aria-expanded="${menu}" aria-label="More actions">···</button>
              ${menu ? html`<div class="menu" role="menu" style="width:280px">${[
                ['Add tag…', 'Tags group deployments in lists and filters', 'tag', false],
                ['Edit settings', v.rawStatus === 'active' ? 'Pause first so a change can’t land mid-trade' : 'Change what it trades and its limits', 'edit', false],
                ['Remove current trade…', v.legs.length ? 'Erase the open cycle from the record. Irreversible.' : 'No open cycle', 'removeTrade', !v.legs.length],
                ['Delete deployment…', stopped ? 'Removes it and all its history' : 'Stop it first', 'delete', !stopped],
              ].map(([l, h, a, off]) => html`<button type="button" role="menuitem" data-act="${a}" ${off ? html`aria-disabled="true" data-reason="${h}"` : ''}><b>${l}</b><span>${h}</span></button>`)}</div>` : ''}
            </div>
          </div>
          ${v.issue && v.issue.down ? html`
            <div class="issue">
              <span class="dia" style="width:12px;height:12px;background:var(--r)"></span>
              <div class="col grow" style="gap:0"><b>Not running: its strategy won’t enter, adjust or exit</b><span class="small mute">${v.issue.reason}.${v.legs.length ? ` ${plural(v.legs.length, 'leg')} will stay open until it runs again.` : ''}</span></div>
              <button type="button" class="btn btn-primary" data-act="restart">Restart</button>
              <button type="button" class="btn" data-act="pauseNow">Pause</button>
            </div>` : v.issue ? html`
            <div class="issue">
              <span class="dia" style="width:12px;height:12px;background:var(--wr)"></span>
              <div class="col grow" style="gap:0"><b>${v.issue.text}</b><span class="small mute">${v.legs.length ? `${plural(v.legs.length, 'leg')} still open. Check them before it carries on.` : 'No legs are open.'}</span></div>
              ${v.legs.length ? html`<button type="button" class="btn btn-primary" data-act="close">Close ${plural(v.legs.length, 'leg')}…</button>` : ''}
              <button type="button" class="btn" data-act="pauseNow">Pause</button>
            </div>` : ''}
          <div class="strip" style="grid-template-columns:var(--g-5)">${strip(v).map(([l, val, c, s, k, tip]) => html`
            <div class="stat" ${tip ? html`data-tip="${tip}"` : ''}><span class="l">${l}</span><span class="v ${c}" ${k ? html`data-strip="${k}"` : ''}>${val}</span><span class="s">${s}</span></div>`)}</div>
          <nav class="tabs" aria-label="Deployment">${TABS(v).map(([k, l]) => html`
            <a href="${href(`d/${v.id}/${k}`)}" class="${k === tabKey ? 'on' : ''}" ${k === tabKey ? html`aria-current="page"` : ''}>${l}</a>`)}</nav>
        </div>`;
    }

    async function paint() {
      if (!book.isLoaded('deployments')) { mount(main, html`<div class="page"><div class="skel" style="height:260px"></div></div>`); return; }
      const v = book.find(id);
      if (!v) {
        mount(main, html`<div class="page">${empty('This deployment doesn’t exist', 'It may have been deleted.', html`<a class="btn" href="${href('deployments')}">All deployments</a>`)}</div>`);
        return;
      }
      document.title = `${v.name} · Linck`;
      const tabs = TABS(v);
      const want = tabs.find(t => t[0] === (params.tab === 'days' ? 'cycles' : params.tab)) || tabs[0];
      const sameTab = tabKey === want[0] && $('#tabBody', main);
      tabKey = want[0];
      if (sameTab) {
        // Data refresh: re-render the header and let the tab update itself.
        const h = $('.dep-head', main);
        if (h) h.outerHTML = String(header(v));
        return;
      }
      mount(main, html`${header(v)}<div id="tabBody"></div>`);
      if (tabCleanup) { tabCleanup(); tabCleanup = null; }
      const mod = (await want[2]()).default;
      if (tabKey !== want[0] || !$('#tabBody', main)) return;
      tabCleanup = mod.render($('#tabBody', main), v) || null;
    }

    const vNow = () => book.find(id);
    const offActs = actions(main, {
      primary: () => { const v = vNow(); if (v) A.primary(v)[1](); },
      pauseNow: () => { const v = vNow(); if (v) A.pause([v]); },
      restart: () => A.restartDeployment(id),
      close: (d, el) => (el.dataset.reason ? say(el.dataset.reason) : A.closeLegs([vNow()])),
      stop: (d, el) => (el.dataset.reason ? say(el.dataset.reason) : A.stop(vNow())),
      edit: () => { menu = false; go(`d/${id}/settings`); },
      copy: () => A.copyAsNew(vNow()),
      tag: () => { menu = false; paintHeader(); A.addTag([vNow()]); },
      untag: d => A.removeTag(vNow(), d.t),
      removeTrade: (d, el) => { menu = false; paintHeader(); if (el.dataset.reason) return say(el.dataset.reason); A.removeTrade(vNow()); },
      delete: (d, el) => { menu = false; paintHeader(); if (el.dataset.reason) return say(el.dataset.reason); A.remove(vNow()); },
      menu: () => { menu = !menu; paintHeader(); },
      geek: (d, el) => enterGeek(id, el),
      makePrimary: () => A.makePrimary(vNow()),
    });
    const offKey = on(document, 'keydown', 'body', e => {
      if (e.key !== 'g' && e.key !== 'G') return;
      if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest('input, textarea, select, [contenteditable], .dialog')) return;
      const v = vNow();
      if (v && geekOk(v)) { e.preventDefault(); enterGeek(id, $('[data-act="geek"]', main)); }
    });
    function paintHeader() {
      const v = vNow(), h = $('.dep-head', main);
      if (v && h) h.outerHTML = String(header(v));
    }
    const offDoc = on(document, 'click', 'body', e => {
      if (menu && !e.target.closest('.menu') && !e.target.closest('[data-act="menu"]')) { menu = false; paintHeader(); }
    });
    paint();
    const offs = [
      listen('data', paint),
      listen('ticks', () => {
        const v = vNow();
        if (!v) return;
        const x = book.live(v);
        [['today', x.cycle, inrFit], ['overall', x.overall, inrFit], ['unreal', x.unreal, inrFit], ['ret', x.ret, pct]].forEach(([k, val, f]) => {
          const el = $(`[data-strip="${k}"]`, main);
          if (el && val != null) { setText(el, f(val)); setTone(el, tone(val)); }
        });
      }),
    ];
    return () => { offActs(); offDoc(); offKey(); offs.forEach(f => f()); if (tabCleanup) tabCleanup(); };
  },
};
