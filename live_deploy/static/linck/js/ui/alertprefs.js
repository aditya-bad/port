// Which kinds of alert a deployment raises (data/alert_types.js): one
// switch per kind. For one deployment on its Settings tab; for every
// deployment of a strategy on the strategy's page, where a switch shows
// how many have it on and flipping it sets them all.

import { html } from '../core/dom.js';
import { api } from '../core/api.js';
import * as book from '../data/book.js';
import { GROUPS, ALWAYS_ON, isOn, switched } from '../data/alert_types.js';
import { toggle } from './components.js';
import { say } from './toast.js';

export function alertPrefs(deps, { act = 'alertKind' } = {}) {
  const many = deps.length > 1;
  const row = ([key, label, hint]) => {
    const on = deps.filter(v => isOn(v, key)).length;
    const state = on === deps.length ? 'on' : on === 0 ? 'off' : 'mixed';
    return html`<div class="pref-row" data-alert-kind="${key}"><div class="col grow" style="gap:0;min-width:180px"><b>${label}</b><span class="tiny mute">${hint}</span></div>
      ${many && state === 'mixed' ? html`<span class="tiny mute" data-mixed>on for ${on} of ${deps.length}</span>` : ''}
      ${toggle(state === 'on', act, { kind: key, sm: true }, `${label} alerts`)}</div>`;
  };
  return html`<div class="stack al-prefs" style="gap:18px" data-alert-prefs>
    ${GROUPS.map(([title, ts]) => html`<div><div class="al-group">${title}</div>${ts.map(row)}</div>`)}
    <div><div class="al-group">Always on</div>
      <p class="tiny mute" style="margin:4px 0 0">These can’t be turned off: missing one can lose money unwatched.</p>
      ${ALWAYS_ON.map(([label, hint]) => html`<div class="pref-row"><div class="col grow" style="gap:0"><b>${label}</b><span class="tiny mute">${hint}</span></div>
        <span class="tiny mute">always</span></div>`)}</div>
  </div>`;
}

// Flip one kind for these deployments: on for all when any had it off,
// else off for all. Says what it did.
export async function flipKind(deps, key) {
  const label = (GROUPS.flatMap(([, ts]) => ts).find(t => t[0] === key) || [key, key])[1];
  const on = !deps.every(v => isOn(v, key));
  const todo = deps.filter(v => isOn(v, key) !== on);
  try {
    for (const v of todo) await api.update(v.id, { alerts_off: switched(v, key, on) });
    await book.load(['deployments']);
    say(`${label} alerts ${on ? 'on' : 'off'}${deps.length > 1 ? ` for ${deps.length} deployments` : ''}.`);
  } catch (e) {
    await book.load(['deployments']).catch(() => {});
    say(`Couldn’t change ${label.toLowerCase()} alerts: ${e.message}`);
  }
}
