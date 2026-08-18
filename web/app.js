import { PadronClient, isValidRuc } from './ruc-client.js';

const $ = (id) => document.getElementById(id);
const form = $('form');
const input = $('ruc');
const submit = $('submit');
const hint = $('hint');
const result = $('result');

const client = new PadronClient('.');

// Pill tone per estado / cond code; anything unlisted falls back to 'warn'.
const ESTADO_TONE = { 0: 'ok', 1: 'bad', 2: 'bad', 5: 'bad', 7: 'bad', 8: 'bad', 9: 'bad', 10: 'bad', 13: 'bad' };
const COND_TONE = { 0: 'ok', 1: 'bad' };

const NUM = (n) => n.toLocaleString('es-PE');

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// Plain-text status line, optionally styled as an error.
function setHint(text, isError = false) {
  hint.textContent = text;
  hint.classList.toggle('error', isError);
}

// Status line with trusted markup (bold numbers, icon); never an error.
function setHintHtml(html) {
  hint.innerHTML = html;
  hint.classList.remove('error');
}

// Inline line icon from the SVG sprite. Decorative: aria-hidden.
function ico(id, cls = '') {
  const klass = cls ? `ico ${cls}` : 'ico';
  return `<svg class="${klass}" width="20" height="20" aria-hidden="true" focusable="false"><use href="#i-${id}"/></svg>`;
}

function formatRuc(ruc) {
  return `${ruc.slice(0, 2)} ${ruc.slice(2, 10)} ${ruc.slice(10)}`;
}

function row(iconId, label, value) {
  return `<div class="row">${ico(iconId)}<span class="label">${label}</span><span class="val">${value}</span></div>`;
}

// The "wow" line: the effect (fast, over millions, serverless), not the mechanism.
function setWow(ms) {
  setHintHtml(`${ico('bolt', 'bolt')} <b>${ms} ms</b> · buscado en <b>${NUM(client.meta.records)}</b> registros · sin backend`);
}

function render(record, ruc) {
  if (!record) {
    result.innerHTML = `<div class="panel">
      <h2 class="name">Sin resultados</h2>
      <p class="ruc">${esc(formatRuc(ruc))}</p>
      <p class="empty">Este RUC no aparece en el snapshot. Puede ser muy reciente, o no existir.</p>
    </div>`;
    return;
  }

  const rows = [
    row('badge', 'Estado', `<span class="pill ${ESTADO_TONE[record.estado] || 'warn'}">${esc(client.estadoLabel(record.estado))}</span>`),
    row('home', 'Condición', `<span class="pill ${COND_TONE[record.cond] || 'warn'}">${esc(client.condLabel(record.cond))}</span>`),
  ];
  if (record.ubigeo && record.ubigeo !== '0') rows.push(row('pin', 'Ubigeo', esc(record.ubigeo)));
  if (record.dom) rows.push(row('building', 'Domicilio fiscal', esc(record.dom)));

  result.innerHTML = `<div class="panel">
    <h2 class="name">${esc(record.nombre)}</h2>
    <p class="ruc">${esc(formatRuc(record.ruc))}</p>
    <div class="rows">${rows.join('')}</div>
  </div>`;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const ruc = input.value.replace(/\D/g, '');

  // Client-side check-digit validation avoids a full lookup on the commonest error.
  if (!isValidRuc(ruc)) {
    result.innerHTML = '';
    setHint(
      ruc.length === 11
        ? 'Ese número tiene 11 dígitos pero el dígito verificador no cuadra.'
        : 'Un RUC tiene exactamente 11 dígitos.',
      true,
    );
    return;
  }

  submit.disabled = true;
  setHint('Consultando…');
  const started = performance.now();
  try {
    const record = await client.lookup(ruc);
    render(record, ruc);
    setWow(Math.round(performance.now() - started));
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
    setHintHtml(`<b>${NUM(meta.records)}</b> registros listos · escribe un RUC`);

    const snapshot = meta.snapshot || meta.generatedAt || 'desconocido';
    $('meta-line').textContent = `snapshot: ${snapshot}`;
    input.focus();
  } catch (err) {
    setHint(`No se pudo cargar el índice: ${err.message}`, true);
  }
})();
