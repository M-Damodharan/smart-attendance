// SBG Smart Attendance High-Performance Service Worker
// Automatically caches static styles, scripts, and shell assets for sub-millisecond page loads.
// Core application logic (JS, CSS) uses network-first to ensure live bug fixes take effect immediately.

const CACHE_NAME = 'sbg-static-v9';
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

  // 1. Dynamic attendance, biometric scans, and live sync operations are never cached
  if (url.pathname.startsWith('/api/') || req.method !== 'GET') {
    return;
  }

  // 2. Core JS/CSS logic files: Network-first to always run the latest bug fixes, fallback to cache offline
  if (url.origin === location.origin && (url.pathname.includes('/js/') || url.pathname.includes('/css/'))) {
    event.respondWith(
      fetch(req).then(networkResponse => {
        if (networkResponse && networkResponse.status === 200) {
          const clone = networkResponse.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(req, clone));
        }
        return networkResponse;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // 3. Cache-first for images, fonts, and icons
  if (url.origin === location.origin && url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then(cached => cached || fetch(req).then(networkResponse => {
        if (networkResponse && networkResponse.status === 200) {
          const clone = networkResponse.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(req, clone));
        }
        return networkResponse;
      }).catch(() => null))
    );
    return;
  }
});
