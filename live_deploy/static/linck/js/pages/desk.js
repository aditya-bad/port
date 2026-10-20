// Desk — the session at a glance: today's P&L, what needs you, one lane
// per deployment on the 09:15–15:30 axis, open positions, activity.

import { api } from '../core/api.js';
import { html, raw, mount, $, $$, actions, setText, setTone } from '../core/dom.js';
import { inr, inrS, price, hhmm, hhmmss, dLong, sessionFrac, sessionLeft, isSessionOpen, plural, tone, toneVar, ist, OPEN_MIN, SESSION_MIN } from '../core/format.js';
import { current as basisNow } from '../core/basis.js';
import { ui, listen } from '../core/store.js';
import { go, href } from '../core/router.js';
import * as book from '../data/book.js';
import { kite } from '../data/live.js';
import { alerts, dismiss, attention } from '../data/alerts.js';
import { DESK_LANES, STREAK_ALERT } from '../data/config.js';
import { chips, utabs, marker, empty } from '../ui/components.js';
import { legHead, legRow, liveTag, tickLegs } from '../ui/legs.js';
import { primary, resume, kiteDialog, restartDeployment } from '../actions.js';

const st = ui('desk', { f: 'all', sort: 'attn', all: false, lab: false });
const EV = {
  fill: ['Fill', 'var(--ink)'], exit: ['Exit', 'var(--b)'], adjust: ['Adjust', 'var(--b)'], rollout: ['EOD roll-out', 'var(--c5)'],
  error: ['Error', 'var(--wr)'], pause: ['Pause', 'var(--wr)'], stop: ['Stop', 'var(--wr)'], close: ['Closed', 'var(--wr)'],
};
const HOURS = ['09:15', '10:15', '11:15', '12:15', '13:15', '14:15', '15:15'];

function odometer(text) {
  return html`<span class="odo" aria-label="${text}">${[...text].map(ch => (/\d/.test(ch)
    ? html`<span class="odo-d" aria-hidden="true"><span style="transform:translateY(calc(var(--odo) * -${ch}))">${[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map(d => html`<span>${d}</span>`)}</span></span>`
    : html`<span class="odo-s" aria-hidden="true">${ch}</span>`))}</span>`;
}
function setOdometer(el, text) {
  if (!el) return;
  const cur = el.getAttribute('aria-label');
  if (cur === text) return;
  const digits = $$('.odo-d', el);
  // Same shape (only digits changed): roll in place; otherwise rebuild.
  const shape = s => s.replace(/\d/g, '0');
  if (cur && shape(cur) === shape(text) && digits.length) {
    let i = 0;
    [...text].forEach(ch => { if (/\d/.test(ch)) { digits[i].firstElementChild.style.transform = `translateY(calc(var(--odo) * -${ch}))`; i++; } });
    el.setAttribute('aria-label', text);
  } else {
    el.outerHTML = String(odometer(text));
  }
}

function laneNote(v) {
  if (v.issue) return [v.issue.text, true];
  if (v.rawStatus === 'paused' && v.legs.length) return [`Paused while still holding ${plural(v.legs.length, 'leg')}`, true];
  if (v.streak >= STREAK_ALERT) return [`losing streak of ${v.streak}`, true];
  if (v.quiet) return [`no trade for ${v.quietDays} days`, true];
  return [`${v.strategy} · ${v.mode}`, false];
}
// Lane geometry: x in 0–1000 over 09:15–15:30, y around 31 in a 62-high box.
function lanePath(v, x, frac) {
  const lane = book.session() && book.session().lanes[v.id];
  const pts = [];
  // Legs unpriced since the open: they haven't moved, so neither has the lane.
  if (lane && !x.stale) {
    for (const [t, val] of lane.series) {
      const p = ist(t), m = p.h * 60 + p.mi - OPEN_MIN;
      if (m >= 0 && m <= SESSION_MIN) pts.push([m / SESSION_MIN, val]);
    }
  }
  if (v.rawStatus !== 'stopped') {
    if (!pts.length || pts[0][0] > 0.001) pts.unshift([0, 0]);
    pts.push([frac, x.today]);
  }
  if (!pts.length) return { d: '', area: '' };
  // Floor the scale at 0.1% of capital (min ₹100): a few rupees mustn't fill the lane.
  const sc = Math.max(100, v.cap * 0.001, ...pts.map(p => Math.abs(p[1])));
  const d = pts.map(([fx, y], i) => `${i ? 'L' : 'M'}${(fx * 1000).toFixed(1)} ${(31 - y / sc * 26).toFixed(1)}`).join(' ');
  return { d, area: `${d} L${(pts[pts.length - 1][0] * 1000).toFixed(1)} 31 L0 31 Z` };
}
function laneEvents(v, frac) {
  const lane = book.session() && book.session().lanes[v.id];
  if (!lane) return [];
  return lane.events.map(e => {
    const p = ist(e.t), m = p.h * 60 + p.mi - OPEN_MIN;
    const f = Math.max(0, Math.min(frac, m / SESSION_MIN));
    const [k, c] = EV[e.kind] || EV.fill;
    return { x: f * 100, c, tip: `${k} at ${hhmm(e.t)}${e.reason ? `\n${e.reason}` : ''}` };
  });
}

function hero(T, list) {
  const live = kite.state === 'connected';
  const left = sessionLeft();
  const capAll = list.reduce((a, v) => a + (v.rawStatus === 'stopped' ? 0 : v.cap), 0);
  const capUse = list.filter(v => v.legs.length).reduce((a, v) => a + v.cap, 0);
  const text = inr(T.today);
  const scale = Math.min(1, (matchMedia('(max-width:900px)').matches ? 7 : 10) / text.length);
  return html`
    <div class="hero-l">
      <div class="row hero-date"><span class="dot ${live ? 'live' : 'off'}"></span>
        <span>${dLong(new Date())} · day P&L across ${plural(list.filter(v => v.rawStatus !== 'stopped').length, 'deployment')} <span class="stamp">${basisNow().stamp}</span></span></div>
      <div class="hero-num ${tone(T.today)} ${live ? '' : 'dim'}" style="zoom:${scale.toFixed(3)}" id="heroNum">${odometer(text)}</div>
      <div class="hero-subs">
        ${[['Realized', inr(T.realized), tone(T.realized), 'subR'], ['Unrealized', inr(T.unreal), tone(T.unreal), 'subU'], ['Overall', inrS(T.overall), tone(T.overall), 'subO'],
          ['Capital in use', `${inrS(capUse, { sign: false })} / ${inrS(capAll, { sign: false })}`, '', ''], ['Session left', live ? left || 'closed' : 'not live', '', '']]
          .map(([l, v, c, id]) => html`<div class="col" style="gap:2px"><span class="label">${l}</span><span class="mono hero-sub ${c}" ${id ? html`id="${id}"` : ''}>${v}</span></div>`)}
      </div>
    </div>`;
}
function attentionPanel(A) {
  return html`
    <div class="attn">
      <div class="attn-head ${A.length ? '' : 'clear'}"><span class="attn-n">${A.length}</span><span class="attn-t">${A.length === 1 ? 'thing needs you' : A.length ? 'things need you' : 'all clear'}</span></div>
      ${A.slice(0, 5).map(a => html`
        <div class="attn-row rise">
          <span class="mono tiny mute">${a.n}</span>
          <div class="col" style="gap:1px"><b>${a.title}</b><span class="small mute pretty">${a.body}</span></div>
          <div class="row" style="gap:6px">
            ${a.canDismiss ? html`<button type="button" class="attn-x" title="Dismiss" aria-label="Dismiss" data-act="dismiss" data-key="${a.key}">×</button>` : ''}
            <button type="button" class="btn btn-sm" data-act="alert" data-key="${a.key}">${a.act}</button>
          </div>
        </div>`)}
      ${A.length > 5 ? html`<div class="attn-more small mute">+ ${A.length - 5} more</div>` : ''}
      ${!A.length ? html`<div class="attn-ok mute">Nothing needs you. Every running deployment is connected, managed and within its limits.</div>` : ''}
    </div>`;
}

function lanesBlock(list, frac) {
  const s = st.get();
  const base = list.filter(v => s.lab || !v.isLab);
  const F = [
    ['all', 'All', () => true], ['attn', 'Needs attention', v => attention(v) > 0], ['open', 'Holding', v => v.legs.length > 0],
    ['intra', 'Intraday', v => v.modeKey === 'intraday'], ['pos', 'Positional', v => v.modeKey === 'positional'],
  ];
  const fn = (F.find(f => f[0] === s.f) || F[0])[2];
  let L = base.filter(fn).map(v => ({ v, x: book.live(v), a: attention(v) }));
  if (s.sort === 'attn') L.sort((p, q) => q.a - p.a || Math.abs(q.x.today) - Math.abs(p.x.today));
  else if (s.sort === 'today') L.sort((p, q) => q.x.today - p.x.today);
  else L.sort((p, q) => p.v.name.localeCompare(q.v.name));
  const total = L.length, shown = s.all ? L : L.slice(0, DESK_LANES), compact = shown.length > 14;
  const labN = list.filter(v => v.isLab).length;
  return html`
    <div class="pad-x lanes-head">
      <div class="col"><h2 class="t-h2">The session, deployment by deployment</h2>
        <span class="mute">Each lane is one deployment’s P&L since 09:15. Diamonds mark fills, exits and errors. Click a name to open it.</span></div>
      <span class="grow"></span>
      <div class="chips">${chips(F.map(([k, l, f]) => [k, l, base.filter(f).length]), s.f, 'filter', { strong: true })}</div>
      <div class="row small mute" style="gap:6px;font-weight:600">Sort ${utabs([['attn', 'Attention'], ['today', 'Today P&L'], ['name', 'Name']], s.sort, 'sort')}</div>
    </div>
    <div class="pad-x" style="padding-bottom:30px">
      <div class="lane-axis">
        <b class="small">${plural(total, 'deployment')}</b>
        <div class="axis">${HOURS.map((t, i) => html`<span class="${i % 3 ? 'hide-phone' : ''}" style="left:${i * 60 / 375 * 100}%">${t}</span>`)}
          ${isSessionOpen() ? html`<span class="now-chip" style="left:${frac * 100}%">${hhmm(new Date())}</span>` : ''}</div>
        <b class="small right">Today</b><span></span>
      </div>
      <div class="lanes ${compact ? 'compact' : ''}">
        <div class="now-line" style="left:calc(var(--lane-off) + (100% - var(--lane-rest)) * ${frac.toFixed(4)})"></div>
        ${shown.map(({ v, x }) => {
          const [note, warn] = laneNote(v);
          const p = lanePath(v, x, frac);
          const [act] = primary(v);
          const stopped = v.rawStatus === 'stopped';
          return html`
            <div class="lane ${v.issue ? 'att' : ''}">
              <a class="lane-name" href="${href(`d/${v.id}`)}">
                <span class="row" style="gap:8px">${marker(v.status)}<b>${v.name}</b></span>
                ${!compact || warn ? html`<span class="tiny ${warn ? 'warn' : 'mute'}">${note}</span>` : ''}
              </a>
              <div class="lane-plot">
                <svg viewBox="0 0 1000 62" preserveAspectRatio="none" aria-hidden="true">
                  <line x1="0" x2="1000" y1="31" y2="31" stroke="var(--line)" vector-effect="non-scaling-stroke"></line>
                  ${p.d ? raw(`<path d="${p.area}" fill="${x.today >= 0 ? 'var(--gs)' : 'var(--rs)'}"/><path d="${p.d}" fill="none" stroke="${stopped ? 'var(--line2)' : toneVar(x.today)}" stroke-width="2" vector-effect="non-scaling-stroke"/>`) : ''}
                </svg>
                ${laneEvents(v, frac).map(e => html`<span class="lane-ev" data-tip="${e.tip}" style="left:${e.x}%;background:${e.c}"></span>`)}
              </div>
              <span class="mono lane-today ${stopped ? 'flat' : tone(x.today)}" data-today="${v.id}">${stopped ? '—' : inr(x.today)}</span>
              <span class="lane-act hide-phone"><button type="button" class="btn btn-xs" data-act="primary" data-id="${v.id}">${act}</button></span>
            </div>`;
        })}
        ${!shown.length ? html`<div class="empty">No deployments match this filter.</div>` : ''}
      </div>
      <div class="row wrap lane-legend">
        <span class="row" style="gap:7px"><span class="dia"></span>Fill</span>
        <span class="row" style="gap:7px"><span class="dia" style="background:var(--b)"></span>Exit or adjust</span>
        <span class="row" style="gap:7px"><span class="dia" style="background:var(--wr)"></span>Error or pause</span>
        ${labN ? html`<button type="button" class="linkish" style="font-weight:400" data-act="lab">${s.lab ? 'Hide Strategy Lab' : `Strategy Lab (${labN}) hidden · Show`}</button>` : ''}
        <span class="grow"></span>
        ${total > DESK_LANES ? html`<button type="button" class="btn btn-sm" data-act="all">${s.all ? `Show top ${DESK_LANES}` : `Show all ${total} lanes`}</button>` : ''}
      </div>
    </div>`;
}

function positionsBlock(list, T) {
  const withLegs = list.filter(v => v.legs.length).sort((a, b) => Math.abs(book.live(b).unreal) - Math.abs(book.live(a).unreal));
  return html`
    <div class="col-pad scroll-x">
      <div class="sect ruled"><h3 class="t-h3">Open positions</h3><span class="mute">${plural(T.legs, 'leg')}, grouped by deployment</span><span class="grow"></span>
        ${liveTag(withLegs.flatMap(v => v.legs))}<span class="mono ${tone(T.unreal)}" id="unrTot">${inr(T.unreal)}</span></div>
      ${withLegs.length ? html`
        ${legHead()}
        ${withLegs.slice(0, 8).map(v => {
          const x = book.live(v);
          return html`
            <section class="leg-set" aria-label="${v.name}">
              <a class="leg-group" href="${href(`d/${v.id}`)}">
                <span class="leg-group-name"><b class="ell">${v.name}</b><span class="leg-group-meta ell">${v.strategyLabel} · ${plural(v.legs.length, 'leg')}</span></span>
                <span class="tiny b warn">${v.status === 'down' ? 'NOT RUNNING' : v.status === 'error' ? 'ERROR' : v.rawStatus === 'paused' ? 'PAUSED' : ''}</span><span class="grow"></span>
                <span class="mono ${tone(x.unreal)}" data-grp="${v.id}">${inr(x.unreal)}</span></a>
              <div class="leg-set-rows">${v.legs.map(legRow)}</div>
            </section>`;
        })}
        ${withLegs.length > 8 ? html`<a class="linkish" style="display:inline-block;padding-top:12px" href="${href('deployments')}">+ ${plural(withLegs.length - 8, 'more deployment')} holding positions</a>` : ''}`
      : empty('', 'Nothing open right now.')}
    </div>`;
}

function activityBlock(list, recent) {
  const byId = Object.fromEntries(list.map(v => [v.id, v]));
  const items = [];
  const ses = book.session();
  if (ses) {
    for (const [id, lane] of Object.entries(ses.lanes)) {
      if (!byId[id]) continue;
      lane.events.forEach(e => { if (['error', 'pause', 'stop', 'close'].includes(e.kind)) items.push({ t: e.t, kind: e.kind, id, name: byId[id].name, x: e.reason || '' }); });
    }
  }
  (recent || []).forEach(f => {
    const r = f.reason || '';
    const kind = /eod_rollout/.test(r) ? 'rollout' : !r || /entry|open/i.test(r) ? 'fill' : /roll|adjust|hedge/i.test(r) ? 'adjust' : 'exit';
    items.push({ t: new Date(f.executed_at).getTime(), kind,
      id: f.deployment_id, name: f.deployment_name, x: `${f.action.toUpperCase()} ${Number(f.qty)} ${f.symbol} @ ${price(f.price)}` });
  });
  items.sort((a, b) => b.t - a.t);
  const today = items.filter(i => i.t >= Date.now() - 864e5 * 4).slice(0, 9);
  return html`
    <div class="col-pad">
      <div class="sect ruled"><h3 class="t-h3">Activity</h3><span class="mute">fills, exits, errors and pauses, newest first</span></div>
      ${today.length ? today.map(i => { const [k, c] = EV[i.kind] || EV.fill; return html`
        <a class="act-row" href="${href(`d/${i.id}`)}"><span class="mono tiny mute">${hhmmss(i.t)}</span>
          <span class="row b" style="gap:6px"><span class="dia sm" style="background:${c}"></span>${k}</span>
          <span class="col" style="gap:0"><b>${i.name}</b><span class="mono mute" style="font-size:11px">${i.x}</span></span></a>`; })
        : empty('', 'No fills, exits or alerts in the last few days.')}
    </div>`;
}

export default {
  title: 'Desk',
  render(main) {
    let recent = null;
    function paint() {
      if (!book.isLoaded('deployments')) { mount(main, html`<div class="page"><div class="skel" style="height:220px"></div></div>`); return; }
      const all = book.deployments();
      const list = all.filter(v => !v.isLab || st.get().lab);
      const T = book.totals(all.filter(v => v.rawStatus !== 'stopped'));
      const A = alerts(all);
      const frac = sessionFrac();
      mount(main, html`
        ${kite.state !== 'connected' ? html`
          <div class="banner kite-banner">
            <b class="t-h4" style="font-size:18px">Linck is not connected to Kite.</b>
            <span class="mute grow">Prices and P&L below are frozen. No deployment can enter, adjust or exit until you reconnect. Your daily Kite login expires at about 08:00.</span>
            <button type="button" class="btn btn-primary" data-act="kite">Reconnect Kite</button>
          </div>` : ''}
        <section class="hero" aria-label="Today">${hero(T, all)}${attentionPanel(A)}</section>
        <section aria-label="Session lanes">${lanesBlock(all, frac)}</section>
        <section class="desk-bottom">${positionsBlock(list, T)}${activityBlock(all, recent)}</section>`);
      clearAxis();
    }
    // Hide the hour labels the "now" chip sits on (measured: depends on width).
    function clearAxis() {
      const chip = $('.axis .now-chip', main);
      const r = chip && chip.getBoundingClientRect();
      $$('.axis > span:not(.now-chip)', main).forEach(l => {
        const b = l.getBoundingClientRect();
        l.style.visibility = r && b.width && b.right + 4 > r.left && b.left - 4 < r.right ? 'hidden' : '';
      });
    }
    function tick() {
      if (!book.isLoaded('deployments')) return;
      const all = book.deployments();
      const T = book.totals(all.filter(v => v.rawStatus !== 'stopped'));
      setOdometer($('#heroNum .odo', main), inr(T.today));
      const hn = $('#heroNum', main);
      if (hn) setTone(hn, tone(T.today));
      [['#subR', T.realized, inr], ['#subU', T.unreal, inr], ['#subO', T.overall, inrS], ['#unrTot', T.unreal, inr]].forEach(([sel, v, f]) => {
        const el = $(sel, main); setText(el, f(v)); setTone(el, tone(v));
      });
      all.forEach(v => {
        const x = book.live(v);
        const el = $(`[data-today="${v.id}"]`, main);
        if (el && v.rawStatus !== 'stopped') { setText(el, inr(x.today)); setTone(el, tone(x.today)); }
        const g = $(`[data-grp="${v.id}"]`, main);
        if (g) { setText(g, inr(x.unreal)); setTone(g, tone(x.unreal)); }
      });
      tickLegs(main, all.flatMap(v => v.legs));
    }
    const offActs = actions(main, {
      kite: () => kiteDialog(),
      filter: d => { st.set({ f: d.v, all: false }); paint(); },
      sort: d => { st.set({ sort: d.v }); paint(); },
      lab: () => { st.set({ lab: !st.get().lab }); paint(); },
      all: () => { st.set({ all: !st.get().all }); paint(); },
      primary: d => { const v = book.find(d.id); if (v) primary(v)[1](); },
      dismiss: d => { const a = alerts().find(x => x.key === d.key); if (a) { dismiss(a); paint(); } },
      alert: d => {
        const a = alerts().find(x => x.key === d.key);
        if (!a) return;
        const ac = a.action;
        if (ac.type === 'kite') kiteDialog();
        else if (ac.type === 'open') go(`d/${ac.id}${ac.tab ? `/${ac.tab}` : ''}`);
        else if (ac.type === 'resume') { const v = book.find(ac.id); if (v) resume([v]); }
        else if (ac.type === 'restart') restartDeployment(ac.id);
      },
    });
    api.recentTrades(40).then(r => { recent = r; paint(); }).catch(() => {});
    paint();
    const offs = [listen('data', paint), listen('status', paint), listen('ticks', tick)];
    const iv = setInterval(() => { tick(); const nc = $('.now-chip', main); if (nc) setText(nc, hhmm(new Date())); }, 1000);
    const iv2 = setInterval(paint, 60000);   // move the now-line
    return () => { offActs(); offs.forEach(f => f()); clearInterval(iv); clearInterval(iv2); };
  },
};
