"""Discover the current padron reducido zip URL and its signature (ETag / Last-Modified).

SUNAT publishes no stable manifest, so we scrape the download page for .zip links
and fall back to the URLs in config.json. Either path can break if SUNAT rearranges
the site, so the build fails loudly instead of deploying an empty dataset.
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
    "builds a static index of SUNAT's public padron reducido"
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
    """Cheap signature of the remote resource, to decide whether to rebuild.

    Tries HEAD; some SUNAT frontends handle it poorly, so it falls back to a GET
    with a 1-byte Range, which returns the same validators without downloading
    hundreds of MB.
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


def discover_zips(cfg: dict) -> List[str]:
    """Return candidate padron zip URLs, most likely first."""
    source = cfg["source"]
    listing_url = source["listing_url"]
    candidates: List[str] = []

    try:
        html = fetch_text(listing_url)
        for href in ZIP_HREF.findall(html):
            url = urllib.parse.urljoin(listing_url, href)
            if "anexo" not in url.lower():  # the anexo zip is a different dataset
                candidates.append(url)
        log("download page: {} .zip link(s) found".format(len(candidates)))
    except Exception as exc:  # noqa: BLE001 - degrade, don't blow up
        log("could not read {}: {}".format(listing_url, exc))

    candidates += source.get("fallback_zip_urls", [])

    seen = set()
    ordered = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def resolve(cfg: dict) -> dict:
    """First candidate URL that responds. Raises if none work."""
    candidates = discover_zips(cfg)
    if not candidates:
        die(
            "no zip URL found. Check source.listing_url and "
            "source.fallback_zip_urls in config.json."
        )

    errors = []
    for url in candidates:
        try:
            sig = signature(url)
            log("source resolved: {} ({} bytes)".format(url, sig["content_length"]))
            return sig
        except Exception as exc:  # noqa: BLE001
            errors.append("{} -> {}".format(url, exc))

    die("no candidate URL responded:\n  {}".format("\n  ".join(errors)))
    return {}  # unreachable; keeps the type checker quiet


def previous_signature(base_url: Optional[str]) -> Optional[dict]:
    """Read meta.json from the already-published site.

    Core design trick: the last build's state lives on the deployed site, not in
    the repository, so nothing is ever committed and git history stays the size of
    the source code.
    """
    if not base_url:
        return None
    url = base_url.rstrip("/") + "/meta.json"
    try:
        import json

        return json.loads(fetch_text(url, timeout=30))
    except Exception as exc:  # noqa: BLE001
        log("no previous meta.json at {} ({}); building from scratch".format(url, exc))
        return None


if __name__ == "__main__":
    import json
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    from common import load_config

    print(json.dumps(resolve(load_config()), indent=2))
