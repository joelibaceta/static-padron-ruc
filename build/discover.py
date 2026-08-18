"""Descubre la URL vigente del padron reducido y su firma (ETag / Last-Modified).

SUNAT no publica un manifiesto estable, asi que rascamos la pagina de descarga y
buscamos enlaces .zip. Si eso falla, caemos a las URLs de respaldo de config.json.
Cualquiera de las dos rutas puede romperse si SUNAT reacomoda el sitio: por eso el
build falla ruidosamente en vez de deployar un dataset vacio.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional

from common import die, log

USER_AGENT = (
    "padron-ruc-static/1.0 (+https://github.com/) "
    "construye un indice estatico del padron reducido publico de SUNAT"
)

ZIP_HREF = re.compile(r'href\s*=\s*["\']([^"\']+\.zip)["\']', re.IGNORECASE)


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    return urllib.request.Request(
        url, method=method, headers={"User-Agent": USER_AGENT}
    )


def fetch_text(url: str, timeout: int = 60) -> str:
    with urllib.request.urlopen(_request(url), timeout=timeout) as resp:
        raw = resp.read()
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def signature(url: str, timeout: int = 60) -> dict:
    """Firma barata del recurso remoto, para decidir si vale la pena reconstruir.

    Se intenta HEAD; algunos frontends de SUNAT no lo soportan bien, en cuyo caso
    se cae a un GET con Range de 1 byte, que devuelve los mismos validadores sin
    descargar cientos de MB.
    """
    try:
        with urllib.request.urlopen(_request(url, "HEAD"), timeout=timeout) as resp:
            headers = resp.headers
            length = headers.get("Content-Length")
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 405, 501):
            raise
        req = _request(url)
        req.add_header("Range", "bytes=0-0")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            headers = resp.headers
            content_range = headers.get("Content-Range", "")
            length = content_range.rsplit("/", 1)[-1] if "/" in content_range else None

    return {
        "url": url,
        "etag": (headers.get("ETag") or "").strip('"') or None,
        "last_modified": headers.get("Last-Modified") or None,
        "content_length": int(length) if length and length.isdigit() else None,
    }


def discover_zips(cfg: dict, kind: str = "main") -> List[str]:
    """Devuelve URLs candidatas de zip, mas probable primero."""
    source = cfg["source"]
    listing_url = source["listing_url"]
    candidates: List[str] = []

    try:
        html = fetch_text(listing_url)
        for href in ZIP_HREF.findall(html):
            candidates.append(urllib.parse.urljoin(listing_url, href))
        log("pagina de descarga: {} enlace(s) .zip encontrados".format(len(candidates)))
    except Exception as exc:  # noqa: BLE001 - queremos degradar, no explotar
        log("no se pudo leer {}: {}".format(listing_url, exc))

    is_anexo = lambda u: "anexo" in u.lower()  # noqa: E731
    if kind == "anexo":
        candidates = [u for u in candidates if is_anexo(u)]
        candidates += source.get("anexo_fallback_zip_urls", [])
    else:
        candidates = [u for u in candidates if not is_anexo(u)]
        candidates += source.get("fallback_zip_urls", [])

    seen = set()
    ordered = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def resolve(cfg: dict, kind: str = "main") -> dict:
    """Primera URL candidata que responde. Lanza si ninguna sirve."""
    candidates = discover_zips(cfg, kind)
    if not candidates:
        die(
            "no se encontro ninguna URL de zip para '{}'. Revisa source.listing_url "
            "y source.fallback_zip_urls en config.json.".format(kind)
        )

    errors = []
    for url in candidates:
        try:
            sig = signature(url)
            log("fuente '{}' resuelta: {} ({} bytes)".format(kind, url, sig["content_length"]))
            return sig
        except Exception as exc:  # noqa: BLE001
            errors.append("{} -> {}".format(url, exc))

    die(
        "ninguna URL candidata respondio para '{}':\n  {}".format(
            kind, "\n  ".join(errors)
        )
    )
    return {}  # inalcanzable, calla al type checker


def previous_signature(base_url: Optional[str]) -> Optional[dict]:
    """Lee el meta.json del sitio ya publicado.

    Truco central del diseno: el estado del ultimo build vive en el propio sitio
    desplegado, no en el repositorio. Asi nunca commiteamos nada y el historial
    de git se queda del tamano del codigo fuente.
    """
    if not base_url:
        return None
    url = base_url.rstrip("/") + "/meta.json"
    try:
        import json

        return json.loads(fetch_text(url, timeout=30))
    except Exception as exc:  # noqa: BLE001
        log("no hay meta.json previo en {} ({}); se construira de cero".format(url, exc))
        return None


if __name__ == "__main__":
    import json
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    from common import load_config

    print(json.dumps(resolve(load_config()), indent=2))
