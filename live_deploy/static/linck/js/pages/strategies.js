// Strategies — the catalogue: what each strategy does, its risk and
// hold, how many deployments run it and what they've made.

import { html, mount, actions } from '../core/dom.js';
import { inr, plural, tone } from '../core/format.js';
import { pnlOf } from '../core/basis.js';
import { listen } from '../core/store.js';
import { go } from '../core/router.js';
import * as book from '../data/book.js';
import { empty } from '../ui/components.js';
import { say } from '../ui/toast.js';
import { newDeployment } from '../actions.js';
import { api } from '../core/api.js';
import { openDialog } from '../ui/dialog.js';

export default {
  title: 'Strategies',
  render(main) {
    function paint() {
      if (!book.isLoaded('strategies', 'deployments')) { mount(main, html`<div class="page"><div class="skel" style="height:300px"></div></div>`); return; }
      const S = book.strategies(), all = book.deployments();
      mount(main, html`
        <div class="page-head">
          <div class="col"><h1 class="t-h1">Strategies</h1><span class="mute">${plural(S.length, 'strategy', 'strategies')} · ${plural(all.length, 'deployment')} running them</span></div>
          <span class="grow"></span><button type="button" class="btn btn-primary" data-act="new">New deployment</button>
        </div>
        <div class="pad-x" style="padding-bottom:var(--px)">
          ${S.length ? html`<div class="str-grid">${S.map(s => {
            const L = all.filter(v => v.strategy === s.name);
            const p = L.reduce((a, v) => a + book.live(v).overall, 0);
            const T = L.flatMap(v => v.trades), wins = T.filter(t => pnlOf(t) > 0).length;
            return html`<article class="str-card">
              <div class="row base"><h2 class="t-h3" style="font-size:24px">${s.label}</h2><span class="tiny b mute">${s.mode === 'intraday' ? 'Intraday' : 'Positional'}</span></div>
              <span class="mono mute" style="font-size:11px">${s.name}${s.enabled === false ? ' · disabled' : ''}</span>
              <p class="mute pretty small" style="margin:0;min-height:60px">${s.description}</p>
              <div class="kv-grid small" style="grid-template-columns:96px 1fr">
                <span class="mute">Index</span><b>${s.index}</b>
                ${s.risk ? html`<span class="mute">Risk</span><b>${s.risk}</b>` : ''}
                ${s.hold ? html`<span class="mute">Typical hold</span><b>${s.hold}</b>` : ''}
                <span class="mute">Running</span><b>${L.length ? `${plural(L.length, 'deployment')} · ${L.filter(v => v.rawStatus === 'active').length} active` : 'none'}</b>
                <span class="mute">Closed trades</span><b>${T.length ? `${T.length} · ${Math.round(wins / T.length * 100)}% won` : '—'}</b>
                <span class="mute">Overall P&L</span><b class="mono ${tone(p)}" style="font-weight:500">${L.length ? inr(p) : '—'}</b>
              </div>
              <div class="row" style="gap:8px;padding-top:4px">
                <button type="button" class="btn btn-sm btn-primary" data-act="deploy" data-k="${s.name}" ${s.enabled === false ? html`aria-disabled="true" data-reason="This strategy is disabled on the server."` : ''}>Deploy</button>
                <button type="button" class="btn btn-sm" data-act="view" data-k="${s.name}" ${L.length ? '' : html`aria-disabled="true" data-reason="Nothing runs this strategy yet."`}>View deployments</button>
                <span class="grow"></span>
                <button type="button" class="btn btn-xs btn-quiet" data-act="toggle" data-k="${s.name}" data-tip="${s.enabled === false ? 'Show it in New deployment again' : 'Hide it from New deployment. Running deployments keep running.'}">${s.enabled === false ? 'Enable' : 'Disable'}</button>
              </div>
            </article>`;
          })}</div>` : empty('No strategies registered', 'Strategies are Python modules in app/strategies/ on the server.')}
        </div>`);
    }
    const off = actions(main, {
      new: () => newDeployment(),
      deploy: (d, el) => (el.dataset.reason ? say(el.dataset.reason) : newDeployment(d.k)),
      toggle: d => {
        const s = book.strategy(d.k), on = s.enabled === false;
        openDialog({
          kicker: 'ADMIN', title: `${on ? 'Enable' : 'Disable'} ${s.label}?`,
          lines: [on ? 'It shows in New deployment again, for everyone.' : 'It disappears from New deployment for everyone. Deployments already running it keep running, and you can enable it again any time.'],
          okLabel: on ? 'Enable' : 'Disable',
          onOk: async () => { await api.setStrategyEnabled(d.k, on); await book.load(['strategies']); say(`${s.label} ${on ? 'enabled' : 'disabled'}.`); },
        });
      },
      view: (d, el) => (el.dataset.reason ? say(el.dataset.reason) : go(`deployments?strat=${encodeURIComponent(d.k)}`)),
    });
    paint();
    const offs = [listen('data', paint)];
    return () => { off(); offs.forEach(f => f()); };
  },
};
