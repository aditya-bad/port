// Geek mode for one deployment (#/d/:id/geek/:view): the whole screen,
// in the chosen theme, only this strategy. A top bar with the live figures and Exit, a
// menu of views (each with a key), and the chosen view. The basics reuse
// the deployment page's own tabs; the rest are strategy-specific views
// over /v2/deployments/:id/geek/:view. Read-only: orders stay on the
// normal page.

import { api } from '../../core/api.js';
import { html, mount, $, $$, on, actions, setText, setTone } from '../../core/dom.js';
import { inr, price, pct, hhmmss, tone } from '../../core/format.js';
import { listen } from '../../core/store.js';
import { go, href } from '../../core/router.js';
import * as book from '../../data/book.js';
import { prices, isLive } from '../../data/live.js';
import { indexByName } from '../../data/config.js';
import { supports, takeEntry, exit } from '../../data/geek.js';
import { empty } from '../../ui/components.js';

// [key, label, group, shortcut, load, reused deployment tab?]
export const VIEWS = [
  ['overview', 'Overview', 'Basics', '1', () => import('./overview.js')],
  ['positions', 'Positions', 'Basics', '2', () => import('../deployment/now.js'), true],
  ['trades', 'Trades & replay', 'Basics', '3', () => import('../deployment/trades.js'), true],
  ['performance', 'Performance', 'Basics', '4', () => import('../deployment/performance.js'), true],
  ['engine', 'Engine', 'Live', 'E', () => import('./engine.js')],
  ['decisions', 'Decisions', 'Live', 'D', () => import('./decisions.js')],
  ['map', 'Cycle map', 'Anatomy', 'M', () => import('../deployment/days.js'), true],
  ['cycles', 'Cycles', 'Anatomy', 'C', () => import('./cycles.js')],
  ['adjustments', 'Adjustments', 'Anatomy', 'A', () => import('./adjustments.js')],
  ['premium', 'Premium & decay', 'Anatomy', 'P', () => import('./premium.js')],
  ['entries', 'Entries', 'Anatomy', 'N', () => import('./entries.js')],
  ['settings', 'Settings at work', 'Exposure', 'S', () => import('./settings.js')],
];
const GROUPS = ['Basics', 'Live', 'Anatomy', 'Exposure'];

export default {
  title: () => 'Geek mode',
  render(main, params) {
    const id = params.id;
    const view = VIEWS.find(x => x[0] === params.view) || VIEWS[0];
    const entry = takeEntry();
    let engine = null, viewCleanup = null, mounted = false;
    const subs = new Set();
    const g = {
      id, href: k => href(`d/${id}/geek/${k}`),
      engine: () => engine,
      onEngine: fn => { subs.add(fn); return () => subs.delete(fn); },
    };

    function top(v) {
      const x = book.live(v);
      const ix = indexByName(v.index);
      const spot = ix ? prices.get(ix.token) : null;
      const ck = engine && engine.rules ? engine.rules.find(r => r.key === 'checkpoint_target') : null;
      const cycle = engine && engine.state ? engine.state.cycle_id : null;
      return html`
        <img class="mark gk-mark" src="/icons/linck-mark.svg" alt="" width="24" height="24"><span class="gk-badge">GEEK</span>
        <a class="gk-name" href="${href(`d/${id}/now`)}" title="${v.name}">${v.name}</a>
        <span class="mono tiny mute hide-m">${v.strategy} · ${v.mode.toLowerCase()}</span>
        <span class="gk-live ${isLive() && v.rawStatus === 'active' ? 'on' : ''}"><i></i><span class="hide-m">${v.rawStatus === 'active' ? (isLive() ? 'Live' : 'Not streaming') : v.rawStatus === 'paused' ? 'Paused' : 'Stopped'}</span></span>
        <span class="grow"></span>
        ${ix ? html`<span class="gk-kv hide-m"><span>${v.index}</span><b data-gk="spot">${spot ? price(spot.price) : '—'}${spot && spot.prevClose ? html` <span class="${tone(spot.price / spot.prevClose - 1)}">${pct((spot.price / spot.prevClose - 1) * 100)}</span>` : ''}</b></span>` : ''}
        <span class="gk-kv"><span>Today</span><b class="${tone(x.today)}" data-gk="today">${v.rawStatus === 'stopped' ? '—' : inr(x.today)}</b></span>
        <span class="gk-kv hide-m"><span>${cycle ? `Cycle ${cycle}` : 'Cycle'}</span><b class="${ck ? tone(ck.value) : ''}" data-gk="cycle">${ck ? inr(ck.value) : '—'}</b></span>
        <span class="gk-kv hide-m"><span>Overall</span><b class="${tone(x.overall)}" data-gk="overall">${inr(x.overall)}</b></span>
        <span class="gk-kv hide-m"><span>IST</span><b data-gk="clock">${hhmmss(new Date())}</b></span>
        <button type="button" class="gk-exit" data-act="exit">Exit <kbd>Esc</kbd></button>`;
    }

    function rail() {
      const armed = engine && engine.rules && engine.rules.some(r => r.state === 'armed');
      return html`${GROUPS.map(gr => html`<div class="gk-grp"><h4>${gr}</h4>${VIEWS.filter(x => x[2] === gr).map(([k, l, , key]) => html`
        <a href="${g.href(k)}" class="${k === view[0] ? 'on' : ''}" ${k === view[0] ? html`aria-current="page"` : ''}>${l}${k === 'engine' && armed ? html`<span class="dot" title="A rule is about to act"></span>` : ''}<span class="k">${key}</span></a>`)}</div>`)}
        <div class="foot">Keys switch views · Esc leaves<br>Pause, Close and Stop stay on the normal page</div>`;
    }

    function paint() {
      if (!book.isLoaded('deployments')) {
        mount(main, html`<div class="gk"><div class="gk-top"><img class="mark gk-mark" src="/icons/linck-mark.svg" alt="" width="24" height="24"><span class="gk-badge">GEEK</span></div><nav class="gk-rail"></nav><div class="gk-main"><div class="gk-view"><div class="skel" style="height:260px"></div></div></div></div>`);
        return;
      }
      const v = book.find(id);
      if (!v || !supports(v)) {
        mount(main, html`<div class="gk"><div class="gk-top"><img class="mark gk-mark" src="/icons/linck-mark.svg" alt="" width="24" height="24"><span class="gk-badge">GEEK</span><span class="grow"></span><button type="button" class="gk-exit" data-act="exit">Exit <kbd>Esc</kbd></button></div><nav class="gk-rail"></nav>
          <div class="gk-main"><div class="gk-view">${empty(v ? 'No Geek mode for this strategy yet' : 'This deployment doesn’t exist', v ? `${v.strategy} has no strategy-specific views. strangle_monthly_v2 is the first.` : 'It may have been deleted.')}</div></div></div>`);
        return;
      }
      if (mounted) {
        const t = $('.gk-top', main);
        if (t) mount(t, top(v));
        return;
      }
      mounted = true;
      document.title = `${v.name} · Geek · Linck`;
      mount(main, html`<div class="gk ${entry ? 'gk-in' : ''}" ${entry ? html`style="--ox:${entry.x}px;--oy:${entry.y}px"` : ''}>
        <div class="gk-top">${top(v)}</div>
        <nav class="gk-rail" aria-label="Geek views">${rail()}</nav>
        <main class="gk-main"><div class="gk-view" id="gkView"></div></main>
      </div>`);
      if (entry) setTimeout(() => { const s = $('.gk', main); if (s) s.classList.remove('gk-in'); }, 1500);
      view[4]().then(m => {
        const el = $('#gkView', main);
        if (!el) return;
        viewCleanup = m.default.render(el, v, g) || null;
        stagger();
      }).catch(e => { const el = $('#gkView', main); if (el) mount(el, empty('This view didn’t load', e.message)); });
    }
    function stagger() {
      const el = $('#gkView', main);
      if (el) $$(':scope > *', el).forEach((c, i) => c.style.setProperty('--i', i));
    }

    const offActs = actions(main, { exit: (d, el) => exit(id, $('.gk', main), el) });
    const offKeys = on(document, 'keydown', 'body', e => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest('input, textarea, select, [contenteditable]')) return;
      if (e.key === 'Escape') { e.preventDefault(); exit(id, $('.gk', main), $('[data-act="exit"]', main)); return; }
      const hit = VIEWS.find(x => x[3].toLowerCase() === e.key.toLowerCase());
      if (hit && hit[0] !== view[0]) { e.preventDefault(); go(`d/${id}/geek/${hit[0]}`); }
    });
    const stopEngine = (() => {
      let alive = true;
      const run = () => api.geek(id, 'engine').then(d => {
        if (!alive) return;
        engine = d;
        subs.forEach(fn => fn(d));
        const v = book.find(id);
        if (v && mounted) {
          const t = $('.gk-top', main), r = $('.gk-rail', main);
          if (t) mount(t, top(v));
          if (r) { const sc = r.scrollLeft; mount(r, rail()); r.scrollLeft = sc; }
        }
      }).catch(() => {});
      run();
      const iv = setInterval(run, 4000);
      return () => { alive = false; clearInterval(iv); };
    })();
    paint();
    const offs = [
      listen('data', paint),
      listen('ticks', () => {
        const v = book.find(id);
        if (!v || !mounted) return;
        const x = book.live(v);
        const set = (k, val, t) => { const el = $(`[data-gk="${k}"]`, main); if (el) { setText(el, val); if (t !== undefined) setTone(el, tone(t)); } };
        if (v.rawStatus !== 'stopped') set('today', inr(x.today), x.today);
        set('overall', inr(x.overall), x.overall);
      }),
    ];
    const clock = setInterval(() => { const el = $('[data-gk="clock"]', main); if (el) setText(el, hhmmss(new Date())); }, 1000);
    return () => {
      offActs(); offKeys(); stopEngine(); offs.forEach(f => f()); clearInterval(clock);
      if (viewCleanup) viewCleanup();
    };
  },
};
