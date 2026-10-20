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
