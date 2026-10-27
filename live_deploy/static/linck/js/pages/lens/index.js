// LinckLens: what the Kite account holds (app/lens.py). Four pages over
// the same data (data/lens.js): Overview, Shares, Funds, Account.

import { html, mount, actions, on, $ } from '../../core/dom.js';
import { hhmm } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { say, fail } from '../../ui/toast.js';
import { lens, onLens, loadHoldings } from '../../data/lens.js';
import * as overview from './overview.js';
import * as shares from './shares.js';
import * as funds from './funds.js';
import * as account from './account.js';

export const TABS = [['overview', 'Overview', overview], ['shares', 'Shares', shares], ['funds', 'Funds', funds], ['account', 'Account', account]];
const HEAD = {
  overview: ['Overview', 'Everything in your Kite account, and what it’s done.'],
  shares: ['Shares', 'Stocks, ETFs, gold and REITs in your demat.'],
  funds: ['Funds', 'Mutual funds and the SIPs feeding them.'],
  account: ['Account', 'The money in the trading account, and how these pages are read.'],
};
// what each page remembers while you move around LinckLens
const ui = { window: '1Y', q: '', asset: 'all', sort: 'value', dir: 'desc', open: null };

export default {
  title: p => `${(HEAD[p.tab] || HEAD.overview)[0]} · LinckLens`,
  render(main, params) {
    const tab = TABS.find(t => t[0] === params.tab) ? params.tab : 'overview';
    if (params.open) ui.open = params.open;
    const mod = TABS.find(t => t[0] === tab)[2];

    function paint() {
      const st = lens, [title, sub] = HEAD[tab];
      const focus = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.f : null;
      mount(main, html`<div class="page-head lens-head">
          <div class="col"><h1 class="t-h1">${title}</h1><span class="mute">${sub}</span></div>
          <span class="grow"></span>
          ${st.h && st.h.as_of ? html`<span class="small mute">Read ${hhmm(st.h.as_of)}</span>` : ''}
          <button type="button" class="btn btn-sm" data-act="refresh" ${st.busy ? 'disabled' : ''}>${st.busy ? 'Reading…' : 'Refresh'}</button>
        </div>
        <div class="page lens" data-tab="${tab}">
          ${st.hErr ? (st.hErr.status === 409
              ? empty('Kite isn’t connected', 'Your holdings are read from your Kite account. Connect it with Reconnect at the top, then Refresh.')
              : empty('Couldn’t read your holdings', st.hErr.message))
            : !st.h ? loading(8) : mod.render(st, ui)}
        </div>`);
      if (focus) {   // typing in the search box survives the redraw
        const el = $(`[data-f="${focus}"]`, main);
        if (el) { el.focus(); if (el.setSelectionRange && el.value != null) el.setSelectionRange(el.value.length, el.value.length); }
      }
      if (ui.open && params.open) {
        const row = $(`[data-symbol="${CSS.escape(ui.open)}"]`, main);
        if (row) { row.scrollIntoView({ block: 'center' }); params.open = null; }
      }
    }
    const off = onLens(paint);
    const offActs = actions(main, {
      refresh: async () => { await loadHoldings(true); if (lens.hErr) fail(lens.hErr, 'Couldn’t read your holdings'); else say('Read from Kite again.'); },
      window: x => { ui.window = x.v; paint(); },
      asset: x => { ui.asset = x.v; paint(); },
      dir: () => { ui.dir = ui.dir === 'asc' ? 'desc' : 'asc'; paint(); },
      open: x => { ui.open = ui.open === x.s ? null : x.s; paint(); },
    });
    const offIn = on(main, 'input', '[data-f="q"]', (e, el) => { ui.q = el.value; paint(); });
    const offSort = on(main, 'change', '[data-f="sort"]', (e, el) => { ui.sort = el.value; ui.dir = el.value === 'name' ? 'asc' : 'desc'; paint(); });
    paint();
    loadHoldings();
    return () => { off(); offActs(); offIn(); offSort(); };
  },
};
