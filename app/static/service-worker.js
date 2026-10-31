const CACHE_NAME = 'bethel-shell-v1';
const CATALOG_CACHE_NAME = 'bethel-catalog-v1';
const STATIC_CACHE_NAME = 'bethel-static-v1';

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const shellCache = await caches.open(CACHE_NAME);
    await shellCache.addAll(['/static/manifest.webmanifest', '/static/icon.svg']);
    try {
      const response = await fetch('/');
      if (response.ok) await shellCache.put('/', response.clone());
    } catch (error) { }
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter(name => name.startsWith('bethel-') && ![CACHE_NAME, CATALOG_CACHE_NAME, STATIC_CACHE_NAME].includes(name)).map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  if (url.origin !== self.location.origin) {
    if (request.destination === 'script' || request.destination === 'style' || request.destination === 'font') {
      event.respondWith((async () => {
        const cache = await caches.open(STATIC_CACHE_NAME);
        const cached = await cache.match(request);
        if (cached) return cached;
        const response = await fetch(request);
        if (response.ok || response.type === 'opaque') await cache.put(request, response.clone());
        return response;
      })());
    }
    return;
  }

  if (url.pathname === '/') {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      try {
        const response = await fetch(request);
        if (response.ok) await cache.put('/', response.clone());
        return response;
      } catch (error) {
        return await cache.match('/') || new Response('The boutique app is not available offline yet. Connect once to load it for offline use.', {
          status: 503,
          headers: { 'Content-Type': 'text/plain; charset=utf-8' },
        });
      }
    })());
    return;
  }

  if (url.pathname === '/api/products' && url.search === '') {
    event.respondWith((async () => {
      const cache = await caches.open(CATALOG_CACHE_NAME);
      try {
        const response = await fetch(request);
        if (response.ok) await cache.put('/api/products', response.clone());
        return response;
      } catch (error) {
        return await cache.match('/api/products') || Response.json({ error: 'Catalog is not available offline yet.' }, { status: 503 });
      }
    })());
    return;
  }

  if (url.pathname.startsWith('/uploads/') || url.pathname.startsWith('/static/')) {
    event.respondWith((async () => {
      const cache = await caches.open(STATIC_CACHE_NAME);
      const cached = await cache.match(request);
      try {
        const response = await fetch(request);
        if (response.ok) await cache.put(request, response.clone());
        return response;
      } catch (error) {
        return cached || Response.error();
      }
    })());
  }
});