/**
 * Cliente de consulta del padron RUC estatico.
 *
 * Dos backends, misma interfaz:
 *
 *   sqlite  - sql.js-httpvfs. Baja solo las paginas del B-tree que la consulta
 *             toca (unos pocos KB) via range requests. Es el camino preferido.
 *   shards  - un unico fetch de un TSV comprimido de ~40 KB elegido por
 *             `ruc % shardCount`. Sin WASM, sin workers, sin range requests.
 *
 * El backend sqlite se intenta primero y degrada en silencio a shards ante
 * cualquier fallo: WASM bloqueado, worker no vendorizado, un proxy que se coma
 * los range requests. La consulta nunca queda sin responder por eso.
 */

const ESTADO_FALLBACK = {
  0: 'ACTIVO',
  1: 'BAJA DE OFICIO',
  2: 'BAJA DEFINITIVA',
  3: 'BAJA PROVISIONAL',
  4: 'SUSPENSION TEMPORAL',
  5: 'INHABILITADO-VENT.UNICA',
  6: 'PENDIENTE DE INICIO DE ACTIVIDAD',
  7: 'ANULACION DEL NUMERO INTERNO',
  255: 'DESCONOCIDO',
};

const COND_FALLBACK = {
  0: 'HABIDO',
  1: 'NO HABIDO',
  2: 'NO HALLADO',
  3: 'POR VERIFICAR',
  4: 'PENDIENTE',
  5: '-',
  255: 'DESCONOCIDO',
};

/** Digito verificador del RUC: modulo 11 con pesos 5 4 3 2 7 6 5 4 3 2. */
export function isValidRuc(ruc) {
  if (!/^\d{11}$/.test(ruc)) return false;
  const weights = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2];
  let sum = 0;
  for (let i = 0; i < 10; i += 1) sum += Number(ruc[i]) * weights[i];
  let check = 11 - (sum % 11);
  if (check === 10) check = 0;
  if (check === 11) check = 1;
  return check === Number(ruc[10]);
}

async function gunzipIfNeeded(buffer) {
  const bytes = new Uint8Array(buffer);
  // Si el servidor ya lo descomprimio (Content-Encoding: gzip), la magia gzip
  // 1f 8b ya no esta y hay que tratarlo como texto plano.
  const looksGzipped = bytes.length > 2 && bytes[0] === 0x1f && bytes[1] === 0x8b;
  if (!looksGzipped) return new TextDecoder('utf-8').decode(bytes);

  if (typeof DecompressionStream === 'undefined') {
    throw new Error('El navegador no soporta DecompressionStream');
  }
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).text();
}

class ShardBackend {
  constructor(baseUrl, meta) {
    this.baseUrl = baseUrl;
    this.shardCount = meta.shards.shardCount;
    this.cache = new Map();
    this.name = 'shards';
  }

  shardPath(ruc) {
    const id = Number(BigInt(ruc) % BigInt(this.shardCount));
    const hex = id.toString(16).padStart(3, '0');
    return `${this.baseUrl}/shards/${hex[0]}/${hex}.tsv.gz`;
  }

  async lookup(ruc) {
    const url = this.shardPath(ruc);
    let table = this.cache.get(url);

    if (!table) {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`shard ${url}: HTTP ${resp.status}`);
      const text = await gunzipIfNeeded(await resp.arrayBuffer());
      table = new Map();
      for (const line of text.split('\n')) {
        if (!line) continue;
        const [k, nombre, estado, cond, ubigeo] = line.split('\t');
        table.set(k, {
          ruc: k,
          nombre,
          estado: Number(estado),
          cond: Number(cond),
          ubigeo,
          dom: null,
        });
      }
      // Los shards son chicos; conservarlos evita refetch al tipear variantes.
      this.cache.set(url, table);
    }

    return table.get(String(ruc)) || null;
  }
}

/** Carga un bundle UMD como script clasico y devuelve el global que expone. */
function loadUmd(url, globalName) {
  if (window[globalName]) return Promise.resolve(window[globalName]);
  return new Promise((resolve, reject) => {
    const tag = document.createElement('script');
    tag.src = url;
    tag.async = true;
    tag.onload = () => (
      window[globalName]
        ? resolve(window[globalName])
        : reject(new Error(`${url} no expuso ${globalName}`))
    );
    tag.onerror = () => reject(new Error(`no se pudo cargar ${url}`));
    document.head.appendChild(tag);
  });
}

class SqliteBackend {
  constructor(baseUrl, meta) {
    this.baseUrl = baseUrl;
    this.meta = meta;
    this.worker = null;
    this.name = 'sqlite';
  }

  async init() {
    // Todo lo que cruza hacia el worker va absoluto. Dentro del worker,
    // `location` es la del propio worker (vendor/sql.js-httpvfs/), asi que una
    // ruta relativa a la pagina se resuelve al lugar equivocado y da 404.
    const root = new URL(`${this.baseUrl}/`, location.href);
    const abs = (path) => new URL(path, root).toString();
    const dir = 'vendor/sql.js-httpvfs';

    // El paquete se publica como bundle UMD, no como modulo ES: un `import()`
    // dinamico revienta. Se carga como script clasico y se toma el global.
    const createDbWorker = await loadUmd(abs(`${dir}/index.js`), 'createDbWorker');

    const inner = { ...this.meta.sqlite };
    inner.urlPrefix = abs(inner.urlPrefix);

    this.worker = await createDbWorker(
      [{ from: 'inline', config: inner }],
      abs(`${dir}/sqlite.worker.js`),
      abs(`${dir}/sql-wasm.wasm`),
    );
    // Consulta de humo: si el B-tree no es navegable por HTTP, que falle ahora
    // y no en la primera busqueda del usuario.
    await this.worker.db.query('SELECT ruc FROM contribuyente LIMIT 1');
  }

  async lookup(ruc) {
    // Los parametros van en un array. La firma del paquete es variadica
    // (`query(sql, ...params)`) pero por dentro pasa el rest tal cual a sql.js,
    // que espera un array: mandar el valor suelto devuelve cero filas en
    // silencio, sin error. Perdido un rato ahi, queda escrito.
    const rows = await this.worker.db.query(
      'SELECT ruc, nombre, estado, cond, ubigeo, dom FROM contribuyente WHERE ruc = ?',
      [Number(ruc)],
    );
    if (!rows.length) return null;
    const row = rows[0];
    return {
      ruc: String(row.ruc),
      nombre: row.nombre,
      estado: Number(row.estado),
      cond: Number(row.cond),
      ubigeo: String(row.ubigeo ?? ''),
      dom: row.dom || null,
    };
  }
}

export class PadronClient {
  constructor(baseUrl = '.') {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.meta = null;
    this.backend = null;
    this.degraded = null;
  }

  async init() {
    const resp = await fetch(`${this.baseUrl}/meta.json`, { cache: 'no-cache' });
    if (!resp.ok) throw new Error(`meta.json: HTTP ${resp.status}`);
    this.meta = await resp.json();

    try {
      const sqlite = new SqliteBackend(this.baseUrl, this.meta);
      await sqlite.init();
      this.backend = sqlite;
    } catch (err) {
      this.degraded = err.message || String(err);
      this.backend = new ShardBackend(this.baseUrl, this.meta);
    }
    return this.meta;
  }

  estadoLabel(code) {
    return this.meta?.codes?.estado?.[String(code)] ?? ESTADO_FALLBACK[code] ?? '?';
  }

  condLabel(code) {
    return this.meta?.codes?.cond?.[String(code)] ?? COND_FALLBACK[code] ?? '?';
  }

  async lookup(ruc) {
    const clean = String(ruc).replace(/\D/g, '');
    if (clean.length !== 11) throw new Error('El RUC debe tener 11 digitos');

    try {
      return await this.backend.lookup(clean);
    } catch (err) {
      if (this.backend.name === 'sqlite') {
        // Degradar en caliente: el worker puede morir despues del init.
        this.degraded = err.message || String(err);
        this.backend = new ShardBackend(this.baseUrl, this.meta);
        return this.backend.lookup(clean);
      }
      throw err;
    }
  }
}
