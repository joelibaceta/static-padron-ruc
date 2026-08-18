"""Ruta de respaldo: shards TSV comprimidos, direccionados por `ruc % N`.

Por que modulo y no prefijo: los RUC peruanos empiezan casi siempre en 10 o 20,
asi que shardear por los primeros digitos produce cubetas grotescamente
desbalanceadas. El modulo del numero completo reparte parejo sin necesitar un
hash.

Los shards guardan solo los campos core (sin domicilio). Duplicar el domicilio
aqui sumaria cientos de MB al sitio y competiria con el limite de 1 GB de
GitHub Pages, a cambio de un fallback marginalmente mas rico.

Escritura en dos fases para no abrir 4096 descriptores a la vez:
  fase 1  -> 256 cubetas crudas por `ruc % 256`
  fase 2  -> cada cubeta se abre, se parte en sus 16 shards finos, se ordena y
             se comprime.
Funciona porque 256 divide a 4096, asi que `(ruc % 4096) % 256 == ruc % 256`.
"""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

from common import dir_size, human_bytes, log, write_json

Record = Tuple[int, str, int, int, int, str]

COARSE = 256


class ShardSink:
    def __init__(self, work_dir: Path, out_dir: Path, shard_count: int):
        if shard_count % COARSE != 0:
            raise ValueError("shard_count debe ser multiplo de {}".format(COARSE))
        self.shard_count = shard_count
        self.out_dir = out_dir
        self.work_dir = work_dir / "buckets"
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._handles = [
            open(self.work_dir / "{:03d}.tsv".format(i), "w", encoding="utf-8")
            for i in range(COARSE)
        ]
        self.count = 0

    def add(self, rec: Record) -> None:
        ruc, nombre, estado, cond, ubigeo, _dom = rec
        # El nombre puede traer tabs en casos raros; el TSV se rompe si pasan.
        self._handles[ruc % COARSE].write(
            "{}\t{}\t{}\t{}\t{}\n".format(
                ruc, nombre.replace("\t", " "), estado, cond, ubigeo
            )
        )
        self.count += 1

    def finalize(self) -> dict:
        for fh in self._handles:
            fh.close()

        if self.out_dir.exists():
            shutil.rmtree(self.out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

        fine_per_coarse = self.shard_count // COARSE
        written = 0
        for bucket in range(COARSE):
            groups: Dict[int, List[Tuple[int, str]]] = {}
            src = self.work_dir / "{:03d}.tsv".format(bucket)
            with open(src, "r", encoding="utf-8") as fh:
                for line in fh:
                    ruc = int(line.split("\t", 1)[0])
                    groups.setdefault(ruc % self.shard_count, []).append((ruc, line))
            src.unlink()

            for offset in range(fine_per_coarse):
                shard_id = bucket + offset * COARSE
                rows = groups.get(shard_id, [])
                rows.sort(key=lambda item: item[0])
                path = self._shard_path(shard_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = "".join(row[1] for row in rows).encode("utf-8")
                # mtime=0 para que dos builds del mismo dato den bytes identicos.
                with gzip.GzipFile(str(path), "wb", compresslevel=9, mtime=0) as gz:
                    gz.write(payload)
                written += 1

            if bucket % 64 == 0:
                log("  shards: cubeta {}/{}".format(bucket, COARSE))

        shutil.rmtree(self.work_dir, ignore_errors=True)
        total = dir_size(self.out_dir)
        log(
            "shards listos: {} archivos, {} en total ({} por shard en promedio)".format(
                written, human_bytes(total), human_bytes(total // max(written, 1))
            )
        )

        manifest = {
            "format": "tsv.gz",
            "columns": ["ruc", "nombre", "estado", "cond", "ubigeo"],
            "shardCount": self.shard_count,
            "pathTemplate": "shards/{dir}/{id}.tsv.gz",
            "records": self.count,
            "totalBytes": total,
        }
        write_json(self.out_dir.parent / "shards-manifest.json", manifest)
        return manifest

    def _shard_path(self, shard_id: int) -> Path:
        hex_id = "{:03x}".format(shard_id)
        return self.out_dir / hex_id[0] / "{}.tsv.gz".format(hex_id)
