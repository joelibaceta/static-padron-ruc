"""Construye el SQLite que sql.js-httpvfs consulta por rangos HTTP.

Dos decisiones cargan todo el peso del diseno:

1. `ruc INTEGER PRIMARY KEY` convierte al RUC en el rowid. La consulta por RUC
   baja directo por el B-tree de la tabla, sin indice secundario y sin una
   indireccion extra: menos paginas leidas por consulta y ~200 MB menos de sitio.

2. `VACUUM` al final reescribe el archivo en orden de rowid. Sin eso las paginas
   quedan desperdigadas segun el orden de insercion y cada consulta pide chunks
   dispersos, que es justo lo que se paga caro sobre HTTP.
"""

from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Iterable, Tuple

from common import human_bytes, log, write_json

Record = Tuple[int, str, int, int, int, str]

SCHEMA = """
CREATE TABLE contribuyente (
  ruc     INTEGER PRIMARY KEY,
  nombre  TEXT NOT NULL,
  estado  INTEGER NOT NULL,
  cond    INTEGER NOT NULL,
  ubigeo  INTEGER NOT NULL,
  dom     TEXT
);
"""


class SqliteSink:
    """Acumula registros y los vuelca en lotes a un SQLite local."""

    BATCH = 50_000

    def __init__(self, db_path: Path, page_size: int, include_domicilio: bool):
        self.db_path = db_path
        self.include_domicilio = include_domicilio
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if db_path.exists():
            db_path.unlink()

        self.conn = sqlite3.connect(str(db_path))
        cur = self.conn.cursor()
        # page_size debe fijarse ANTES de crear cualquier tabla.
        cur.execute("PRAGMA page_size = {}".format(int(page_size)))
        cur.execute("PRAGMA journal_mode = OFF")
        cur.execute("PRAGMA synchronous = OFF")
        cur.execute("PRAGMA temp_store = MEMORY")
        cur.execute("PRAGMA cache_size = -262144")  # 256 MiB
        cur.executescript(SCHEMA)
        self.conn.commit()

        self._buf = []
        self.count = 0

    def add(self, rec: Record) -> None:
        ruc, nombre, estado, cond, ubigeo, dom = rec
        self._buf.append(
            (ruc, nombre, estado, cond, ubigeo, dom if self.include_domicilio else None)
        )
        self.count += 1
        if len(self._buf) >= self.BATCH:
            self._flush()

    def _flush(self) -> None:
        if not self._buf:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO contribuyente VALUES (?,?,?,?,?,?)", self._buf
        )
        self._buf.clear()

    def finalize(self, meta: dict) -> None:
        self._flush()
        self.conn.commit()
        log("sqlite: {:,} filas insertadas, compactando...".format(self.count))

        cur = self.conn.cursor()
        cur.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        cur.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [(k, str(v)) for k, v in meta.items()],
        )
        self.conn.commit()
        cur.execute("PRAGMA optimize")
        self.conn.commit()
        # VACUUM deja el archivo contiguo y en orden de rowid.
        self.conn.execute("VACUUM")
        self.conn.commit()
        self.conn.close()
        log("sqlite listo: {}".format(human_bytes(self.db_path.stat().st_size)))


def split_for_httpvfs(
    db_path: Path,
    out_dir: Path,
    request_chunk_size: int,
    server_chunk_size: int,
    url_prefix: str,
) -> dict:
    """Parte el SQLite en trozos y emite el config que espera sql.js-httpvfs.

    Se usa modo 'chunked' y se fija databaseLengthBytes explicitamente. No es
    cosmetico: GitHub Pages responde HEAD con `content-encoding: gzip` y un
    Content-Length comprimido, mientras que las respuestas por rango vienen sin
    comprimir. Un cliente que deduzca el tamano del HEAD calcula mal y falla.
    Al declarar el largo, el cliente nunca hace HEAD y el problema desaparece.
    """
    if server_chunk_size % request_chunk_size != 0:
        raise ValueError(
            "server_chunk_size ({}) debe ser multiplo de request_chunk_size ({})".format(
                server_chunk_size, request_chunk_size
            )
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*"):
        if stale.is_file():
            stale.unlink()

    total = db_path.stat().st_size
    suffix_length = 3
    parts = 0
    with open(db_path, "rb") as src:
        while True:
            chunk = src.read(server_chunk_size)
            if not chunk:
                break
            name = "{}.{:0{}d}".format(db_path.name, parts, suffix_length)
            with open(out_dir / name, "wb") as dst:
                dst.write(chunk)
            parts += 1

    log(
        "sqlite partido en {} trozo(s) de {}".format(
            parts, human_bytes(server_chunk_size)
        )
    )

    cfg = {
        "serverMode": "chunked",
        "urlPrefix": "{}{}.".format(url_prefix, db_path.name),
        "requestChunkSize": request_chunk_size,
        "serverChunkSize": server_chunk_size,
        "databaseLengthBytes": total,
        "suffixLength": suffix_length,
    }
    write_json(out_dir / "config.json", cfg)
    return cfg
