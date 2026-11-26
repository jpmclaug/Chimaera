/**
 * Chimaera PWA Service Worker
 * Accelerates mobile load times via Cache-First static asset & Scryfall card thumbnail caching,
 * while keeping HTML navigations and live pricing API requests strictly Network-First.
 */
const STATIC_CACHE = "chimaera-static-v4";
const ART_CACHE = "chimaera-art-v3";
const MAX_ART_ENTRIES = 1000;
let isTrimming = false;

self.addEventListener("install", (event) => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil((async () => {
        const keys = await caches.keys();
        await Promise.all(
            keys.map((key) => {
                if (key !== STATIC_CACHE && key !== ART_CACHE) {
                    return caches.delete(key);
                }
            })
        );
        if (self.registration && self.registration.navigationPreload) {
            try {
                await self.registration.navigationPreload.enable();
            } catch (_) {
                // Ignore if navigation preload cannot be enabled
            }
        }
        await self.clients.claim();
    })());
});

async function trimCache(cacheName, maxItems) {
    if (isTrimming) return;
    isTrimming = true;
    try {
        const cache = await caches.open(cacheName);
        const keys = await cache.keys();
        if (keys.length > maxItems) {
            const deleteCount = keys.length - maxItems;
            await Promise.all(keys.slice(0, deleteCount).map((k) => cache.delete(k)));
        }
    } catch (_) {
        // Ignore storage quota / trim errors
    } finally {
        isTrimming = false;
    }
}

async function evictStaleStaticSiblings(cache, reqUrl) {
    try {
        const keys = await cache.keys();
        const stale = keys.filter((k) => {
            try {
                const u = new URL(k.url);
                return u.origin === reqUrl.origin && u.pathname === reqUrl.pathname && k.url !== reqUrl.href;
            } catch (_) {
                return false;
            }
        });
        if (stale.length) {
            await Promise.all(stale.map((k) => cache.delete(k)));
        }
    } catch (_) {
        // Ignore eviction errors
    }
}

self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.method !== "GET") return;

    // 0. Navigation Preload for top-level HTML requests (Network-First)
    if (req.mode === "navigate") {
        event.respondWith((async () => {
            try {
                const preload = await event.preloadResponse;
                if (preload) return preload;
            } catch (_) {
                // Fall through to normal fetch
            }
            return fetch(req);
        })());
        return;
    }

    const url = new URL(req.url);

    // 1. Stale-While-Revalidate for Google Fonts stylesheet
    if (url.hostname === "fonts.googleapis.com") {
        event.respondWith(
            caches.open(STATIC_CACHE).then(async (cache) => {
                const cached = await cache.match(req);
                const networkPromise = fetch(req).then((netResp) => {
                    if (netResp && (netResp.status === 200 || netResp.type === "opaque")) {
                        cache.put(req, netResp.clone());
                    }
                    return netResp;
                }).catch(() => null);
                if (cached) {
                    event.waitUntil(networkPromise);
                    return cached;
                }
                const fresh = await networkPromise;
                return fresh || Response.error();
            })
        );
        return;
    }

    // 2. Cache-First for versioned static assets (/static/...) and Google Font files
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
                        const tasks = [cache.put(req, netResp.clone())];
                        if (url.origin === self.location.origin && url.pathname.startsWith("/static/")) {
                            tasks.push(evictStaleStaticSiblings(cache, url));
                        }
                        event.waitUntil(Promise.all(tasks));
                    }
                    return netResp;
                } catch (err) {
                    return cached || Response.error();
                }
            })
        );
        return;
    }

    // 3. Cache-First with serialized LRU cap for Scryfall thumbnails, art crops, and normal card art
    if (
        url.hostname === "cards.scryfall.io" &&
        (url.pathname.startsWith("/small/") || url.pathname.startsWith("/art_crop/") || url.pathname.startsWith("/normal/"))
    ) {
        event.respondWith(
            caches.open(ART_CACHE).then(async (cache) => {
                const cached = await cache.match(req);
                if (cached) return cached;
                try {
                    const netResp = await fetch(req);
                    if (netResp && (netResp.status === 200 || netResp.type === "opaque")) {
                        event.waitUntil((async () => {
                            await cache.put(req, netResp.clone());
                            await trimCache(ART_CACHE, MAX_ART_ENTRIES);
                        })());
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
