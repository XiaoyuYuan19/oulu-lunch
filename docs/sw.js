const CACHE = 'oulu-lunch-v12';
const STATIC = ['manifest.webmanifest', 'icons/icon.svg', 'icons/icon-192.png', 'icons/icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// network-first for navigation/HTML/data so updates land on next reload;
// cache-first only for tiny static assets (icon, manifest).
function isHtmlOrData(url, request) {
  if (request.mode === 'navigate') return true;
  const p = url.pathname;
  return p === '/' || p.endsWith('/') ||
         p.endsWith('/index.html') || p.endsWith('index.html') ||
         p.endsWith('/data.json')  || p.endsWith('data.json');
}

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);

  if (isHtmlOrData(url, e.request)) {
    e.respondWith(
      fetch(e.request).then(r => {
        const clone = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return r;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
