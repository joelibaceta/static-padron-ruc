"""Size budget guard.

GitHub Pages rejects published sites larger than 1 GB, and the deploy times out
after 10 minutes. A site that grows slowly gives no warning: one day the deploy
simply fails, or worse, takes so long it times out. We prefer to break the build
here, with a per-folder breakdown, than to discover it in the deploy step.
"""

from __future__ import annotations

import sys
from pathlib import Path

from common import DIST, dir_size, human_bytes, load_config, log


def check(dist: Path, max_bytes: int) -> int:
    if not dist.exists():
        print("ERROR: {} does not exist".format(dist), file=sys.stderr)
        return 1

    entries = []
    for child in sorted(dist.iterdir()):
        size = dir_size(child) if child.is_dir() else child.stat().st_size
        entries.append((child.name, size))
    entries.sort(key=lambda item: -item[1])

    total = sum(size for _name, size in entries)
    log("site breakdown:")
    for name, size in entries:
        share = (size / total * 100) if total else 0
        log("  {:<28} {:>12}  {:5.1f}%".format(name, human_bytes(size), share))
    log("  {:<28} {:>12}".format("TOTAL", human_bytes(total)))
    log("budget: {}".format(human_bytes(max_bytes)))

    if total > max_bytes:
        print(
            "ERROR: the site weighs {} and exceeds the budget of {}.\n"
            "Options: lower dataset.shard_count, set dataset.include_domicilio to "
            "false, or move the dataset to Releases / R2 and keep only the UI on Pages.".format(
                human_bytes(total), human_bytes(max_bytes)
            ),
            file=sys.stderr,
        )
        return 1

    log("within budget ({} free)".format(human_bytes(max_bytes - total)))
    return 0


if __name__ == "__main__":
    cfg = load_config()
    sys.exit(check(DIST, int(cfg["budget"]["max_site_bytes"])))
