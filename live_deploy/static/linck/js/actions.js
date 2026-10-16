// Every action on a deployment, with the confirmation the spec asks for:
//   Reversible (pause, resume): no dialog, toast with Undo.
//   Order-placing (close positions, stop): dialog listing the exact orders.
//   Irreversible (delete, remove trade, close everything): typed confirmation.

import { api } from './core/api.js';
import { html } from './core/dom.js';
import { inr, plural, tone, price } from './core/format.js';
import { go } from './core/router.js';
import * as book from './data/book.js';
import { kite, loginWithKite, isLive } from './data/live.js';
import { openDialog } from './ui/dialog.js';
import { say } from './ui/toast.js';

const after = kinds => book.load(kinds || ['deployments', 'positions', 'session', 'book']);
// Undo acts on the state after the action, not the objects captured before it.
const fresh = list => list.map(v => book.find(v.id)).filter(Boolean);

// The one action a row offers, by state.
export function primary(v) {
  if (v.rawStatus === 'active') return v.status === 'error' ? ['Review', () => go(`d/${v.id}`)] : ['Pause', () => pause([v])];
  if (v.rawStatus === 'paused') return ['Resume', () => resume([v])];
  return ['Copy as new', () => copyAsNew(v)];
}

export async function pause(list, { quiet = false } = {}) {
  const L = list.filter(v => v.rawStatus === 'active');
  if (!L.length) return say('Nothing in the selection is running.');
  const done = [];
  for (const v of L) {
    try { await api.pause(v.id); done.push(v); } catch (e) { say(`Couldn’t pause ${v.name}: ${e.message}`); }
  }
  await after(['deployments']);
  if (!done.length || quiet) return;
  say(`${done.length === 1 ? done[0].name : plural(done.length, 'deployment')} paused.`, () => resume(fresh(done), { quiet: true }));
}
export async function resume(list, { quiet = false } = {}) {
  const L = list.filter(v => v.rawStatus === 'paused');
  if (!L.length) return say('Nothing in the selection is paused.');
  const done = [];
  for (const v of L) {
    try { await api.resume(v.id); done.push(v); } catch (e) { say(`Couldn’t resume ${v.name}: ${e.message}`); }
  }
  await after(['deployments']);
  if (!done.length || quiet) return;
  const who = done.length === 1 ? done[0].name : plural(done.length, 'deployment');
  say(`${who} resumed.${isLive() ? '' : ' It trades once Kite is connected.'}`, () => pause(fresh(done), { quiet: true }));
}

// The exit orders for a set of legs, shorts first (longs last keeps
// every short hedged until it's gone).
export function exitOrders(legs) {
  return [...legs].sort((a, b) => (a.side === 'short' ? 0 : 1) - (b.side === 'short' ? 0 : 1)).map(p => {
    const pnl = book.legPnl(p);
    const px = book.legPrice(p);
    return { t: `${p.side === 'short' ? 'BUY' : 'SELL'} ${Number(p.qty)} ${p.symbol}${px != null ? ` @ ${price(px)}` : ''}`, v: inr(pnl), tone: tone(pnl) };
  });
}

export function closeLegs(list) {
  const L = list.filter(v => v.legs.length && v.rawStatus !== 'stopped');
  if (!L.length) return say('No open legs to close.');
  const legs = L.flatMap(v => v.legs);
  const amt = L.reduce((a, v) => a + book.live(v).unreal, 0);
  const one = L.length === 1 ? L[0] : null;
  openDialog({
    kicker: 'CONFIRM',
    title: one ? `Close ${plural(legs.length, 'leg')} of ${one.name}?` : `Close positions in ${plural(L.length, 'deployment')}?`,
    lines: [
      `Places ${plural(legs.length, 'market order')}, shorts first. Books about ${inr(amt)} at current prices.`,
      one && one.rawStatus === 'paused' ? 'The deployment stays paused.' : 'The deployment keeps running and can enter a new cycle.',
      !isLive() && 'Kite is not connected, so legs close at their last known prices.',
    ],
    orders: exitOrders(legs),
    okLabel: `Close ${plural(legs.length, 'leg')}`,
    onOk: async () => {
      let n = 0;
      for (const v of L) n += (await api.flatten(v.id)).positions_closed || 0;
      await after();
      say(`${plural(n, 'leg')} closed, about ${inr(amt)} booked.`);
    },
  });
}

export function stop(v) {
  if (v.rawStatus === 'stopped') return say('Already stopped.');
  const n = v.legs.length;
  openDialog({
    kicker: 'CONFIRM',
    title: `Stop ${v.name}?`,
    lines: ['A stopped deployment stops managing positions and can’t be resumed. Its history is kept, and you can copy its settings into a new one.'],
    check: n ? { label: 'Close open positions first (recommended)', value: true } : null,
    render: s => html`<p class="mute" style="margin:0">${n
      ? (s.checked ? `Its ${plural(n, 'open leg')} close first at market.` : `Its ${plural(n, 'open leg')} stay open and unmanaged. You’ll have to close them yourself.`)
      : 'It has no open legs.'}</p>
      ${n && s.checked ? html`<div class="orders">${exitOrders(v.legs).map(o => html`<div><span>${o.t}</span><span class="${o.tone}">${o.v}</span></div>`)}</div>` : ''}`,
    okLabel: 'Stop deployment',
    onOk: async s => {
      await api.stop(v.id, n ? s.checked : false);
      await after();
      say(`${v.name} stopped.`);
    },
  });
}

function typed(v, { kicker = 'IRREVERSIBLE', title, lines, okLabel, run }) {
  openDialog({
    kicker, hot: true, title, lines,
    input: { label: 'Type the deployment name to confirm', placeholder: v.name, mono: false },
    armed: s => s.text.trim() === v.name,
    okLabel, onOk: run,
  });
}
export function removeTrade(v) {
  typed(v, {
    title: 'Remove the current trade from the record?',
    lines: [
      `Erases the open cycle of ${v.name}${v.legs.length ? ` (${plural(v.legs.length, 'leg')})` : ''} from P&L, reports, leaderboards and history.`,
      'Use it only to clean up a bad or test entry.',
    ],
    okLabel: 'Remove trade',
    run: async () => { await api.removeTrades(v.id); await after(); say('Current trade removed from the record.'); },
  });
}
export function remove(v) {
  if (v.rawStatus !== 'stopped') return say('Stop it first.');
  typed(v, {
    title: `Delete ${v.name}?`,
    lines: [`Deletes the deployment and all ${plural(v.trades.length, 'trade')} of history. It disappears from reports and leaderboards. This can’t be undone.`],
    okLabel: 'Delete deployment',
    run: async () => { await api.remove(v.id); await after(); go('deployments'); say(`${v.name} deleted.`); },
  });
}

export function addTag(list) {
  const known = book.tags().map(t => t.name);
  openDialog({
    kicker: 'TAG',
    title: `Add a tag to ${list.length === 1 ? list[0].name : plural(list.length, 'deployment')}`,
    chips: { options: known, value: null },
    input: { label: 'Or create a new tag', placeholder: 'e.g. Expiry week', mono: false },
    armed: s => !!(s.text.trim() || s.pick),
    okLabel: 'Add tag',
    onOk: async s => {
      const name = s.text.trim() || s.pick;
      if (!known.includes(name)) await api.createTag(name);
      for (const v of list) {
        if (!v.tags.includes(name)) await api.update(v.id, { tags: [...v.tags, name] });
      }
      await book.load(['deployments', 'tags']);
      say(`Tagged ${list.length === 1 ? list[0].name : plural(list.length, 'deployment')} with “${name}”.`);
    },
  });
}
export async function removeTag(v, name) {
  await api.update(v.id, { tags: v.tags.filter(t => t !== name) });
  await book.load(['deployments']);
  say(`Removed “${name}” from ${v.name}.`, () => api.update(v.id, { tags: v.tags }).then(() => book.load(['deployments'])));
}

export function closeEverything() {
  const all = book.deployments().filter(v => v.rawStatus !== 'stopped');
  const holding = all.filter(v => v.legs.length);
  const legs = holding.flatMap(v => v.legs);
  const running = all.filter(v => v.rawStatus === 'active').length;
  const amt = holding.reduce((a, v) => a + book.live(v).unreal, 0);
  const orders = exitOrders(legs);
  openDialog({
    kicker: 'EMERGENCY', hot: true, tone: 'warn',
    title: 'Close every open position, everywhere',
    lines: [
      !isLive() && 'Kite is not connected, so legs close at their last known prices.',
      `Places ${plural(legs.length, 'market order')} across ${plural(holding.length, 'deployment')}, longs last so no short is left without its hedge. Then pauses all ${running} running deployments so nothing re-enters.`,
      `Books about ${inr(amt)} at current prices. Fills may differ. Nothing is deleted.`,
    ],
    orders: orders.slice(0, 8).concat(orders.length > 8 ? [{ t: `+ ${orders.length - 8} more orders`, v: '', tone: 'flat' }] : []),
    input: { label: 'Type CLOSE ALL to arm', placeholder: 'CLOSE ALL' },
    armed: s => s.text.trim().toUpperCase() === 'CLOSE ALL' && (legs.length > 0 || running > 0),
    okLabel: legs.length ? `Close ${plural(legs.length, 'leg')} now` : `Pause ${running} deployments`,
    onOk: async () => {
      const r = await api.closeEverything();
      await after();
      say(`${plural(r.positions_closed, 'exit order')} filled. ${plural(r.deployments_paused, 'deployment')} paused.${r.errors.length ? ` ${r.errors.length} failed; check them.` : ''}`);
    },
  });
}

export function kiteDialog() {
  if (kite.state === 'connected') {
    return openDialog({
      kicker: 'BROKER', tone: 'good', title: 'Kite is connected',
      lines: ['Prices are streaming and deployments can enter, adjust and exit.', 'Kite logins expire every morning at about 08:00. Reconnect before 09:15 so deployments can enter on time.'],
      cancelLabel: 'Close',
    });
  }
  openDialog({
    kicker: 'BROKER', title: 'Connect your Zerodha Kite account',
    lines: [
      'You’ll be sent to Zerodha to log in with your Kite ID, password and TOTP. Linck never sees your password.',
      'Once connected, prices go live and deployments can enter, adjust and exit. The session lasts until about 08:00 tomorrow.',
    ],
    okLabel: 'Log in with Kite',
    onOk: async () => {
      const d = openDialog({
        kicker: 'BROKER', title: 'Connecting to Kite', spin: 'Waiting for Zerodha to confirm your login…',
        lines: ['A Zerodha login window opened. Finish logging in there.'],
        cancelLabel: 'Cancel',
      });
      try {
        await loginWithKite(ok => {
          d.close();
          say(ok ? 'Kite connected. Prices are live and deployments resume managing positions.' : 'Kite login didn’t finish. Prices stay frozen.');
        });
      } catch (e) {
        d.close();
        say(`Couldn’t start the Kite login: ${e.message}`);
      }
      return false;
    },
  });
}

export function copyAsNew(v) {
  import('./pages/wizard.js').then(m => m.openWizard({ copy: v }));
}
export function newDeployment(strategyName) {
  import('./pages/wizard.js').then(m => m.openWizard({ strategy: strategyName }));
}

// Row/page "···" menus share these entries.
export function menuItems(v) {
  const stopped = v.rawStatus === 'stopped';
  return [
    { l: 'Open', h: 'Now, performance, trades, settings', run: () => go(`d/${v.id}`) },
    { l: 'Copy as new…', h: 'Start a new deployment with these settings', run: () => copyAsNew(v) },
    { l: 'Add tag…', h: 'Group it with others', run: () => addTag([v]) },
    { l: 'Close positions…', h: v.legs.length ? (stopped ? 'Stopped deployments can’t place orders' : `${plural(v.legs.length, 'leg')}, keeps running`) : 'No open legs', run: () => closeLegs([v]), off: !v.legs.length || stopped },
    { l: 'Stop…', h: stopped ? 'Already stopped' : 'Stops managing; option to close first', run: () => stop(v), off: stopped },
    { l: 'Remove current trade…', h: v.legs.length ? 'Erase the open cycle from the record' : 'No open cycle', run: () => removeTrade(v), off: !v.legs.length },
    { l: 'Delete…', h: stopped ? 'Removes it and its history' : 'Stop it first', run: () => remove(v), off: !stopped },
  ];
}
