// Service worker do PWA "Fora do ar" — CamWatch
// Servido via rota Flask (não como estático) para herdar o prefixo de
// montagem (/camwatch em produção) através de url_for.

const CACHE_VERSION = 'v1';
const CACHE_NAME = 'camwatch-fora-do-ar-' + CACHE_VERSION;

const PAGINA_PRINCIPAL = '{{ url_for("monitor.fora_do_ar_mobile") }}';

const APP_SHELL = [
  PAGINA_PRINCIPAL,
  '{{ url_for("static", filename="css/mobile.css") }}',
  '{{ url_for("static", filename="img/logo_camwatch.png") }}',
  '{{ url_for("static", filename="img/icons/icon-192.png") }}',
  '{{ url_for("static", filename="img/icons/icon-512.png") }}',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((nomes) => Promise.all(
        nomes.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  const ehDadosVivos = url.pathname.endsWith('/parcial');
  const ehNavegacao = req.mode === 'navigate';

  if (ehDadosVivos || ehNavegacao) {
    // Página e dados ao vivo: rede primeiro (status precisa ser atual);
    // se offline, cai para a última versão em cache.
    event.respondWith(
      fetch(req)
        .then((resp) => {
          const copia = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(req, copia));
          return resp;
        })
        .catch(() => caches.match(req).then((resp) => resp || caches.match(PAGINA_PRINCIPAL)))
    );
    return;
  }

  // Assets estáticos (CSS, ícones): cache primeiro, rede como respaldo.
  event.respondWith(
    caches.match(req).then((resp) => resp || fetch(req))
  );
});
