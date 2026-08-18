// live_deploy — minimal pass-through service worker (Step 71).
//
// Exists ONLY to satisfy Android Chrome's installability requirement
// for a real standalone install (a "WebAPK") — manifest.json + icons
// alone (Step 70) get you as far as a browser-badged "Create Shortcut"
// that still opens inside Chrome's own UI, not a real app; Android
// specifically also requires a registered service worker with a fetch
// handler before it will generate the real thing.
//
// Deliberately does NO caching — every request just falls straight
// through to the network, untouched. This app is inherently live-
// data-dependent (ticks, P&L, positions change continuously), so
// caching responses would only be a staleness bug waiting to happen
// for zero actual offline benefit. This is the emptiest possible
// service worker that still counts.

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
