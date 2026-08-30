#!/usr/bin/env python3
"""Serve the current watcher snapshot over a small read-only HTTP API."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
PUBLIC_FILES = {
    "/latest.json": DATA_DIR / "latest.json",
    "/history.json": DATA_DIR / "history.json",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class DataHandler(BaseHTTPRequestHandler):
    server_version = "CinemaCityWatch/1.0"

    def _send_json(self, status: int, payload: Any, *, head_only: bool = False) -> None:
        body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _document_for_path(self, path: str) -> tuple[int, Any]:
        if path == "/healthz":
            try:
                latest = read_json(PUBLIC_FILES["/latest.json"])
            except (OSError, json.JSONDecodeError) as exc:
                return 503, {"status": "unhealthy", "error": str(exc)}
            return 200, {
                "status": "ok",
                "checked_at": latest.get("checked_at"),
                "matching_showings_count": latest.get("matching_showings_count"),
            }

        if path == "/":
            try:
                latest = read_json(PUBLIC_FILES["/latest.json"])
            except (OSError, json.JSONDecodeError):
                latest = {}
            return 200, {
                "service": "Cinema City Odyssea watch",
                "latest": "/latest.json",
                "history": "/history.json",
                "health": "/healthz",
                "checked_at": latest.get("checked_at"),
                "matching_showings_count": latest.get("matching_showings_count"),
            }

        document = PUBLIC_FILES.get(path)
        if document is None:
            return 404, {"error": "not_found"}
        try:
            return 200, read_json(document)
        except FileNotFoundError:
            return 503, {"error": "snapshot_not_ready"}
        except (OSError, json.JSONDecodeError) as exc:
            return 503, {"error": "snapshot_unavailable", "detail": str(exc)}

    def do_GET(self) -> None:
        status, document = self._document_for_path(urlsplit(self.path).path)
        self._send_json(status, document)

    def do_HEAD(self) -> None:
        status, document = self._document_for_path(urlsplit(self.path).path)
        self._send_json(status, document, head_only=True)


def main() -> None:
    try:
        port = int(os.environ.get("PORT", "8000"))
    except ValueError as exc:
        raise SystemExit("PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be between 1 and 65535.")

    server = ThreadingHTTPServer(("0.0.0.0", port), DataHandler)
    print(f"Serving watcher data on 0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
