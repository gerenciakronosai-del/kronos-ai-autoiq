/* Service worker de Kronos Studio.
 *
 * Dos cachés separadas a proposito:
 *
 * - `kronos-app`: los ficheros propios. Se sirven de red primero, para que una
 *   version nueva llegue sin que el usuario tenga que borrar nada.
 * - `kronos-pyodide`: los ~10 MB del runtime de Python, que vienen de un CDN y
 *   no cambian nunca para una version dada. Se sirven de cache primero: bajarlos
 *   dos veces seria una falta de respeto con el plan de datos de cualquiera.
 */

const VERSION = "v1";
const CACHE_APP = `kronos-app-${VERSION}`;
const CACHE_PY = "kronos-pyodide";

const PROPIOS = [
  "./",
  "./index.html",
  "./estilo.css",
  "./app.js",
  "./manifest.json",
  "./icono.svg",
  "./kronos.zip",
  "./demo.csv",
];

self.addEventListener("install", (ev) => {
  ev.waitUntil(
    caches.open(CACHE_APP)
      .then((c) => c.addAll(PROPIOS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (ev) => {
  ev.waitUntil(
    caches.keys()
      .then((claves) => Promise.all(
        claves
          .filter((k) => k.startsWith("kronos-app-") && k !== CACHE_APP)
          .map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (ev) => {
  const req = ev.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Runtime de Python: cache primero, y lo que se descargue se guarda.
  if (url.hostname === "cdn.jsdelivr.net") {
    ev.respondWith(
      caches.open(CACHE_PY).then(async (cache) => {
        const guardado = await cache.match(req);
        if (guardado) return guardado;
        const respuesta = await fetch(req);
        if (respuesta.ok) cache.put(req, respuesta.clone());
        return respuesta;
      })
    );
    return;
  }

  // Ficheros propios: red primero, cache como red de seguridad si no hay linea.
  if (url.origin === self.location.origin) {
    ev.respondWith(
      fetch(req)
        .then((respuesta) => {
          if (respuesta.ok) {
            const copia = respuesta.clone();
            caches.open(CACHE_APP).then((c) => c.put(req, copia));
          }
          return respuesta;
        })
        .catch(() => caches.match(req).then((r) => r || caches.match("./index.html")))
    );
  }
});
