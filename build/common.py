"""Shared utilities for the build pipeline."""

from __future__ import annotations

import json
import os
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
WORK = ROOT / ".work"

# Contribuyente estado: code -> human label (shown in the UI via meta.json).
ESTADO_LABELS = {
    0: "ACTIVO",
    1: "BAJA DE OFICIO",
    2: "BAJA DEFINITIVA",
    3: "BAJA PROVISIONAL",
    4: "SUSPENSION TEMPORAL",
    5: "INHABILITADO-VENT.UNICA",
    6: "PENDIENTE DE INICIO DE ACTIVIDAD",
    7: "ANULACION DEL NUMERO INTERNO",
    8: "BAJA PROVISIONAL POR OFICIO",
    9: "BAJA POR NO ACTIVIDAD",
    10: "BAJA MULTIPLE INSCRIPCION",
    11: "NUMERO INTERNO IDENTIFICATORIO",
    12: "OTROS OBLIGADOS",
    13: "ANULACION",
    255: "DESCONOCIDO",
}

# Raw source value (a fixed-width field, so SUNAT truncates it) -> code.
ESTADO_CODES = {
    "ACTIVO": 0,
    "BAJA DE OFICIO": 1,
    "BAJA DEFINITIVA": 2,
    "BAJA PROVISIONAL": 3,
    "BAJA PROV. POR OFICI": 8,
    "SUSPENSION TEMPORAL": 4,
    "INHABILITADO-VENT.UNICA": 5,
    "INHABILITADO-VENT.UN": 5,
    "PENDIENTE DE INICIO DE ACTIVIDAD": 6,
    "PENDIENTE DE INI. DE": 6,
    "ANULACION DEL NUMERO INTERNO": 7,
    "NUM. INTERNO IDENTIF": 11,
    "BAJA NO ACTIV. SISEV": 9,
    "BAJA MULT.INSCR. Y O": 10,
    "OTROS OBLIGADOS": 12,
    "ANULACION - ERROR SU": 13,
    "ANULACION - ACTO ILI": 13,
    "ANUL.PROVI.-ACTO ILI": 13,
}

# Domicilio condition: code -> human label.
COND_LABELS = {
    0: "HABIDO",
    1: "NO HABIDO",
    2: "NO HALLADO",
    3: "POR VERIFICAR",
    4: "PENDIENTE",
    5: "-",
    6: "NO APLICABLE",
    255: "DESCONOCIDO",
}

COND_CODES = {
    "HABIDO": 0,
    "NO HABIDO": 1,
    "NO HALLADO": 2,
    "POR VERIFICAR": 3,
    "PENDIENTE": 4,
    "": 5,
    "NO APLICABLE": 6,
}


def estado_code(raw: str) -> "int | None":
    return ESTADO_CODES.get(raw)


def cond_code(raw: str) -> "int | None":
    if raw in COND_CODES:
        return COND_CODES[raw]
    # NO HALLADO ships with many truncated reasons; collapse to the base state.
    if raw.startswith("NO HALLADO"):
        return 2
    return None


def log(msg: str) -> None:
    elapsed = time.monotonic() - _T0
    print("[{:7.1f}s] {}".format(elapsed, msg), flush=True)


_T0 = time.monotonic()


def die(msg: str, code: int = 1):
    print("ERROR: " + msg, file=sys.stderr, flush=True)
    sys.exit(code)


def load_config(path: "Path | None" = None) -> dict:
    path = path or (ROOT / "config.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def strip_accents(text: str) -> str:
    """Strip accents so headers can be compared regardless of encoding."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def header_key(name: str) -> str:
    """Normalize a column name into something stably comparable."""
    cleaned = strip_accents(name).upper()
    return "".join(ch if ch.isalnum() else " " for ch in cleaned).split()


def norm_header(name: str) -> str:
    return " ".join(header_key(name))


def clean_text(value: str) -> str:
    """Collapse whitespace and strip typical padron junk."""
    value = value.replace("\x00", " ").strip()
    if not value or value == "-":
        return ""
    return " ".join(value.split())


def ruc_check_digit(first_ten: str) -> int:
    """RUC check digit (modulo 11, weights 5 4 3 2 7 6 5 4 3 2)."""
    weights = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    total = sum(int(d) * w for d, w in zip(first_ten, weights))
    residue = total % 11
    check = 11 - residue
    if check == 10:
        return 0
    if check == 11:
        return 1
    return check


def is_valid_ruc(ruc: str) -> bool:
    if len(ruc) != 11 or not ruc.isdigit():
        return False
    return ruc_check_digit(ruc[:10]) == int(ruc[10])


def human_bytes(n: int) -> str:
    step = 1024.0
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < step or unit == "GiB":
            return "{:.1f} {}".format(value, unit)
        value /= step
    return "{:.1f} GiB".format(value)


def dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            fp = Path(dirpath) / name
            if fp.is_file():
                total += fp.stat().st_size
    return total


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
