// Generic strategy-config form: one input per key, typed from the
// strategy's own default value (bool → switch, number → number,
// "HH:MM" → time, list/object → JSON), with a raw-JSON mode for
// anything the simple form can't express.
import { html, mount, humanize } from './util.js';

const HHMM = /^\d{1,2}:\d{2}$/;
function kindOf(def, val) {
  const v = def !== undefined && def !== null ? def : val;
  if (typeof v === 'boolean') return 'bool';
  if (typeof v === 'number') return 'number';
  if (typeof v === 'string' && HHMM.test(v)) return 'time';
  if (v !== null && typeof v === 'object') return 'json';
  if (v === null && typeof val === 'string' && HHMM.test(val)) return 'time';
  return 'text';
}

// Renders into `el`. Returns { read(): config | throws, setValues(cfg) }.
export function configForm(el, { defaults = {}, values = {}, disabled = false }) {
  let mode = 'form';
  let current = { ...defaults, ...values };
  const keys = () => Object.keys({ ...defaults, ...current });

  const fieldHtml = k => {
    const kind = kindOf(defaults[k], current[k]);
    const v = current[k];
    const id = `cf_${k}`;
    if (kind === 'bool') {
      return html`<div class="field"><span>${humanize(k)}</span>
        <label class="switch"><input type="checkbox" id="${id}" data-k="${k}" data-kind="bool" ${v ? 'checked' : ''} ${disabled ? 'disabled' : ''}><i></i></label></div>`;
    }
    if (kind === 'json') {
      return html`<label class="field" style="grid-column:1/-1"><span>${humanize(k)}</span>
        <textarea class="input" id="${id}" data-k="${k}" data-kind="json" rows="1" style="min-height:36px" ${disabled ? 'disabled' : ''}>${JSON.stringify(v)}</textarea></label>`;
    }
    const type = kind === 'number' ? 'number' : kind === 'time' ? 'time' : 'text';
    const shown = kind === 'time' && typeof v === 'string' && HHMM.test(v) ? v.padStart(5, '0') : v;
    const placeholder = v === null || v === undefined ? 'Not set' : '';
    return html`<label class="field"><span>${humanize(k)}</span>
      <input class="input" id="${id}" type="${type}" ${kind === 'number' ? 'step="any"' : ''} data-k="${k}" data-kind="${kind}"
        value="${shown === null || shown === undefined ? '' : shown}" placeholder="${placeholder}" ${disabled ? 'disabled' : ''}></label>`;
  };

  const paint = () => {
    mount(el, html`
      <div class="actions" style="justify-content:space-between;margin-bottom:12px">
        <span class="sub small">${keys().length} settings${disabled ? ' · read-only' : ''}</span>
        <div class="seg" role="group" aria-label="Editor"><button type="button" data-mode="form" class="${mode === 'form' ? 'on' : ''}">Form</button><button type="button" data-mode="json" class="${mode === 'json' ? 'on' : ''}">JSON</button></div>
      </div>
      ${mode === 'form'
        ? html`<div class="form-grid">${keys().map(fieldHtml)}</div>`
        : html`<textarea class="input" data-raw rows="16" ${disabled ? 'disabled' : ''}>${JSON.stringify(current, null, 2)}</textarea>`}`);
    el.querySelectorAll('[data-mode]').forEach(b => {
      b.onclick = () => {
        if (b.dataset.mode === mode) return;
        try { current = read(); } catch (e) { if (!disabled) { alertBad(e); return; } }
        mode = b.dataset.mode;
        paint();
      };
    });
  };
  const alertBad = e => {
    const n = document.createElement('div');
    n.className = 'alert bad';
    n.style.marginTop = '10px';
    n.textContent = e.message;
    el.appendChild(n);
    setTimeout(() => n.remove(), 5000);
  };

  function read() {
    if (mode === 'json') {
      const raw = el.querySelector('[data-raw]').value;
      try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Config must be a JSON object');
        return parsed;
      } catch (e) { throw new Error(`Invalid JSON: ${e.message}`); }
    }
    const out = { ...current };
    el.querySelectorAll('[data-k]').forEach(inp => {
      const k = inp.dataset.k, kind = inp.dataset.kind;
      if (kind === 'bool') out[k] = inp.checked;
      else if (kind === 'number') {
        if (inp.value === '') out[k] = null;
        else {
          const n = Number(inp.value);
          if (!Number.isFinite(n)) throw new Error(`${humanize(k)} must be a number`);
          out[k] = n;
        }
      } else if (kind === 'json') {
        try { out[k] = JSON.parse(inp.value); } catch (e) { throw new Error(`${humanize(k)} isn't valid JSON`); }
      } else out[k] = inp.value === '' ? null : inp.value;
    });
    return out;
  }

  paint();
  return {
    read,
    setValues(cfg) { current = { ...defaults, ...cfg }; paint(); },
  };
}
