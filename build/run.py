"""Build orchestrator.

Two modes:

  python build/run.py check   -> resolves the source, compares it against the
                                 meta.json of the ALREADY published site and
                                 decides whether a rebuild is needed. Does not
                                 download the padron.

  python build/run.py build   -> downloads, parses once and feeds the core and
                                 domicilio shard sets, assembles dist/ and writes
                                 meta.json.

The state of the last build lives in the deployed site, not in the repository.
Nothing is ever committed: the git history stays the size of the code.
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
    COND_LABELS,
    DIST,
    ESTADO_LABELS,
    ROOT,
    WORK,
    load_config,
    log,
    write_json,
)
from shards import ShardSink, core_line, domicilio_line  # noqa: E402


def emit_output(**kwargs) -> None:
    """Write outputs for GitHub Actions (and print them for humans)."""
    path = os.environ.get("GITHUB_OUTPUT")
    for key, value in kwargs.items():
        line = "{}={}".format(key, value)
        print(line)
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def cmd_check(args, cfg) -> int:
    sig = discover.resolve(cfg)
    previous = discover.previous_signature(args.base_url)
    prev_sig = (previous or {}).get("source") or {}

    same = (
        bool(previous)
        and prev_sig.get("etag") == sig.get("etag")
        and prev_sig.get("last_modified") == sig.get("last_modified")
        and prev_sig.get("content_length") == sig.get("content_length")
    )
    # A dataset published with another pipeline version must be rebuilt even if
    # the source has not changed.
    same = same and (previous or {}).get("pipeline") == pipeline_version(cfg)

    should = args.force or not same
    log("remote signature : {}".format(json.dumps(sig, ensure_ascii=False)))
    log("previous signature : {}".format(json.dumps(prev_sig, ensure_ascii=False)))
    log("rebuild : {}".format(should))
    emit_output(
        should_build=str(should).lower(),
        source_url=sig["url"],
        etag=sig.get("etag") or "",
        last_modified=sig.get("last_modified") or "",
    )
    return 0


def pipeline_version(cfg: dict) -> str:
    """Changes when the dataset shape changes, to force a rebuild."""
    ds = cfg["dataset"]
    return "v2|dom={}:{}|shards={}".format(
        ds["include_domicilio"],
        ds.get("max_domicilio_chars", 90),
        ds["shard_count"],
    )


def cmd_build(args, cfg) -> int:
    source_cfg = cfg["source"]
    ds = cfg["dataset"]
    include_dom = ds["include_domicilio"]

    if args.local_txt:
        sig = {"url": "local:" + args.local_txt, "etag": None,
               "last_modified": None, "content_length": None}
    else:
        sig = discover.resolve(cfg)

    txt_path, sig["sha256"] = prepare.prepare_source(cfg, sig, args.local_txt)

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    core_sink = ShardSink(WORK, DIST / "shards", ds["shard_count"],
                          ["ruc", "nombre", "estado", "cond", "ubigeo"], core_line)
    dom_sink = (
        ShardSink(WORK, DIST / "dom", ds["shard_count"], ["ruc", "dom"], domicilio_line)
        if include_dom else None
    )

    log("parsing and feeding the shard sets in a single pass...")
    records = prepare.iter_records(
        txt_path,
        source_cfg["encoding"],
        source_cfg["delimiter"],
        include_dom,
        int(ds.get("max_domicilio_chars", 90)),
    )

    total = 0
    for rec in records:
        core_sink.add(rec)
        if dom_sink:
            dom_sink.add(rec)
        total += 1
        if args.limit and total >= args.limit:
            log("stopped by --limit at {:,} records".format(total))
            break

    if total == 0:
        print("ERROR: no records were parsed; aborting.", file=sys.stderr)
        return 1

    snapshot = sig.get("last_modified") or dt.datetime.now(dt.timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    core_manifest = core_sink.finalize()
    dom_manifest = dom_sink.finalize() if dom_sink else None

    # Static UI.
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
        "includeDomicilio": include_dom,
        "shards": core_manifest,
        "domicilio": dom_manifest,
        "codes": {
            "estado": {str(c): lbl for c, lbl in ESTADO_LABELS.items()},
            "cond": {str(c): lbl for c, lbl in COND_LABELS.items()},
        },
    }
    write_json(DIST / "meta.json", meta)
    # .nojekyll prevents Pages from swallowing folders that start with an
    # underscore and skips the Jekyll build step.
    (DIST / ".nojekyll").write_text("", encoding="utf-8")

    log("dist ready with {:,} records".format(total))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build of the static RUC padron")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="decide whether a rebuild is needed")
    p_check.add_argument("--base-url", default=os.environ.get("SITE_BASE_URL", ""))
    p_check.add_argument("--force", action="store_true")

    p_build = sub.add_parser("build", help="build dist/")
    p_build.add_argument("--local-txt", default=None,
                         help="use a local .txt instead of downloading from SUNAT")
    p_build.add_argument("--limit", type=int, default=0,
                         help="stop after N records (for testing)")

    args = parser.parse_args()
    cfg = load_config()

    if args.cmd == "check":
        return cmd_check(args, cfg)
    return cmd_build(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
