// Minimaler Service Worker: App-Hülle offline verfügbar, API immer live
const CACHE = "elektrocar-v4";
const SHELL = ["/", "/style.css", "/app.js", "/icon.svg", "/icon-192.png", "/badge-96.png", "/manifest.webmanifest"];
// Neue Version sofort aktivieren, sonst bleibt ein alter Service Worker ohne
// Push-Unterstützung aktiv, bis die App komplett geschlossen wurde
self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
});
self.addEventListener("activate", (e) =>
  e.waitUntil(caches.keys()
    .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request)));
});

// Push-Benachrichtigungen (80 % / Akku voll)
self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data.json(); } catch { data = { title: "Elektroauto", body: e.data?.text() }; }
  const title = data.title || "Elektroauto";
  const opts = { body: data.body || "", tag: data.tag, icon: "icon-192.png", badge: "badge-96.png" };
  e.waitUntil(Promise.all([
    // Fällt die Anzeige mit Symbolen aus, ohne Symbole erneut versuchen
    self.registration.showNotification(title, opts)
      .catch(() => self.registration.showNotification(title, { body: opts.body, tag: opts.tag })),
    // offene App informieren (Diagnose: Nachricht ist am Gerät angekommen)
    clients.matchAll({ type: "window", includeUncontrolled: true })
      .then((list) => list.forEach((c) => c.postMessage({ type: "push", title }))),
  ]));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    const open = list.find((c) => new URL(c.url).origin === location.origin);
    return open ? open.focus() : clients.openWindow("/");
  }));
});
