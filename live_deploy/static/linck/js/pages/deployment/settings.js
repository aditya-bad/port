// Settings: the strategy's parameters (editable only while paused, so a
// change can't land halfway through a trade), plus the deployment's own
// name, push notifications and whether it counts in reports.

import { api } from '../../core/api.js';
import { html, mount, actions, $ } from '../../core/dom.js';
import * as book from '../../data/book.js';
import { fieldsFor, renderFields, bindFields, validate, toConfig } from '../../ui/fields.js';
import { toggle } from '../../ui/components.js';
import { say } from '../../ui/toast.js';
import * as A from '../../actions.js';

export default {
  render(el, v0) {
    let draft = null, saving = false;
    const v = () => book.find(v0.id) || v0;

    function paint() {
      const d = v();
      const strat = book.strategy(d.strategy);
      const cfg = d.raw.config || {};
      const fields = fieldsFor(strat, cfg);
      // Keys the deployment never set run on the strategy's defaults.
      const effective = { ...(strat ? strat.default_config : {}), ...cfg };
      if (!draft) draft = { ...effective };
      const editable = d.rawStatus === 'paused';
      const E = editable ? validate(fields, draft) : {};
      const dirty = JSON.stringify(toConfigSafe(fields, draft)) !== JSON.stringify(toConfigSafe(fields, effective));
      const canSave = editable && dirty && !Object.keys(E).length && !saving;
      mount(el, html`
        <div class="pad-x tab-body grid-side" style="gap:48px">
          <div class="stack" style="gap:18px">
            ${d.rawStatus === 'active' ? html`<div class="note-box"><div class="grow"><b>Settings are locked while the deployment is running.</b>
                <div class="small mute">Pause it first so a change can’t land halfway through a trade.</div></div>
                <button type="button" class="btn btn-primary" data-act="pauseEdit">Pause to edit</button></div>` : ''}
            ${d.rawStatus === 'stopped' ? html`<div class="note-box"><div class="grow"><b>A stopped deployment can’t be changed.</b>
                <div class="small mute">Copy it as a new deployment to run these settings with changes.</div></div>
                <button type="button" class="btn btn-primary" data-act="copy">Copy as new</button></div>` : ''}
            <div data-fields>${renderFields(fields, draft, { editable, errors: E })}</div>
            <div class="row wrap" style="gap:10px">
              <button type="button" class="btn btn-primary" data-act="save" ${canSave ? '' : html`aria-disabled="true" data-reason="${!editable ? 'Pause the deployment to edit settings.' : Object.keys(E).length ? 'Fix the highlighted fields first.' : 'No changes to save.'}"`}>${saving ? 'Saving…' : 'Save settings'}</button>
              <button type="button" class="btn btn-quiet" data-act="reset" ${editable && strat ? '' : html`aria-disabled="true" data-reason="${editable ? 'No defaults known for this strategy.' : 'Pause the deployment to edit settings.'}"`}>Reset to strategy defaults</button>
              ${dirty ? html`<button type="button" class="btn btn-quiet" data-act="discard">Discard changes</button>` : ''}
            </div>
          </div>
          <div class="stack" style="gap:24px">
            <div class="stack" style="gap:10px">
              <h3 class="t-h4">About ${strat ? strat.label : d.strategy}</h3>
              <p class="mute pretty" style="margin:0">${strat ? strat.description : 'This strategy isn’t registered on the server, so it won’t trade.'}</p>
              ${strat ? html`<div class="kv-grid small"><span class="mute">Style</span><b>${strat.mode === 'intraday' ? 'Intraday' : 'Positional'}</b>
                ${strat.risk ? html`<span class="mute">Risk</span><b>${strat.risk}</b>` : ''}${strat.hold ? html`<span class="mute">Typical hold</span><b>${strat.hold}</b>` : ''}
                <span class="mono mute">Code</span><b class="mono" style="font-weight:500">${strat.name}</b></div>` : ''}
              <p class="small mute" style="margin:0">Saved settings take effect when you resume it.</p>
            </div>
            <div>
              <div class="sect ruled"><h3 class="t-h3">Deployment</h3></div>
              <div class="pref-row"><div class="col grow" style="gap:0"><b>Name</b><span class="tiny mute">Shown on the Desk, in alerts and in push notifications</span></div>
                <div class="row" style="gap:6px"><input class="input" style="width:220px" value="${d.name}" data-name aria-label="Deployment name"><button type="button" class="btn btn-sm" data-act="rename">Rename</button></div></div>
              <div class="pref-row"><div class="col grow" style="gap:0"><b>Push notifications</b><span class="tiny mute">A push to your devices for each entry and exit</span></div>
                ${toggle(d.raw.notifications_enabled !== false, 'notif', {}, 'Push notifications')}</div>
              <div class="pref-row"><div class="col grow" style="gap:0"><b>Count in reports</b><span class="tiny mute">Include it in Reports and account totals</span></div>
                ${toggle(d.inReports, 'reports', {}, 'Count in reports')}</div>
            </div>
          </div>
        </div>`);
    }
    function toConfigSafe(fields, values) { try { return toConfig(fields, values); } catch (e) { return values; } }

    const offFields = bindFields(el, (k, val, rerender) => {
      draft = { ...draft, [k]: val };
      if (rerender) paint();
      else {
        // Keep typing smooth: only refresh errors/buttons after a pause.
        clearTimeout(paint.t);
        paint.t = setTimeout(() => {
          const active = document.activeElement && document.activeElement.dataset.field;
          const pos = document.activeElement && document.activeElement.selectionStart;
          paint();
          if (active) { const n = $(`[data-field="${active}"]`, el); if (n) { n.focus(); try { n.setSelectionRange(pos, pos); } catch (e) {} } }
        }, 500);
      }
    });
    const off = actions(el, {
      pauseEdit: () => A.pause([v()]),
      copy: () => A.copyAsNew(v()),
      discard: () => { draft = null; paint(); },
      reset: (d, b) => {
        if (b.dataset.reason) return say(b.dataset.reason);
        const s = book.strategy(v().strategy);
        draft = { ...draft, ...s.default_config };
        paint();
      },
      save: async (d, b) => {
        if (b.dataset.reason) return say(b.dataset.reason);
        const fields = fieldsFor(book.strategy(v().strategy), v().raw.config || {});
        saving = true; paint();
        try {
          await api.update(v().id, { config: toConfig(fields, draft) });
          await book.load(['deployments']);
          draft = null;
          say('Settings saved. They take effect when you resume it.');
        } catch (e) { say(`Couldn’t save: ${e.message}`); }
        saving = false; paint();
      },
      rename: async () => {
        const name = $('[data-name]', el).value.trim();
        if (!name || name === v().name) return say(name ? 'That’s already its name.' : 'Give it a name.');
        try { await api.update(v().id, { deployment_name: name }); await book.load(['deployments']); say(`Renamed to ${name}.`); } catch (e) { say(`Couldn’t rename: ${e.message}`); }
      },
      notif: async () => {
        const on = v().raw.notifications_enabled !== false;
        try { await api.update(v().id, { notifications_enabled: !on }); await book.load(['deployments']); say(on ? 'Push notifications off for this deployment.' : 'Push notifications on.'); } catch (e) { say(e.message); }
      },
      reports: async () => {
        try { await api.update(v().id, { include_in_reports: !v().inReports }); await book.load(['deployments']); paint(); } catch (e) { say(e.message); }
      },
    });
    paint();
    return () => { off(); offFields(); clearTimeout(paint.t); };
  },
};
