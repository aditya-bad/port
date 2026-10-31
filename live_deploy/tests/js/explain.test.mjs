import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { sentences, readMs, glide, pickVoice } from '../../static/linck/js/data/explain.js';

test('said one sentence at a time; decimals and rupees never split one', () => {
  assert.deepEqual(sentences('NIFTY 50 was at 24,900, and India VIX at 13.2. It sold the 25,300 call at ₹121.5. Done!'),
    ['NIFTY 50 was at 24,900, and India VIX at 13.2.', 'It sold the 25,300 call at ₹121.5.', 'Done!']);
  assert.deepEqual(sentences('No full stop'), ['No full stop']);
  assert.deepEqual(sentences(''), []);
});

test('without a voice a beat stays up as long as it takes to read', () => {
  assert.equal(readMs('one two'), 2500);
  assert.equal(readMs(Array(26).fill('w').join(' ')), 10000);
});

test('the playhead glides to the moment, in at most 40 steps', () => {
  assert.deepEqual(glide(3, 3), [3]);
  assert.deepEqual(glide(0, 4), [1, 2, 3, 4]);
  const g = glide(0, 500);
  assert.equal(g.length, 40);
  assert.equal(g[g.length - 1], 500);
  assert.deepEqual(glide(10, 7), [9, 8, 7]);
});

test('an Indian English voice first', () => {
  const v = [{ lang: 'en-US', name: 'a' }, { lang: 'en-IN', name: 'b' }, { lang: 'hi-IN', name: 'c' }];
  assert.equal(pickVoice(v).name, 'b');
  assert.equal(pickVoice([{ lang: 'en-US', name: 'a' }]).name, 'a');
  assert.equal(pickVoice([{ lang: 'fr-FR' }]), null);
});
