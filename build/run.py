"""Orquestador del build.

Dos modos:

  python build/run.py check   -> resuelve la fuente, la compara contra el
                                 meta.json del sitio YA publicado y decide si
                                 hay que reconstruir. No descarga el padron.

  python build/run.py build   -> descarga, parsea una sola vez y alimenta en
                                 paralelo el SQLite y los shards, arma dist/ y
                                 escribe meta.json.

El estado del ultimo build vive en el sitio desplegado, no en el repositorio.
Nada se commitea nunca: el historial de git se queda del tamano del codigo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import discover  # noqa: E402
import prepare  # noqa: E402
from common import (  # noqa: E402
    COND_CODES,
    DIST,
    ESTADO_CODES,
    ROOT,
    WORK,
    human_bytes,
    load_config,
    log,
    write_json,
)
from shards import ShardSink  # noqa: E402
from sqlite_index import SqliteSink, split_for_httpvfs  # noqa: E402


def emit_output(**kwargs) -> None:
    """Escribe outputs para GitHub Actions (y los imprime para humanos)."""
    path = os.environ.get("GITHUB_OUTPUT")
    for key, value in kwargs.items():
        line = "{}={}".format(key, value)
        print(line)
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def cmd_check(args, cfg) -> int:
    sig = discover.resolve(cfg, "main")
    previous = discover.previous_signature(args.base_url)
    prev_sig = (previous or {}).get("source") or {}

    same = (
        bool(previous)
        and prev_sig.get("etag") == sig.get("etag")
        and prev_sig.get("last_modified") == sig.get("last_modified")
        and prev_sig.get("content_length") == sig.get("content_length")
    )
    # Un dataset publicado con otra version del pipeline debe reconstruirse
    # aunque la fuente no haya cambiado.
    same = same and (previous or {}).get("pipeline") == pipeline_version(cfg)

    should = args.force or not same
    log("firma remota : {}".format(json.dumps(sig, ensure_ascii=False)))
    log("firma previa : {}".format(json.dumps(prev_sig, ensure_ascii=False)))
    log("reconstruir  : {}".format(should))
    emit_output(
        should_build=str(should).lower(),
        source_url=sig["url"],
        etag=sig.get("etag") or "",
        last_modified=sig.get("last_modified") or "",
    )
    return 0


def pipeline_version(cfg: dict) -> str:
    """Cambia cuando cambia la forma del dataset, para forzar rebuild."""
    ds = cfg["dataset"]
    sq = cfg["sqlite"]
    return "v1|dom={}:{}|anexos={}|shards={}|page={}|chunk={}".format(
        ds["include_domicilio"],
        ds.get("max_domicilio_chars", 90),
        ds["include_anexos"],
        ds["shard_count"],
        sq["page_size"],
        sq["request_chunk_size"],
    )


def cmd_build(args, cfg) -> int:
    source_cfg = cfg["source"]
    ds = cfg["dataset"]
    sq = cfg["sqlite"]

    if args.local_txt:
        sig = {"url": "local:" + args.local_txt, "etag": None,
               "last_modified": None, "content_length": None}
    else:
        sig = discover.resolve(cfg, "main")

    txt_path = prepare.prepare_source(cfg, sig, args.local_txt)

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    db_path = WORK / "padron.sqlite3"
    sqlite_sink = SqliteSink(db_path, sq["page_size"], ds["include_domicilio"])
    shard_sink = ShardSink(WORK, DIST / "shards", ds["shard_count"])

    log("parseando y alimentando ambos indices en una sola pasada...")
    records = prepare.iter_records(
        txt_path,
        source_cfg["encoding"],
        source_cfg["delimiter"],
        ds["include_domicilio"],
        int(ds.get("max_domicilio_chars", 90)),
    )

    total = 0
    for rec in records:
        sqlite_sink.add(rec)
        shard_sink.add(rec)
        total += 1
        if args.limit and total >= args.limit:
            log("corte por --limit en {:,} registros".format(total))
            break

    if total == 0:
        print("ERROR: no se parseo ningun registro; abortando.", file=sys.stderr)
        return 1

    snapshot = sig.get("last_modified") or dt.datetime.now(dt.timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    sqlite_sink.finalize({"records": total, "snapshot": snapshot})
    shard_manifest = shard_sink.finalize()

    db_cfg = split_for_httpvfs(
        db_path,
        DIST / "db",
        request_chunk_size=sq["request_chunk_size"],
        server_chunk_size=sq["split_bytes"],
        url_prefix="db/",
    )

    # UI estatica.
    for item in (ROOT / "web").iterdir():
        target = DIST / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)

    meta = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "pipeline": pipeline_version(cfg),
        "source": sig,
        "snapshot": snapshot,
        "records": total,
        "includeDomicilio": ds["include_domicilio"],
        "sqlite": db_cfg,
        "shards": shard_manifest,
        "codes": {
            "estado": {str(v): k for k, v in ESTADO_CODES.items()},
            "cond": {str(v): k for k, v in COND_CODES.items()},
        },
    }
    write_json(DIST / "meta.json", meta)
    # .nojekyll evita que Pages se coma las carpetas que empiezan con guion bajo
    # y ahorra el paso de build de Jekyll.
    (DIST / ".nojekyll").write_text("", encoding="utf-8")

    log("dist listo con {:,} registros".format(total))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build del padron RUC estatico")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="decide si hay que reconstruir")
    p_check.add_argument("--base-url", default=os.environ.get("SITE_BASE_URL", ""))
    p_check.add_argument("--force", action="store_true")

    p_build = sub.add_parser("build", help="construye dist/")
    p_build.add_argument("--local-txt", default=None,
                         help="usa un .txt local en vez de descargar de SUNAT")
    p_build.add_argument("--limit", type=int, default=0,
                         help="corta despues de N registros (para pruebas)")

    args = parser.parse_args()
    cfg = load_config()

    if args.cmd == "check":
        return cmd_check(args, cfg)
    return cmd_build(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
