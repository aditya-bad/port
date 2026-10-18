// Installing Linck as an app (the PWA: /manifest.json + /sw.js).
// Chrome/Edge/Android fire `beforeinstallprompt` once, early; it's kept
// here so Account can offer an "Install" button later. iOS Safari has
// no prompt, so Account shows the Add to Home Screen steps instead.

import { emit } from '../core/store.js';

let deferred = null;

export const isStandalone = () =>
  (window.matchMedia && matchMedia('(display-mode: standalone)').matches) || window.navigator.standalone === true;
export const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

export function installState() {
  if (isStandalone()) return 'installed';
  if (deferred) return 'prompt';
  if (isIOS()) return 'ios';
  return 'unavailable';
}

export async function promptInstall() {
  if (!deferred) return false;
  const ev = deferred;
  deferred = null;
  ev.prompt();
  const choice = await ev.userChoice.catch(() => null);
  emit('install');
  return !!choice && choice.outcome === 'accepted';
}

export function startInstall() {
  window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); deferred = e; emit('install'); });
  window.addEventListener('appinstalled', () => { deferred = null; emit('install'); });
  // Linck registers the service worker itself (push + installability),
  // instead of relying on the classic UI having done it.
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
}
