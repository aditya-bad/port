// Deployment actions shared by the Strategies list and the strategy page.
// Every destructive action states exactly what will happen first.
import { html } from './util.js';
import { api } from './api.js';
import { confirm, modal, toast, toastError } from './ui.js';

export async function pause(d) {
  try { await api.pause(d.id); toast(`${d.deployment_name} paused`, 'Open positions stay open but are no longer managed.'); return true; }
  catch (e) { toastError(e, 'Couldn’t pause'); return false; }
}
export async function resume(d) {
  try { await api.resume(d.id); toast(`${d.deployment_name} resumed`, 'It picks up from its saved state on the next tick.'); return true; }
  catch (e) { toastError(e, 'Couldn’t resume'); return false; }
}
export async function flatten(d) {
  const ok = await confirm({
    title: `Close ${d.deployment_name}'s open positions?`,
    description: 'Every open leg is closed at its last traded price and the deployment is paused, so it doesn’t re-enter straight away. Resume it whenever you’re ready.',
    confirmLabel: 'Close positions', danger: true,
  });
  if (!ok) return false;
  try { const r = await api.flatten(d.id); toast('Positions closed', r && r.positions_closed != null ? `${r.positions_closed} leg(s) closed · ${d.deployment_name} is paused.` : ''); return true; }
  catch (e) { toastError(e, 'Couldn’t close positions'); return false; }
}
export async function stop(d, hasOpen) {
  const choice = await modal({
    title: `Stop ${d.deployment_name}?`,
    description: hasOpen
      ? 'It still has open positions. Stopping is permanent — a stopped deployment can’t be resumed.'
      : 'Stopping is permanent — a stopped deployment can’t be resumed. Its history stays.',
    actions: [
      { label: 'Cancel', value: null },
      ...(hasOpen ? [{ label: 'Close positions & stop', value: 'force', kind: 'danger' }] : [{ label: 'Stop', value: 'stop', kind: 'danger' }]),
    ],
  });
  if (!choice) return false;
  try { await api.stop(d.id, choice === 'force'); toast(`${d.deployment_name} stopped`); return true; }
  catch (e) { toastError(e, 'Couldn’t stop'); return false; }
}
export async function remove(d) {
  const ok = await confirm({
    title: `Delete ${d.deployment_name}?`,
    description: 'This permanently deletes the deployment and every position, fill and snapshot it recorded. This can’t be undone.',
    confirmLabel: 'Delete permanently', danger: true,
  });
  if (!ok) return false;
  try { await api.remove(d.id); toast(`${d.deployment_name} deleted`); return true; }
  catch (e) { toastError(e, 'Couldn’t delete'); return false; }
}
export async function removeTrades(d) {
  const ok = await confirm({
    title: 'Remove the current trade?',
    description: html`This wipes the current trade${d.mode === 'positional' ? ' cycle (every leg, roll and adjustment in it)' : ''} as if it never happened — positions, fills and their P&amp;L — and restores the cash. Use it for trades placed off bad data.`,
    confirmLabel: 'Remove trade', danger: true,
  });
  if (!ok) return false;
  try { await api.removeTrades(d.id); toast('Trade removed'); return true; }
  catch (e) { toastError(e, 'Couldn’t remove the trade'); return false; }
}
export async function flattenAll(count) {
  const ok = await confirm({
    title: 'Close every open position?',
    description: `Closes all ${count} open leg(s) across every strategy at their last traded price, and pauses those strategies. Use this as a panic button.`,
    confirmLabel: 'Close everything', danger: true,
  });
  if (!ok) return false;
  try { await api.flattenAll(); toast('All positions closed'); return true; }
  catch (e) { toastError(e, 'Couldn’t close everything'); return false; }
}

export function actionItems(d, { hasOpen, refresh, openHref = true }) {
  const run = fn => async () => { if (await fn()) refresh(); };
  return [
    openHref && { label: 'Open', href: `#/s/${d.id}` },
    d.status === 'active' && { label: 'Pause', onClick: run(() => pause(d)) },
    d.status === 'paused' && { label: 'Resume', onClick: run(() => resume(d)) },
    d.status !== 'stopped' && hasOpen && { label: 'Close open positions', onClick: run(() => flatten(d)) },
    d.status !== 'stopped' && { sep: true },
    d.status !== 'stopped' && { label: 'Stop…', danger: true, onClick: run(() => stop(d, hasOpen)) },
    d.status === 'stopped' && { label: 'Delete…', danger: true, onClick: run(() => remove(d)) },
  ];
}
