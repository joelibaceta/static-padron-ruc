import { PadronClient, isValidRuc } from './ruc-client.js';

const $ = (id) => document.getElementById(id);
const form = $('form');
const input = $('ruc');
const submit = $('submit');
const hint = $('hint');
const result = $('result');

const client = new PadronClient('.');

const ESTADO_TONE = { 0: 'ok', 2: 'bad', 1: 'bad', 3: 'warn', 4: 'warn', 6: 'warn' };
const COND_TONE = { 0: 'ok', 1: 'bad', 2: 'warn', 3: 'warn' };

function setHint(text, isError = false) {
  hint.textContent = text;
  hint.classList.toggle('error', isError);
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function formatRuc(ruc) {
  return `${ruc.slice(0, 2)} ${ruc.slice(2, 10)} ${ruc.slice(10)}`;
}

function render(record, ruc) {
  if (!record) {
    result.innerHTML = `<div class="card">
      <h2>Sin resultados</h2>
      <p class="ruc">${esc(formatRuc(ruc))}</p>
      <p style="margin:.9rem 0 0;font-size:.92rem">
        Este RUC no aparece en el snapshot. Puede ser muy reciente, o no existir.
      </p>
    </div>`;
    return;
  }

  const estado = client.estadoLabel(record.estado);
  const cond = client.condLabel(record.cond);
  const rows = [
    ['Estado', `<span class="pill ${ESTADO_TONE[record.estado] || 'warn'}">${esc(estado)}</span>`],
    ['Condicion', `<span class="pill ${COND_TONE[record.cond] || 'warn'}">${esc(cond)}</span>`],
  ];
  if (record.ubigeo && record.ubigeo !== '0') rows.push(['Ubigeo', esc(record.ubigeo)]);
  if (record.dom) {
    rows.push(['Domicilio fiscal', esc(record.dom)]);
  } else if (client.meta?.includeDomicilio && client.backend.name === 'shards') {
    // Los shards no cargan el domicilio a proposito; decirlo evita que parezca
    // un dato faltante del padron.
    rows.push(['Domicilio fiscal', '<em style="color:var(--muted)">no disponible en el indice de respaldo</em>']);
  }

  result.innerHTML = `<div class="card">
    <h2>${esc(record.nombre)}</h2>
    <p class="ruc">${esc(formatRuc(record.ruc))}</p>
    <dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
  </div>`;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const ruc = input.value.replace(/\D/g, '');

  // Validar el digito verificador en el cliente ahorra una peticion completa
  // en el caso mas comun de error: un digito mal tipeado.
  if (!isValidRuc(ruc)) {
    result.innerHTML = '';
    setHint(
      ruc.length === 11
        ? 'Ese numero tiene 11 digitos pero el digito verificador no cuadra.'
        : 'Un RUC tiene exactamente 11 digitos.',
      true,
    );
    return;
  }

  submit.disabled = true;
  setHint('Consultando…');
  const started = performance.now();
  try {
    const record = await client.lookup(ruc);
    const ms = Math.round(performance.now() - started);
    render(record, ruc);
    setHint(`Resuelto en ${ms} ms via ${client.backend.name}.`);
  } catch (err) {
    result.innerHTML = '';
    setHint(`No se pudo consultar: ${err.message}`, true);
  } finally {
    submit.disabled = false;
  }
});

input.addEventListener('input', () => {
  if (hint.classList.contains('error')) setHint('');
});

(async () => {
  try {
    const meta = await client.init();
    submit.disabled = false;
    setHint('');

    const snapshot = meta.snapshot || meta.generatedAt || 'desconocido';
    const parts = [
      `${meta.records.toLocaleString('es-PE')} contribuyentes`,
      `snapshot: ${snapshot}`,
      `indice: ${client.backend.name}`,
    ];
    if (client.degraded) parts.push(`sqlite no disponible (${client.degraded})`);
    $('meta-line').textContent = parts.join(' · ');
    input.focus();
  } catch (err) {
    setHint(`No se pudo cargar el indice: ${err.message}`, true);
  }
})();
