// SBG Smart Attendance High-Performance Service Worker
// Automatically caches static styles, scripts, and shell assets for sub-millisecond page loads.
// Dynamic attendance, biometric scans, and live sync operations are never stale and always hit the cloud.

const CACHE_NAME = 'sbg-static-v5';
const PRECACHE_ASSETS = [
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/img/icon.svg',
  '/static/manifest.json'
];

self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE_ASSETS)).catch(() => {})
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);

  // 1. Static asset cache-first with background revalidation
  if (url.origin === location.origin && url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then(cached => {
        const fetchPromise = fetch(req).then(networkResponse => {
          if (networkResponse && networkResponse.status === 200) {
            const clone = networkResponse.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(req, clone));
          }
          return networkResponse;
        }).catch(() => null);

        return cached || fetchPromise;
      })
    );
    return;
  }

  // 2. All mutation, biometric punches, APIs, and HTML documents use direct network
  return;
});
