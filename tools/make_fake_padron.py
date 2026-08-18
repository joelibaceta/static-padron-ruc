"""Genera un padron sintetico con la forma del archivo real de SUNAT.

Sirve para probar el pipeline entero sin descargar cientos de MB, y para tener
un caso reproducible en CI. Los RUC generados tienen digito verificador valido.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "build"))

from common import ruc_check_digit  # noqa: E402

HEADER = (
    "RUC|NOMBRE O RAZON SOCIAL|ESTADO DEL CONTRIBUYENTE|CONDICION DE DOMICILIO|"
    "UBIGEO|TIPO DE VIA|NOMBRE DE VIA|CODIGO ZONA|TIPO ZONA|NUMERO|INTERIOR|"
    "LOTE|DEPARTAMENTO|MANZANA|KILOMETRO"
)

ESTADOS = ["ACTIVO"] * 7 + ["BAJA DEFINITIVA", "SUSPENSION TEMPORAL", "BAJA DE OFICIO"]
CONDICIONES = ["HABIDO"] * 8 + ["NO HABIDO", "NO HALLADO"]
TIPOS_VIA = ["AV.", "JR.", "CAL.", "PSJ.", "CAR."]
NOMBRES_VIA = ["LOS PROCERES", "ARENALES", "JAVIER PRADO ESTE", "LA MARINA", "SAN MARTIN"]
PALABRAS = [
    "ANDINA", "PACIFICO", "GLOBAL", "TECNOLOGIAS", "SERVICIOS", "CORPORACION",
    "INVERSIONES", "DISTRIBUIDORA", "CONSTRUCTORA", "SOLUCIONES", "PERUANA",
    "AMAZONICA", "TRANSPORTES", "AGROINDUSTRIAL", "CONSULTORES", "LOGISTICA",
]
SUFIJOS = ["S.A.C.", "S.R.L.", "E.I.R.L.", "S.A.", "S.A.A."]
NOMBRES = ["MARIA", "JOSE", "ANA", "LUIS", "ROSA", "CARLOS", "NIEVES"]
APELLIDOS = ["QUISPE", "MAMANI", "GARCIA", "IBACETA", "ÑAUPA", "VASQUEZ", "CACERES"]


def make_ruc(rng: random.Random, prefix: str) -> str:
    body = prefix + "".join(str(rng.randint(0, 9)) for _ in range(8))
    return body + str(ruc_check_digit(body))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default=".work/fake_padron.txt")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    seen = set()

    with open(out, "w", encoding="cp1252", errors="replace", newline="\r\n") as fh:
        fh.write(HEADER + "\n")
        while len(seen) < args.rows:
            juridica = rng.random() < 0.45
            ruc = make_ruc(rng, "20" if juridica else "10")
            if ruc in seen:
                continue
            seen.add(ruc)

            if juridica:
                nombre = "{} {} {}".format(
                    rng.choice(PALABRAS), rng.choice(PALABRAS), rng.choice(SUFIJOS)
                )
            else:
                nombre = "{} {} {}".format(
                    rng.choice(APELLIDOS), rng.choice(APELLIDOS), rng.choice(NOMBRES)
                )

            fh.write(
                "|".join(
                    [
                        ruc,
                        nombre,
                        rng.choice(ESTADOS),
                        rng.choice(CONDICIONES),
                        "{:06d}".format(rng.randint(10101, 250401)),
                        rng.choice(TIPOS_VIA),
                        rng.choice(NOMBRES_VIA),
                        "-",
                        "-",
                        str(rng.randint(100, 4999)),
                        rng.choice(["-", str(rng.randint(1, 20))]),
                        "-",
                        "-",
                        rng.choice(["-", "A", "B", "F"]),
                        "-",
                    ]
                )
                + "\n"
            )

    print("escrito {} con {:,} filas".format(out, len(seen)))
    # Deja a mano un RUC valido para probar la UI.
    print("ejemplo consultable: {}".format(sorted(seen)[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
