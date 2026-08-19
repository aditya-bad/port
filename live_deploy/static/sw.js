// live_deploy — service worker: installability (Step 71) + Web Push
// (Step 85).
//
// Installability: satisfies Android Chrome's requirement for a real
// standalone install (a "WebAPK") — manifest.json + icons alone
// (Step 70) get you as far as a browser-badged "Create Shortcut" that
// still opens inside Chrome's own UI, not a real app; Android
// specifically also requires a registered service worker with a fetch
// handler before it will generate the real thing.
//
// Fetch handling deliberately does NO caching — every request just
// falls straight through to the network, untouched. This app is
// inherently live-data-dependent (ticks, P&L, positions change
// continuously), so caching responses would only be a staleness bug
// waiting to happen for zero actual offline benefit.
//
// Push: this is the piece that makes a notification arrive even when
// no tab is open and Chrome isn't running in the foreground — the OS
// wakes this service worker specifically to handle the incoming push,
// independent of the app itself. See static/js/account.js for the
// subscribe side (PushManager.subscribe() + POST /notifications/
// subscribe) and app/notifications.py for the send side.

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});

self.addEventListener('push', (event) => {
  // Payload shape set by app/notifications.py's own _build_payload —
  // keep the two in sync if this changes. event.data can genuinely be
  // absent (a push with no payload is valid per spec, e.g. a bare
  // "wake up and check" ping) — fall back to something generic rather
  // than let a malformed/empty payload throw and silently drop the
  // notification entirely.
  let title = 'live_deploy';
  let body = '';
  try {
    const payload = event.data ? event.data.json() : {};
    title = payload.title || title;
    body = payload.body || '';
  } catch (e) {
    body = event.data ? event.data.text() : '';
  }
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      tag: 'live-deploy-execution',   // a second notification while the first is still showing REPLACES it rather than stacking -- this is a status ping, not a message thread
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  // Focus an already-open tab if one exists, otherwise open a new one
  // — either way lands on the Deployments list, the most useful place
  // to land after "something entered/exited."
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow('/#/deployments');
    })
  );
});
