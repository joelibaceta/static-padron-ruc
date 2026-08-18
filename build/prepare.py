"""Descarga, descomprime y normaliza el padron reducido a registros limpios.

El parseo es guiado por la cabecera, no por posiciones fijas: SUNAT ha movido
columnas antes. Si falta una columna requerida, el build muere imprimiendo las
columnas que si encontro, que es la unica forma sana de depurar esto sin bajar
el archivo a mano.
"""

from __future__ import annotations

import io
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from common import (
    COND_CODES,
    ESTADO_CODES,
    WORK,
    clean_text,
    die,
    human_bytes,
    log,
    norm_header,
)
from discover import USER_AGENT

# Alias aceptados por columna logica. Se comparan ya normalizados
# (sin tildes, mayusculas, separadores colapsados a un espacio).
COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "ruc": ("RUC", "NUMERO RUC", "NRO RUC"),
    "nombre": (
        "NOMBRE O RAZON SOCIAL",
        "RAZON SOCIAL",
        "NOMBRE RAZON SOCIAL",
        "APELLIDOS Y NOMBRES O RAZON SOCIAL",
        "NOMBRE",
    ),
    "estado": ("ESTADO DEL CONTRIBUYENTE", "ESTADO CONTRIBUYENTE", "ESTADO"),
    "condicion": (
        "CONDICION DE DOMICILIO",
        "CONDICION DOMICILIO",
        "CONDICION",
    ),
    "ubigeo": ("UBIGEO", "CODIGO UBIGEO"),
}

# Partes del domicilio, en el orden en que se concatenan.
ADDRESS_PARTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("tipo_via", ("TIPO DE VIA", "TIPO VIA")),
    ("nombre_via", ("NOMBRE DE VIA", "NOMBRE VIA")),
    ("numero", ("NUMERO", "NRO")),
    ("interior", ("INTERIOR", "INT",)),
    ("lote", ("LOTE",)),
    ("departamento_dir", ("DEPARTAMENTO",)),
    ("manzana", ("MANZANA", "MZA", "MZ")),
    ("kilometro", ("KILOMETRO", "KM")),
    ("tipo_zona", ("TIPO ZONA", "TIPO DE ZONA")),
    ("codigo_zona", ("CODIGO ZONA", "COD ZONA", "NOMBRE ZONA")),
)

PREFIXED = {
    "interior": "INT.",
    "lote": "LOTE",
    "departamento_dir": "DPTO.",
    "manzana": "MZ.",
    "kilometro": "KM.",
}


def download(url: str, dest: Path, timeout: int = 900) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    log("descargando {}".format(url))
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out, length=1 << 20)
    log("descargado {} ({})".format(dest.name, human_bytes(dest.stat().st_size)))
    return dest


def extract_main_member(zip_path: Path, dest_dir: Path) -> Path:
    """Saca el .txt mas grande del zip; SUNAT a veces mete un readme al lado."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if not members:
            die("el zip {} esta vacio".format(zip_path.name))
        txt = [m for m in members if m.filename.lower().endswith((".txt", ".csv"))]
        target = max(txt or members, key=lambda m: m.file_size)
        log(
            "extrayendo {} ({}) de {}".format(
                target.filename, human_bytes(target.file_size), zip_path.name
            )
        )
        out_path = dest_dir / Path(target.filename).name
        with zf.open(target) as src, open(out_path, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1 << 20)
    return out_path


def _build_index(header_fields: List[str]) -> Dict[str, int]:
    normalized = [norm_header(h) for h in header_fields]
    index: Dict[str, int] = {}

    def find(aliases: Tuple[str, ...]) -> Optional[int]:
        for alias in aliases:
            key = norm_header(alias)
            if key in normalized:
                return normalized.index(key)
        return None

    for logical, aliases in COLUMN_ALIASES.items():
        pos = find(aliases)
        if pos is not None:
            index[logical] = pos

    for logical, aliases in ADDRESS_PARTS:
        pos = find(aliases)
        if pos is not None:
            index[logical] = pos

    missing = [k for k in ("ruc", "nombre", "estado", "condicion") if k not in index]
    if missing:
        die(
            "faltan columnas requeridas {} en el padron.\n"
            "Columnas encontradas: {}\n"
            "Ajusta COLUMN_ALIASES en build/prepare.py.".format(missing, normalized)
        )
    return index


def _compose_address(fields: List[str], index: Dict[str, int], max_chars: int) -> str:
    chunks: List[str] = []
    for logical, _aliases in ADDRESS_PARTS:
        pos = index.get(logical)
        if pos is None or pos >= len(fields):
            continue
        value = clean_text(fields[pos])
        if not value:
            continue
        prefix = PREFIXED.get(logical)
        chunks.append("{} {}".format(prefix, value) if prefix else value)
    return " ".join(chunks)[:max_chars]


def iter_records(
    txt_path: Path,
    encoding: str,
    delimiter: str,
    include_domicilio: bool,
    max_domicilio_chars: int = 90,
) -> Iterator[Tuple[int, str, int, int, int, str]]:
    """Genera (ruc, nombre, estado, condicion, ubigeo, domicilio).

    Es un generador a proposito: el padron no cabe comodo en memoria y el runner
    de GitHub tiene 7 GB de RAM que preferimos no tocar.
    """
    stats = {"lines": 0, "bad": 0, "dupe": 0}
    unknown_estado: Dict[str, int] = {}
    unknown_cond: Dict[str, int] = {}
    seen = set()

    with io.open(txt_path, "r", encoding=encoding, errors="replace", newline="") as fh:
        first = fh.readline()
        header_fields = first.rstrip("\r\n").split(delimiter)
        first_cell = header_fields[0].strip() if header_fields else ""

        if first_cell.isdigit() and len(first_cell) == 11:
            die(
                "el archivo no trae fila de cabecera (primera celda: {}). "
                "El parseo posicional no es seguro; define la cabecera a mano "
                "en build/prepare.py antes de continuar.".format(first_cell)
            )

        index = _build_index(header_fields)
        log("mapeo de columnas: {}".format(index))

        pos_ruc = index["ruc"]
        pos_nombre = index["nombre"]
        pos_estado = index["estado"]
        pos_cond = index["condicion"]
        pos_ubigeo = index.get("ubigeo")
        max_pos = max(index.values())

        for line in fh:
            stats["lines"] += 1
            fields = line.rstrip("\r\n").split(delimiter)
            if len(fields) <= max_pos:
                stats["bad"] += 1
                continue

            ruc_raw = fields[pos_ruc].strip()
            if len(ruc_raw) != 11 or not ruc_raw.isdigit():
                stats["bad"] += 1
                continue
            ruc = int(ruc_raw)
            if ruc in seen:
                stats["dupe"] += 1
                continue
            seen.add(ruc)

            estado_raw = clean_text(fields[pos_estado]).upper()
            cond_raw = clean_text(fields[pos_cond]).upper()
            estado = ESTADO_CODES.get(estado_raw)
            cond = COND_CODES.get(cond_raw)
            if estado is None:
                unknown_estado[estado_raw] = unknown_estado.get(estado_raw, 0) + 1
                estado = 255
            if cond is None:
                unknown_cond[cond_raw] = unknown_cond.get(cond_raw, 0) + 1
                cond = 255

            ubigeo = 0
            if pos_ubigeo is not None:
                ub_raw = fields[pos_ubigeo].strip()
                if ub_raw.isdigit():
                    ubigeo = int(ub_raw)

            domicilio = (
                _compose_address(fields, index, max_domicilio_chars)
                if include_domicilio
                else ""
            )

            yield (
                ruc,
                clean_text(fields[pos_nombre])[:120],
                estado,
                cond,
                ubigeo,
                domicilio,
            )

            if stats["lines"] % 1_000_000 == 0:
                log("  {:,} lineas leidas".format(stats["lines"]))

    log(
        "parseo terminado: {:,} lineas, {:,} descartadas, {:,} duplicadas".format(
            stats["lines"], stats["bad"], stats["dupe"]
        )
    )
    if unknown_estado:
        log("AVISO estados no mapeados: {}".format(dict(list(unknown_estado.items())[:10])))
    if unknown_cond:
        log("AVISO condiciones no mapeadas: {}".format(dict(list(unknown_cond.items())[:10])))


def prepare_source(cfg: dict, sig: dict, local_txt: Optional[str] = None) -> Path:
    """Deja en disco el .txt listo para parsear (descargando si hace falta)."""
    if local_txt:
        path = Path(local_txt)
        if not path.exists():
            die("no existe el archivo local {}".format(path))
        log("usando archivo local {}".format(path))
        return path

    WORK.mkdir(parents=True, exist_ok=True)
    zip_path = WORK / "padron.zip"
    if not zip_path.exists() or os.environ.get("FORCE_DOWNLOAD") == "1":
        download(sig["url"], zip_path)
    return extract_main_member(zip_path, WORK)
