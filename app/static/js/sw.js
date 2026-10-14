// Minimal service worker: lets the app be installed to the home screen.
// Attendance data is never cached (punching must always hit the server).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
