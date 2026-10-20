// Settings at work: every setting the strategy reads, its value, the
// default, and what it has actually done here. Changed ones are marked.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { href } from '../../core/router.js';
import { empty, loading } from '../../ui/components.js';
import { head, table, poll } from './ui.js';

const show = x => (x == null ? '—' : typeof x === 'boolean' ? (x ? 'on' : 'off') : Array.isArray(x) ? x.join(', ') : String(x));

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Settings at work')}${loading(8)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Settings at work')}${empty('Couldn’t load settings', data.message)}`); return; }
      const changed = data.rows.filter(r => r.changed).length;
      const done = data.rows.some(r => r.effect);
      mount(el, html`
        ${head('Settings at work', `Every setting the strategy reads, its value${done ? ', the default, and what it has done here' : ' and the default'}. Highlighted: changed from the default.`, `${changed} changed`)}
        ${table([['Setting', 'minmax(220px,1.3fr)'], ['Value', '110px', 'num'], ['Default', '110px', 'num'], ...(done ? [['What it has done', 'minmax(200px,1.6fr)']] : [])],
          data.rows.map(r => [html`<span class="col" style="gap:1px">${r.label ? html`<b class="small">${r.label}</b>` : ''}<span class="mono tiny">${r.key}</span>${r.help ? html`<span class="tiny mute">${r.help}</span>` : ''}</span>`,
            html`<b>${show(r.value)}</b>`, html`<span class="mute">${show(r.default)}</span>`, ...(done ? [r.effect || html`<span class="mute">—</span>`] : [])]),
          { minw: done ? 760 : 520, rowCls: i => (data.rows[i].changed ? 'att' : '') })}
        ${data.rules ? html`<div class="gk-panel"><h3>Rules that traded <span class="s">fills grouped by the rule that placed them</span></h3>
          ${data.rules.length ? table([['Rule', 'minmax(0,1fr)'], ['Actions', '90px', 'num']], data.rules.map(r => [html`<span class="col" style="gap:1px"><b>${r.label}</b><span class="mono tiny mute">${r.rule}</span></span>`, String(r.n)]), { minw: 0 }) : empty('', 'No trades yet.')}</div>` : ''}
        <p class="gk-note">Read-only here. To change a setting, pause the deployment and use <a href="${href(`d/${v.id}/settings`)}">Settings</a> on the normal page.</p>`);
    }
    const stop = poll(() => api.geek(g.id, 'settings'), 120000, d => { data = d; paint(); });
    return stop;
  },
};
