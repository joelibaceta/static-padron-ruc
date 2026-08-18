# padron-ruc-static

Consulta de RUC sin backend. Una GitHub Action baja el **padrón reducido** público
de SUNAT, lo indexa y publica el resultado en GitHub Pages. El navegador resuelve
cada consulta bajando unos pocos KB.

**Nada del dataset se commitea nunca.** El sitio se construye en CI y se deploya
directo como artifact de Pages. El repo se queda del tamaño del código.

---

## No usar esto como validación legal

Es una **copia periódica**. El estado del contribuyente y la condición de domicilio
pueden estar desfasados respecto al registro vivo de SUNAT. Sirve para autocompletar
razón social, verificar que un RUC existe, enriquecer formularios.

Para validar un comprobante antes de aceptarlo corresponde la
[API de Consulta Integrada de CPE](https://cpe.sunat.gob.pe/sites/default/files/inline-files/Manual-de-Consulta-Integrada-de-Comprobante-de-Pago-por-ServicioWEB_v2.pdf)
de SUNAT, con credenciales de Clave SOL. La UI lo dice en el pie.

---

## Cómo funciona

```
SUNAT (.zip)  ──►  parseo en una pasada  ──┬──►  SQLite indexado  ──►  dist/db/*
                                           └──►  shards .tsv.gz   ──►  dist/shards/*
                                                                        │
                                            GitHub Pages ◄── artifact ──┘
```

Dos índices sobre los mismos datos, y un cliente que elige:

| | SQLite (`sql.js-httpvfs`) | Shards (respaldo) |
|---|---|---|
| Transporte | range requests sobre el B-tree | 1 fetch de un TSV gzip |
| Medido @2M filas | **8 KB / 2 requests** por consulta | **6.9 KB / 1 request** |
| Arranque | 32 KB / 4 requests | ninguno |
| Campos | todos, incluido domicilio fiscal | solo core, sin domicilio |
| Necesita | WASM + Web Worker + Range | `fetch` y `DecompressionStream` |

El cliente intenta SQLite y **degrada solo** a shards ante cualquier fallo — WASM
bloqueado, vendor ausente, un proxy que se coma los rangos. También degrada en
caliente si el worker muere después del init.

A escala real la ventaja de SQLite crece: la profundidad del B-tree sube de forma
logarítmica (los ~8 KB por consulta se mantienen), mientras que cada shard crece
lineal — con 4096 shards y ~12M de RUC serían ~41 KB por consulta.

### Por qué `ruc % N` y no un prefijo

Casi todos los RUC peruanos empiezan en `10` o `20`. Shardear por los primeros
dígitos produce cubetas grotescamente desbalanceadas. El módulo del número
completo reparte parejo sin necesitar un hash.

### Por qué `ruc INTEGER PRIMARY KEY`

En SQLite eso convierte al RUC en el `rowid`: la consulta baja directo por el
B-tree de la tabla, sin índice secundario y sin una indirección extra. Menos
páginas leídas por consulta y bastante menos sitio publicado. El `VACUUM` final
reescribe el archivo en orden de rowid para que las páginas contiguas lo sean
también en HTTP.

---

## Presupuesto de tamaño

GitHub Pages **rechaza sitios publicados de más de 1 GB** y el deploy expira a los
10 minutos. `build/guard.py` corta el build antes con un desglose por carpeta.

Medido con datos sintéticos de 2M filas, **por cada millón de RUC**:

| | SQLite | shards | total |
|---|---|---|---|
| con domicilio | ~68 MiB | ~14 MiB | **~82 MiB** |
| sin domicilio | ~40 MiB | ~14 MiB | **~54 MiB** |

Si el padrón real anda por los ~12M de RUC, eso proyecta **~985 MiB con domicilio**
(por encima del presupuesto de 900 MiB y peligrosamente cerca del límite duro) y
**~654 MiB sin domicilio** (cómodo).

Palancas en `config.json`, de menos a más drástica:

1. `dataset.max_domicilio_chars` (default 90) — recorta la dirección, que es el
   campo gordo. Bajarlo a 60 ahorra bastante y sigue siendo legible.
2. `dataset.include_domicilio: false` — el corte grande.
3. Mover el dataset a **GitHub Releases** (2 GB por asset, no cuenta contra el
   repo) o **Cloudflare R2** (10 GB gratis, egress cero) y dejar en Pages solo la
   UI. En ese caso hay que apuntar `meta.sqlite.urlPrefix` al host externo y
   confirmar que sirve `Accept-Ranges` con CORS.

Las proyecciones salen de datos sintéticos: las razones sociales y direcciones
reales pueden ser más largas. **Mide con el archivo real antes de confiar.**

---

## Uso local

```bash
make fake          # padrón sintético de 50k filas (RUC con dígito verificador válido)
make build-fake    # construye dist/ desde ese archivo + corre el guard
make serve         # http://localhost:8000
```

Para el archivo real (pesado, cientos de MB):

```bash
make check         # ¿cambió la fuente desde el último deploy? no descarga nada
make vendor        # copia sql.js-httpvfs a web/vendor (necesita npm)
make build         # descarga, indexa y arma dist/
```

### No sirvas con `python -m http.server`

No implementa `Range`: devuelve el archivo entero con 200 e ignora la cabecera.
`sql.js-httpvfs` lee bytes equivocados y SQLite reporta **"database disk image is
malformed"** — parece un build corrupto y es el servidor de pruebas. Por eso existe
`tools/serve.py`, que sí implementa rangos. `make serve` ya lo usa.

---

## El workflow

```
check   →  resuelve la URL del zip, compara ETag/Last-Modified/tamaño contra el
           meta.json del sitio YA PUBLICADO. Si no cambió, termina en segundos.
build   →  descarga, parsea UNA vez alimentando ambos índices, arma dist/, guard.
deploy  →  actions/deploy-pages
```

El estado del último build vive en el sitio desplegado (`/meta.json`), no en el
repositorio. Por eso nunca hay que commitear nada.

El cron corre a las 02:15 de Lima. El padrón no cambia a diario, así que la mayoría
de las corridas mueren en `check` a los pocos segundos. Un push a `web/`, `build/`
o `config.json` fuerza rebuild.

**Minutos de CI:** en repositorio público, Actions es ilimitado. En privado, un
build completo puede irse a 10–15 minutos y con corridas diarias se acerca al
límite del plan gratuito — otra razón para que `check` aborte temprano.

### Antes del primer deploy

1. Settings → Pages → **Source: GitHub Actions**.
2. Correr el workflow a mano (`workflow_dispatch`) con `force: true`.
3. Revisar en el log el paso *Guard de presupuesto*.

---

## Lo frágil, dicho de frente

- **La URL del zip.** SUNAT no publica un manifiesto estable. `build/discover.py`
  rasca la página de descarga buscando enlaces `.zip` y cae a
  `source.fallback_zip_urls`. Las URLs de respaldo en `config.json` **no están
  verificadas** — confírmalas contra la página de SUNAT en la primera corrida. Si
  ninguna responde, el build muere ruidosamente en vez de deployar un sitio vacío.
- **El layout de columnas.** El parseo se guía por la fila de cabecera, no por
  posiciones fijas, y acepta varios alias por columna. Si falta una requerida, el
  build imprime las columnas que sí encontró para que ajustes `COLUMN_ALIASES`.
- **El encoding.** El archivo viene en CP1252. Parsearlo como UTF-8 rompe toda
  tilde y toda `ñ`. Está fijado en `config.json`.
- **`sql.js-httpvfs`.** Se publica como bundle UMD, no como módulo ES: se carga con
  un `<script>` clásico y se toma el global. Los parámetros de `query()` van en un
  **array** aunque la firma sea variádica — pasarlos sueltos devuelve cero filas
  sin error. Verificado contra `0.8.12`.
- **Nunca hagas `SELECT COUNT(*)`** desde el cliente: fuerza un scan completo y
  baja la base entera. La cuenta ya está en `meta.json`.
- **Ancho de banda.** Pages tiene un límite blando de 100 GB/mes. Con ~8 KB por
  consulta eso es muchísimo tráfico, pero los ToS prohíben usar Pages como CDN o
  como servicio de alto volumen. Si esto crece, R2 o un Worker.
- **No es un endpoint.** Es una UI y un módulo JS. No hay `GET /ruc/{n}` que
  responda JSON a un `curl`. Para eso, un Cloudflare Worker sobre R2 — pero ya
  no sería "sin backend".

---

## Estructura

```
build/
  common.py        utilidades, códigos de estado, dígito verificador
  discover.py      encuentra la URL del zip y su firma
  prepare.py       descarga, descomprime, parseo guiado por cabecera
  sqlite_index.py  construye el SQLite y lo parte para httpvfs
  shards.py        shards .tsv.gz en dos fases (256 cubetas → 4096 shards)
  guard.py         presupuesto de tamaño
  run.py           orquestador (`check` | `build`)
tools/
  make_fake_padron.py  padrón sintético para pruebas
  serve.py             servidor estático local CON Range
web/
  index.html  app.js  ruc-client.js
```

## Licencia

El código, como quieras. Los datos son de SUNAT y públicos; el sitio debe decir de
dónde salen y cuándo se tomó el snapshot — la UI ya lo hace.
