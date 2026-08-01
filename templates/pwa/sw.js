{% load static %}// MedPally service worker.
//
// It exists to make the app installable and to leave something behind when the
// network drops on a ward.  It caches two kinds of thing and nothing else:
//
//   * the offline notice, precached at install;
//   * static assets whose filename carries a content hash, which Django only
//     produces in production and which are therefore safe to keep forever.
//
// Everything else — every feed, every saved list, every page that knows who is
// reading it — goes straight to the network and is never written to a cache
// that outlives the tab.

var CACHE = "medpally-{{ cache_version }}";
var OFFLINE_URL = "{% url 'offline' %}";
var PRECACHE = [
  OFFLINE_URL,
  "{% static 'css/app.css' %}",
  "{% static 'js/app.js' %}",
  "{% static 'js/htmx.min.js' %}"
];

// Django's ManifestStaticFilesStorage names files `app.<12 hex>.css`.
var HASHED_ASSET = /\.[0-9a-f]{12}\.[^./]+$/;

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE)
      .then(function (cache) { return cache.addAll(PRECACHE); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (names) {
        return Promise.all(names.map(function (name) {
          return name === CACHE ? null : caches.delete(name);
        }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

function offlineResponse() {
  return caches.match(OFFLINE_URL).then(function (cached) {
    return cached || new Response(
      "<h1>You are offline</h1>",
      { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } }
    );
  });
}

function cacheFirst(request) {
  return caches.match(request).then(function (cached) {
    if (cached) return cached;
    return fetch(request).then(function (response) {
      if (response.ok) {
        var copy = response.clone();
        caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
      }
      return response;
    });
  });
}

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;

  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(offlineResponse));
    return;
  }

  if (HASHED_ASSET.test(url.pathname)) event.respondWith(cacheFirst(request));
});
