const CACHE_NAME = 'fuelscope-v1';
const STATIC_CACHE = 'fuelscope-static-v1';
const API_CACHE = 'fuelscope-api-v1';

// Files to cache for offline use
const STATIC_FILES = [
  '/',
  '/static/manifest.json',
  '/static/icons/fuelscope-192.svg',
  '/static/icons/fuelscope-512.svg',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
  'https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@400;500;600;700&display=swap',
  'https://cdn.jsdelivr.net/npm/chart.js',
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => {
        return cache.addAll(STATIC_FILES);
      })
      .then(() => self.skipWaiting())
  );
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => name !== STATIC_CACHE && name !== CACHE_NAME && name !== API_CACHE)
            .map((name) => caches.delete(name))
        );
      })
      .then(() => self.clients.claim())
  );
});

// Fetch event - serve from cache, fall back to network
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Cache API responses for offline viewing
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      caches.open(API_CACHE)
        .then((cache) => {
          return fetch(event.request)
            .then((response) => {
              // Only cache successful GET responses
              if (response.ok && event.request.method === 'GET') {
                cache.put(event.request, response.clone());
              }
              return response;
            })
            .catch(() => {
              // Network failed - try cache
              return cache.match(event.request);
            })
        })
    );
    return;
  }

  // For static files and navigation, try cache first, then network
  event.respondWith(
    caches.match(event.request)
      .then((cachedResponse) => {
        if (cachedResponse) {
          return cachedResponse;
        }
        return fetch(event.request)
          .then((response) => {
            // Cache new responses for static files
            if (response.ok && url.origin === self.location.origin) {
              caches.open(STATIC_CACHE)
                .then((cache) => cache.put(event.request, response.clone()));
            }
            return response;
          })
      })
      .catch(() => {
        // If everything fails, show offline page for navigation requests
        if (event.request.mode === 'navigate') {
          return caches.match('/');
        }
      })
  );
});

// Listen for messages from the main page
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
