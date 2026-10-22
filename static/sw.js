/**
 * Chimaera PWA Service Worker
 * Accelerates mobile load times via Cache-First static asset & Scryfall card thumbnail caching,
 * while keeping HTML navigations and live pricing API requests strictly Network-First.
 */
const STATIC_CACHE = "chimaera-static-v2";
const ART_CACHE = "chimaera-art-v2";
const MAX_ART_ENTRIES = 250;

self.addEventListener("install", (event) => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(
                keys.map((key) => {
                    if (key !== STATIC_CACHE && key !== ART_CACHE) {
                        return caches.delete(key);
                    }
                })
            )
        ).then(() => self.clients.claim())
    );
});

async function trimCache(cacheName, maxItems) {
    try {
        const cache = await caches.open(cacheName);
        const keys = await cache.keys();
        if (keys.length > maxItems) {
            const deleteCount = keys.length - maxItems;
            for (let i = 0; i < deleteCount; i++) {
                await cache.delete(keys[i]);
            }
        }
    } catch (_) {
        // Ignore storage quota / trim errors
    }
}

self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.method !== "GET") return;

    const url = new URL(req.url);

    // 1. Cache-First for versioned static assets (/static/...) and Google Font files
    if (
        (url.origin === self.location.origin && url.pathname.startsWith("/static/")) ||
        url.hostname === "fonts.gstatic.com"
    ) {
        event.respondWith(
            caches.open(STATIC_CACHE).then(async (cache) => {
                const cached = await cache.match(req);
                if (cached) return cached;
                try {
                    const netResp = await fetch(req);
                    if (netResp && netResp.status === 200) {
                        cache.put(req, netResp.clone());
                    }
                    return netResp;
                } catch (err) {
                    return cached || Response.error();
                }
            })
        );
        return;
    }

    // 2. Cache-First with LRU cap for Scryfall small thumbnails & art crops
    if (
        url.hostname === "cards.scryfall.io" &&
        (url.pathname.startsWith("/small/") || url.pathname.startsWith("/art_crop/"))
    ) {
        event.respondWith(
            caches.open(ART_CACHE).then(async (cache) => {
                const cached = await cache.match(req);
                if (cached) return cached;
                try {
                    const netResp = await fetch(req);
                    if (netResp && (netResp.status === 200 || netResp.type === "opaque")) {
                        cache.put(req, netResp.clone());
                        trimCache(ART_CACHE, MAX_ART_ENTRIES);
                    }
                    return netResp;
                } catch (err) {
                    return cached || Response.error();
                }
            })
        );
        return;
    }
});
