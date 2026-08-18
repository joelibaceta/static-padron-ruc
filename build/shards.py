"""Gzipped TSV shards addressed by `ruc % N` — the site's only index.

Why modulo and not a prefix: Peruvian RUCs almost always start with 10 or 20, so
sharding by the first digits produces grotesquely unbalanced buckets. The modulo
of the whole number spreads evenly without needing a hash.

Two shard sets share this machinery:
  core       -> ruc, nombre, estado, cond, ubigeo (every record)
  domicilio  -> ruc, dom (only the ~14% of records that carry an address)

Domicilio lives in its own compressed sidecar so the core index stays small and
the client fetches an address lazily, only when it renders a result.

Two-phase writing so we don't open shard_count descriptors at once:
  phase 1  -> 256 raw buckets by `ruc % 256`
  phase 2  -> each bucket is opened, split into its fine shards, sorted, compressed.
Works because 256 divides shard_count, so `(ruc % shard_count) % 256 == ruc % 256`.
"""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from common import dir_size, human_bytes, log, write_json

Record = Tuple[int, str, int, int, int, str]
ToLine = Callable[[Record], Optional[str]]

COARSE = 256


def core_line(rec: Record) -> str:
    ruc, nombre, estado, cond, ubigeo, _dom = rec
    # A stray tab in a name would break the TSV.
    return "{}\t{}\t{}\t{}\t{}\n".format(ruc, nombre.replace("\t", " "), estado, cond, ubigeo)


def domicilio_line(rec: Record) -> Optional[str]:
    ruc, _nombre, _estado, _cond, _ubigeo, dom = rec
    if not dom:
        return None
    return "{}\t{}\n".format(ruc, dom.replace("\t", " "))


class ShardSink:
    """Bucket records to disk, then emit sorted gzipped TSV shards.

    `to_line` turns a record into a shard line, or None to drop it (the domicilio
    sidecar uses this to keep only rows that carry an address).
    """

    def __init__(self, work_dir: Path, out_dir: Path, shard_count: int,
                 columns: List[str], to_line: ToLine):
        if shard_count % COARSE != 0:
            raise ValueError("shard_count must be a multiple of {}".format(COARSE))
        self.shard_count = shard_count
        self.out_dir = out_dir
        self.columns = columns
        self.to_line = to_line
        self.work_dir = work_dir / ("buckets_" + out_dir.name)
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._handles = [
            open(self.work_dir / "{:03d}.tsv".format(i), "w", encoding="utf-8")
            for i in range(COARSE)
        ]
        self.count = 0

    def add(self, rec: Record) -> None:
        line = self.to_line(rec)
        if line is None:
            return
        self._handles[rec[0] % COARSE].write(line)
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
                # mtime=0 so two builds of the same data give identical bytes.
                with gzip.GzipFile(str(path), "wb", compresslevel=9, mtime=0) as gz:
                    gz.write(payload)
                written += 1

            if bucket % 64 == 0:
                log("  shards ({}): bucket {}/{}".format(self.out_dir.name, bucket, COARSE))

        shutil.rmtree(self.work_dir, ignore_errors=True)
        total = dir_size(self.out_dir)
        log("shards ({}) ready: {} files, {} total ({} avg)".format(
            self.out_dir.name, written, human_bytes(total), human_bytes(total // max(written, 1))))

        manifest = {
            "format": "tsv.gz",
            "columns": self.columns,
            "shardCount": self.shard_count,
            "pathTemplate": self.out_dir.name + "/{dir}/{id}.tsv.gz",
            "records": self.count,
            "totalBytes": total,
        }
        write_json(self.out_dir.parent / (self.out_dir.name + "-manifest.json"), manifest)
        return manifest

    def _shard_path(self, shard_id: int) -> Path:
        hex_id = "{:03x}".format(shard_id)
        return self.out_dir / hex_id[0] / "{}.tsv.gz".format(hex_id)
