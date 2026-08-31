#!/usr/bin/env python3
"""Serve the current watcher snapshot over a small read-only HTTP API."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.discord_interactions import (
    INTERACTION_PATH,
    MAX_REQUEST_BYTES,
    CommandLimiter,
    DiscordInteractionError,
    authorize_command,
    complete_command,
    ephemeral_message,
    interaction_url,
    load_discord_config,
    verify_request_signature,
)


DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
PUBLIC_FILES = {
    "/latest.json": DATA_DIR / "latest.json",
    "/history.json": DATA_DIR / "history.json",
}
COMMAND_LIMITER = CommandLimiter()


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
                "discord_interactions": INTERACTION_PATH,
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

    def do_POST(self) -> None:
        if urlsplit(self.path).path != INTERACTION_PATH:
            self._send_json(404, {"error": "not_found"})
            return

        try:
            config = load_discord_config()
        except DiscordInteractionError as exc:
            self._send_json(503, {"error": "discord_configuration_invalid"})
            print(f"ERROR: {exc}", flush=True)
            return
        if config is None:
            self._send_json(503, {"error": "discord_commands_disabled"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = -1
        if not 0 < content_length <= MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "invalid_request_size"})
            return
        body = self.rfile.read(content_length)
        try:
            verify_request_signature(
                config.public_key,
                self.headers.get("X-Signature-Timestamp", ""),
                self.headers.get("X-Signature-Ed25519", ""),
                body,
            )
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise DiscordInteractionError("Discord request body must be an object.")
        except (DiscordInteractionError, json.JSONDecodeError):
            self._send_json(401, {"error": "invalid_discord_request"})
            return

        interaction_type = payload.get("type")
        if interaction_type == 1:
            self._send_json(200, {"type": 1})
            return
        if interaction_type != 2:
            self._send_json(400, {"error": "unsupported_interaction_type"})
            return

        denial = authorize_command(payload, config)
        if denial:
            self._send_json(200, ephemeral_message(denial))
            return

        command_name = str(payload["data"]["name"])

        reserved, reason, remaining = COMMAND_LIMITER.reserve()
        if not reserved:
            if reason == "running":
                message = "⏳ Kontrola už probíhá. Počkej prosím na její výsledek."
            else:
                message = f"⏱️ Zkus příkaz znovu přibližně za {remaining} sekund."
            self._send_json(200, ephemeral_message(message))
            return

        token = str(payload.get("token") or "")
        try:
            # Validate before acknowledging so malformed data never reaches a worker.
            interaction_url(config, token, original=True)
        except DiscordInteractionError:
            COMMAND_LIMITER.finish()
            self._send_json(401, {"error": "invalid_interaction_token"})
            return

        self._send_json(
            200,
            {
                "type": 5,
                "data": {"flags": 1 << 6},
            },
        )
        threading.Thread(
            target=complete_command,
            args=(config, token, COMMAND_LIMITER, command_name),
            name=f"discord-{command_name}",
            daemon=True,
        ).start()


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
