/* Service worker de l'application « Situation hydrologique »
   - garde en cache la page et les icônes (ouverture rapide, affichage hors connexion) ;
   - ne touche jamais aux données (SHOM, VigiEau, Hub'Eau, Météo-France) : elles sont toujours lues en direct. */
const CACHE = "situation-hydro-v61";
const FICHIERS = ["./", "index.html", "manifest.webmanifest",
  "meteo-pluie-temperature.html", "meteo-pluviometrie.html", "meteo-temperature.html",
  "meteo-nappe-30-jours.html", "meteo-correlation-nappe-pluie.html", "meteo-graphique-nappe-pluie.html", "meteo-reponse-nappe-10mm.html", "d3.min.js",
  "icons/icon-192.png", "icons/icon-512.png", "icons/icon-maskable-512.png", "icons/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(FICHIERS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((cles) => Promise.all(cles.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;   // données externes : en direct
  // Réseau d'abord (pour avoir la dernière version de la page), cache en secours
  e.respondWith(
    // « no-cache » : le navigateur revérifie toujours auprès du site, il ne ressert jamais une ancienne copie de son cache
    fetch(e.request, { cache: "no-cache" })
      .then((rep) => { const copie = rep.clone(); if (rep.ok) caches.open(CACHE).then((c) => c.put(e.request, copie)); return rep; })
      .catch(() => caches.match(e.request, { ignoreSearch: true }).then((r) => r || caches.match("index.html")))
  );
});
