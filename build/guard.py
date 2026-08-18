"""Guard de presupuesto de tamano.

GitHub Pages rechaza sitios publicados de mas de 1 GB, y el deploy expira a los
10 minutos. Un sitio que crece de a poco no avisa: simplemente un dia el deploy
falla, o peor, tarda tanto que expira. Preferimos romper el build aqui, con un
desglose por carpeta, que descubrirlo en el paso de deploy.
"""

from __future__ import annotations

import sys
from pathlib import Path

from common import DIST, dir_size, human_bytes, load_config, log


def check(dist: Path, max_bytes: int) -> int:
    if not dist.exists():
        print("ERROR: no existe {}".format(dist), file=sys.stderr)
        return 1

    entries = []
    for child in sorted(dist.iterdir()):
        size = dir_size(child) if child.is_dir() else child.stat().st_size
        entries.append((child.name, size))
    entries.sort(key=lambda item: -item[1])

    total = sum(size for _name, size in entries)
    log("desglose del sitio:")
    for name, size in entries:
        share = (size / total * 100) if total else 0
        log("  {:<28} {:>12}  {:5.1f}%".format(name, human_bytes(size), share))
    log("  {:<28} {:>12}".format("TOTAL", human_bytes(total)))
    log("presupuesto: {}".format(human_bytes(max_bytes)))

    if total > max_bytes:
        print(
            "ERROR: el sitio pesa {} y supera el presupuesto de {}.\n"
            "Opciones: baja dataset.shard_count, pon dataset.include_domicilio en "
            "false, o mueve el dataset a Releases / R2 y deja en Pages solo la UI.".format(
                human_bytes(total), human_bytes(max_bytes)
            ),
            file=sys.stderr,
        )
        return 1

    log("dentro del presupuesto ({} libres)".format(human_bytes(max_bytes - total)))
    return 0


if __name__ == "__main__":
    cfg = load_config()
    sys.exit(check(DIST, int(cfg["budget"]["max_site_bytes"])))
