# Consulta el Padron RUC sin Backend

18 millones de RUC, consultables en el navegador. Cero backend.     

---


## Cómo funciona

```
SUNAT (.zip)  ──►  parseo en una pasada  ──┬──►  shards core .tsv.gz    ──►  dist/shards/*
                                           └──►  sidecar domicilio .gz  ──►  dist/dom/*
                                                                              │
                                            GitHub Pages ◄── artifact ────────┘
```

Un solo índice: **shards de TSV gzipeado**, direccionados por `ruc % shardCount`.
Cada consulta baja **un** shard (~55 KB), lo descomprime en el navegador y busca el
RUC. El domicilio vive aparte, en un **sidecar comprimido** que solo se baja cuando
hay un resultado que mostrar.

Sin backend: la página es interactiva al instante y cada consulta es un `fetch` + `DecompressionStream`.

### ¿Por qué `ruc % N` y no un prefijo?

Casi todos los RUC peruanos empiezan en `10` o `20`. Shardear por los primeros
dígitos produce cubetas grotescamente desbalanceadas. El módulo del número
completo reparte parejo sin necesitar un hash.

### ¿Por qué el domicilio va en un sidecar aparte?

Solo ~14% de los RUC del padrón reducido traen dirección, y el domicilio es el
campo más pesado. Meterlo en el índice principal inflaría cada shard con datos que
el 86% no tiene. En un sidecar gzipeado separado (`dist/dom/`), el domicilio pesa
~41 MiB en total y se baja perezoso, solo al mostrar un resultado. El índice core
queda chico y las consultas rápidas.

---

## Uso local

```bash
make build   # descarga el padrón real de SUNAT, indexa y arma dist/ (pesado, cientos de MB)
make check   # ¿cambió la fuente desde el último deploy? no descarga nada
make serve   # http://localhost:8000
```

`make serve` usa `python -m http.server`: como los shards son un `fetch` simple (no
range requests), no hace falta un servidor especial.

---

## El workflow

```
check   →  resuelve la URL del zip, compara ETag/Last-Modified/tamaño contra el
           meta.json del sitio YA PUBLICADO. Si no cambió, termina en segundos.
build   →  descarga, parsea UNA vez alimentando ambos shard sets, arma dist/, guard.
deploy  →  actions/deploy-pages
```

El estado del último build vive en el sitio desplegado (`/meta.json`), no en el
repositorio. Por eso nunca hay que commitear nada. Cada build guarda además el
**SHA-256 del zip** en `meta.json` (huella de lo procesado) y verifica que el
tamaño descargado coincida con `Content-Length` antes de indexar.

El cron corre a medianoche de Lima. El padrón no cambia a diario, así que la mayoría
de las corridas mueren en `check` a los pocos segundos. Un push a `web/`, `build/`
o `config.json` fuerza rebuild.

### Antes del primer deploy

1. Settings → Pages → **Source: GitHub Actions**.
2. Correr el workflow a mano (`workflow_dispatch`) con `force: true`.
3. Revisar en el log el paso *Size budget guard*.

---

---

## Estructura

```
build/
  common.py    utilidades, códigos de estado, dígito verificador
  discover.py  encuentra la URL del zip y su firma
  prepare.py   descarga, descomprime, parseo guiado por cabecera
  shards.py    shards .tsv.gz en dos fases (core + sidecar domicilio)
  guard.py     presupuesto de tamaño
  run.py       orquestador (`check` | `build`)
web/
  index.html  app.js  ruc-client.js
```

## Licencia

El código es libre. Los datos son de SUNAT y públicos
