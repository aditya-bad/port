import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { html } from '../../static/linck/js/core/dom.js';

test('false renders as nothing, so conditional content works', () => {
  assert.equal(String(html`<p>${false && html`<b>x</b>`}</p>`), '<p></p>');
  assert.equal(String(html`<p class="${false}">`), '<p class="">');
});

test('an ARIA state that is off says "false", not empty (empty means it isn’t a toggle at all)', () => {
  const off = false, on = true;
  assert.equal(String(html`<button aria-pressed="${off}">`), '<button aria-pressed="false">');
  assert.equal(String(html`<button aria-checked="${off}">`), '<button aria-checked="false">');
  assert.equal(String(html`<button aria-selected="${off}">`), '<button aria-selected="false">');
  assert.equal(String(html`<button aria-expanded="${off}">`), '<button aria-expanded="false">');
  assert.equal(String(html`<button class="x" aria-pressed="${on}">`), '<button class="x" aria-pressed="true">');
  assert.equal(String(html`<button aria-label="${false}">`), '<button aria-label="">', 'only the state attributes');
  assert.equal(String(html`<i aria-hidden="true">${false}</i>`), '<i aria-hidden="true"></i>');
});

test('a link inside a clickable row goes where it says; the row acts on any other click', async () => {
  const { actions } = await import('../../static/linck/js/core/dom.js');
  let h;
  const root = { addEventListener: (t, fn) => { h = fn; }, removeEventListener() {}, contains: () => true };
  const row = { dataset: { act: 'pick' }, getAttribute: () => null };
  const link = { closest: s => (s === '[data-act]' ? row : s === 'a[href]' ? link : null) };   // a Repaired mark in a Trades row
  const cell = { closest: s => (s === '[data-act]' ? row : null) };
  row.contains = x => x === link || x === cell;
  const hit = [];
  actions(root, { pick: () => hit.push('pick'), go: () => hit.push('go') });
  const click = target => { const e = { target, prevented: false, preventDefault() { this.prevented = true; } }; h(e); return e; };
  let e = click(link);
  assert.deepEqual(hit, []); assert.equal(e.prevented, false, 'the link navigates');
  e = click(cell);
  assert.deepEqual(hit, ['pick']); assert.equal(e.prevented, true);
  // a link that is itself the action still acts
  const own = { dataset: { act: 'go' }, getAttribute: () => null, contains: () => true };
  own.closest = () => own;
  click(own);
  assert.deepEqual(hit, ['pick', 'go']);
});
