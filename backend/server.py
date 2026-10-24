"""
Adaptive UDP Streaming — Local Test Server (backend)
====================================================

Serves the web front end (``frontend/``) and a small JSON API:

    GET  /api/info       protocol constants and upload limit
    GET  /api/history    recent transfers (in memory, newest first)
    POST /api/transfer   raw file body; settings via query string
                         (?filename=&arq=&interval=&max_retries=&initial_rto=)

Usage::

    python -m backend.server --port 8000
"""

import argparse
import json
import logging
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from backend.service import (
    HISTORY,
    MAX_UPLOAD_BYTES,
    TransferError,
    TransferParams,
    protocol_info,
    run_transfer,
)

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


class Handler(BaseHTTPRequestHandler):
    server_version = "AUDPLocal/1.0"

    # -- helpers ----------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _static(self, url_path: str) -> None:
        rel = "index.html" if url_path in ("", "/") else url_path.lstrip("/")
        target = (FRONTEND_DIR / rel).resolve()
        if FRONTEND_DIR.resolve() not in target.parents or not target.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)

    # -- routes -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/info":
            self._json(HTTPStatus.OK, protocol_info())
        elif path == "/api/history":
            self._json(HTTPStatus.OK, HISTORY.list())
        elif path.startswith("/api/"):
            self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})
        else:
            self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path != "/api/transfer":
            self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Bad Content-Length"})
            return
        if length > MAX_UPLOAD_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                       {"error": f"File too large (max {MAX_UPLOAD_BYTES} bytes)."})
            return
        query = dict(parse_qsl(parts.query))
        try:
            params = TransferParams.from_mapping(query)
            record = run_transfer(query.get("filename", "upload.bin"),
                                  self.rfile.read(length), params)
        except TransferError as exc:
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
            return
        except Exception:  # unexpected: log detail, return generic message
            logger.exception("Unhandled error during transfer")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal error"})
            return
        self._json(HTTPStatus.OK, record)

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-5s  %(message)s")
    parser = argparse.ArgumentParser(description="Adaptive UDP Streaming local test server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    logger.info("Serving on http://%s:%d", args.host, args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
