/**
 * Query client for the static RUC padron.
 *
 * One index: gzipped TSV shards addressed by `ruc % shardCount`. A lookup fetches
 * one core shard (~55 KB) and, if the record is shown, one domicilio sidecar
 * shard. No WASM, no worker, no range requests — the page is interactive at once.
 */

// Labels are normally read from meta.json; these cover a meta without codes.
const ESTADO_FALLBACK = {
  0: 'ACTIVO',
  1: 'BAJA DE OFICIO',
  2: 'BAJA DEFINITIVA',
  3: 'BAJA PROVISIONAL',
  4: 'SUSPENSION TEMPORAL',
  5: 'INHABILITADO-VENT.UNICA',
  6: 'PENDIENTE DE INICIO DE ACTIVIDAD',
  7: 'ANULACION DEL NUMERO INTERNO',
  8: 'BAJA PROVISIONAL POR OFICIO',
  9: 'BAJA POR NO ACTIVIDAD',
  10: 'BAJA MULTIPLE INSCRIPCION',
  11: 'NUMERO INTERNO IDENTIFICATORIO',
  12: 'OTROS OBLIGADOS',
  13: 'ANULACION',
  255: 'DESCONOCIDO',
};

const COND_FALLBACK = {
  0: 'HABIDO',
  1: 'NO HABIDO',
  2: 'NO HALLADO',
  3: 'POR VERIFICAR',
  4: 'PENDIENTE',
  5: '-',
  6: 'NO APLICABLE',
  255: 'DESCONOCIDO',
};

// RUC check digit: modulo 11 with weights 5 4 3 2 7 6 5 4 3 2.
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

async function gunzip(buffer) {
  const bytes = new Uint8Array(buffer);
  // If the server already decoded it (Content-Encoding: gzip), the 1f 8b magic
  // is gone and it is plain text.
  const looksGzipped = bytes.length > 2 && bytes[0] === 0x1f && bytes[1] === 0x8b;
  if (!looksGzipped) return new TextDecoder('utf-8').decode(bytes);

  if (typeof DecompressionStream === 'undefined') {
    throw new Error('El navegador no soporta DecompressionStream');
  }
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).text();
}

/** One sharded gzipped-TSV set. `parse` fills a Map keyed by RUC string. */
class ShardSet {
  constructor(baseUrl, dir, shardCount, parse) {
    this.baseUrl = baseUrl;
    this.dir = dir;
    this.shardCount = shardCount;
    this.parse = parse;
    this.cache = new Map();
  }

  url(ruc) {
    const id = Number(BigInt(ruc) % BigInt(this.shardCount));
    const hex = id.toString(16).padStart(3, '0');
    return `${this.baseUrl}/${this.dir}/${hex[0]}/${hex}.tsv.gz`;
  }

  async load(ruc) {
    const url = this.url(ruc);
    let table = this.cache.get(url);
    if (!table) {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`shard ${url}: HTTP ${resp.status}`);
      const text = await gunzip(await resp.arrayBuffer());
      table = new Map();
      for (const line of text.split('\n')) {
        if (line) this.parse(line, table);
      }
      // Shards are small; keeping them avoids refetching while the user retypes.
      this.cache.set(url, table);
    }
    return table;
  }
}

export class PadronClient {
  constructor(baseUrl = '.') {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.meta = null;
    this.core = null;
    this.dom = null;
  }

  async init() {
    const resp = await fetch(`${this.baseUrl}/meta.json`, { cache: 'no-cache' });
    if (!resp.ok) throw new Error(`meta.json: HTTP ${resp.status}`);
    this.meta = await resp.json();

    this.core = new ShardSet(this.baseUrl, 'shards', this.meta.shards.shardCount, (line, table) => {
      const [ruc, nombre, estado, cond, ubigeo] = line.split('\t');
      table.set(ruc, { ruc, nombre, estado: Number(estado), cond: Number(cond), ubigeo, dom: null });
    });
    if (this.meta.domicilio) {
      this.dom = new ShardSet(this.baseUrl, 'dom', this.meta.domicilio.shardCount, (line, table) => {
        const tab = line.indexOf('\t');
        table.set(line.slice(0, tab), line.slice(tab + 1));
      });
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
    const key = String(ruc).replace(/\D/g, '');
    if (key.length !== 11) throw new Error('El RUC debe tener 11 dígitos');

    // Core and domicilio shards fetch in parallel; both are small and cached.
    const [coreTable, domTable] = await Promise.all([
      this.core.load(key),
      this.dom ? this.dom.load(key) : Promise.resolve(null),
    ]);

    const rec = coreTable.get(key);
    if (!rec) return null;
    if (domTable) rec.dom = domTable.get(key) || null;
    return rec;
  }
}
