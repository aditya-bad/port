// Strategy settings as a form, from the field metadata /v2/strategies
// serves (label, help, type, prefix/suffix, choices). Used by a
// deployment's Settings tab and the New deployment wizard.

import { html } from '../core/dom.js';

const HHMM = /^([01]?\d|2[0-3]):[0-5]\d$/;

// Every key the deployment actually has, described when the catalogue
// knows it and inferred otherwise.
export function fieldsFor(strategy, config = {}) {
  const known = strategy ? strategy.fields : [];
  const keys = new Set(known.map(f => f.key));
  const extra = Object.keys(config).filter(k => !keys.has(k)).map(k => ({
    key: k, label: k.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase()), help: '', type: inferType(config[k]),
    default: undefined, choices: null, prefix: '', suffix: '',
  }));
  return [...known, ...extra];
}
function inferType(v) {
  if (typeof v === 'boolean') return 'bool';
  if (typeof v === 'number') return 'number';
  if (typeof v === 'string' && HHMM.test(v)) return 'time';
  if (v !== null && typeof v === 'object') return 'json';
  return 'text';
}

export function validate(fields, values) {
  const E = {};
  for (const f of fields) {
    const v = values[f.key];
    if (v == null || v === '') continue;   // unset: the strategy's default applies
    if (f.type === 'number' && !Number.isFinite(Number(v))) E[f.key] = 'Enter a number.';
    if (f.type === 'time' && v && !HHMM.test(String(v))) E[f.key] = 'Use HH:MM, 24-hour IST.';
    if (f.type === 'json' && typeof v === 'string') { try { JSON.parse(v); } catch (e) { E[f.key] = 'Not valid JSON.'; } }
  }
  return E;
}
// Draft values (strings from inputs) → the config object to save.
export function toConfig(fields, values) {
  const out = {};
  for (const f of fields) {
    let v = values[f.key];
    if (f.type === 'number') v = v === '' || v == null ? null : Number(v);
    else if (f.type === 'json' && typeof v === 'string') v = JSON.parse(v);
    else if (f.type === 'time' && typeof v === 'string' && /^\d:\d\d$/.test(v)) v = `0${v}`;
    out[f.key] = v;
  }
  return out;
}

export function renderFields(fields, values, { editable = true, errors = {} } = {}) {
  return html`<div class="fields">${fields.map(f => {
    const v = values[f.key], err = errors[f.key];
    let control;
    if (f.type === 'bool') {
      control = html`<button type="button" class="toggle" role="switch" aria-checked="${!!v}" aria-label="${f.label}" data-field="${f.key}" data-kind="bool" ${editable ? '' : html`aria-disabled="true"`} style="margin-top:6px"></button>`;
    } else if (f.type === 'choice') {
      control = html`<div class="seg" role="group" aria-label="${f.label}">${f.choices.map(c => html`
        <button type="button" class="${v === c ? 'on' : ''}" aria-pressed="${v === c}" data-field="${f.key}" data-kind="choice" data-v="${c}" ${editable ? '' : 'disabled'}>${c.replace(/_/g, ' ')}</button>`)}</div>`;
    } else if (f.type === 'json') {
      control = html`<textarea class="input ${err ? 'bad' : ''}" rows="1" data-field="${f.key}" data-kind="json" ${editable ? '' : 'readonly'}>${typeof v === 'string' ? v : JSON.stringify(v)}</textarea>`;
    } else {
      control = html`<div class="input-group ${err ? 'bad' : ''} ${editable ? '' : 'ro'}">
        ${f.prefix ? html`<span>${f.prefix}</span>` : ''}
        <input data-field="${f.key}" data-kind="${f.type}" value="${v == null ? '' : v}" ${editable ? '' : 'readonly'}
          ${f.type === 'number' ? html`inputmode="decimal"` : ''} ${f.type === 'time' ? html`placeholder="HH:MM" inputmode="numeric"` : ''} aria-label="${f.label}">
        ${f.suffix ? html`<span>${f.suffix}</span>` : ''}</div>`;
    }
    return html`<div class="field-row ${editable ? '' : 'ro'}">
      <div class="col"><b>${f.label}</b>${f.help ? html`<span class="tiny mute">${f.help}</span>` : ''}</div>
      <div class="col" style="gap:5px">${control}${err ? html`<span class="err">${err}</span>` : ''}</div>
    </div>`;
  })}</div>`;
}

// Wire inputs to a draft object; calls onChange(key, value, rerender?).
export function bindFields(root, onChange) {
  const input = e => {
    const el = e.target.closest('[data-field]');
    if (!el || el.readOnly) return;
    onChange(el.dataset.field, el.value, false);
  };
  const click = e => {
    const el = e.target.closest('[data-field]');
    if (!el || el.disabled || el.getAttribute('aria-disabled') === 'true') return;
    if (el.dataset.kind === 'bool') onChange(el.dataset.field, el.getAttribute('aria-checked') !== 'true', true);
    else if (el.dataset.kind === 'choice') onChange(el.dataset.field, el.dataset.v, true);
  };
  root.addEventListener('input', input);
  root.addEventListener('click', click);
  return () => { root.removeEventListener('input', input); root.removeEventListener('click', click); };
}
