// Tombstone for the workbox service worker this app used to ship. Browsers that
// installed it keep it — and its precached assets — until the script at this URL
// changes, so it has to stay served and it has to uninstall itself. Deleting the
// file instead would leave those clients on the old worker indefinitely.
self.addEventListener("install", () => self.skipWaiting())

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) await caches.delete(key)
      await self.registration.unregister()
      const clients = await self.clients.matchAll({ type: "window" })
      for (const client of clients) client.navigate(client.url)
    })()
  )
})
