"""Servidor estatico local CON soporte de Range requests.

`python -m http.server` NO implementa `Range`: devuelve el archivo entero con
200 e ignora la cabecera. Contra eso, sql.js-httpvfs lee bytes equivocados y
SQLite reporta "database disk image is malformed" — un sintoma que parece un
build corrupto y en realidad es el servidor de pruebas.

GitHub Pages si sirve rangos, asi que este modulo existe solo para que el
entorno local se parezca a produccion.
"""

from __future__ import annotations

import argparse
import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangeHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".gz": "application/gzip",
        ".json": "application/json",
        ".mjs": "text/javascript",
    }

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Accept-Ranges, Content-Range")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        header = self.headers.get("Range")
        if not header:
            return super().send_head()

        match = RANGE_RE.match(header.strip())
        path = self.translate_path(self.path)
        if not match or not os.path.isfile(path):
            return super().send_head()

        size = os.path.getsize(path)
        start_raw, end_raw = match.groups()
        if start_raw == "":
            # bytes=-N -> ultimos N bytes
            length = int(end_raw or 0)
            start, end = max(0, size - length), size - 1
        else:
            start = int(start_raw)
            end = int(end_raw) if end_raw else size - 1
        end = min(end, size - 1)

        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", "bytes */{}".format(size))
            self.end_headers()
            return None

        fh = open(path, "rb")
        fh.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", "bytes {}-{}/{}".format(start, end, size))
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        # Se acota la lectura al rango pedido.
        self._range_remaining = end - start + 1
        return _Bounded(fh, self._range_remaining)

    def log_message(self, fmt, *args):  # menos ruido
        if os.environ.get("SERVE_VERBOSE"):
            super().log_message(fmt, *args)


class _Bounded:
    """Envuelve un archivo para que copyfile() no se pase del rango."""

    def __init__(self, fh, remaining):
        self._fh = fh
        self._remaining = remaining

    def read(self, size=-1):
        if self._remaining <= 0:
            return b""
        if size is None or size < 0:
            size = self._remaining
        data = self._fh.read(min(size, self._remaining))
        self._remaining -= len(data)
        return data

    def close(self):
        self._fh.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="dist")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    handler = partial(RangeHandler, directory=args.dir)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print("sirviendo {} en http://localhost:{} (con Range)".format(args.dir, args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
