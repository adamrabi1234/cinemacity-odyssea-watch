import json
import os
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from nacl.signing import SigningKey

from src.serve_data import DataHandler, read_json


class ServeDataTests(unittest.TestCase):
    def test_read_json_returns_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latest.json"
            path.write_text(
                '{"checked_at":"now","matching_showings_count":3}',
                encoding="utf-8",
            )
            self.assertEqual(read_json(path)["matching_showings_count"], 3)

    def test_read_json_rejects_invalid_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latest.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                read_json(path)

    def test_discord_ping_requires_and_accepts_valid_signature(self):
        signing_key = SigningKey.generate()
        timestamp = str(int(time.time()))
        body = b'{"type":1}'
        signature = signing_key.sign(timestamp.encode() + body).signature.hex()
        environment = {
            "DISCORD_APPLICATION_ID": "123456789012345678",
            "DISCORD_PUBLIC_KEY": signing_key.verify_key.encode().hex(),
            "DISCORD_ALLOWED_GUILD_ID": "234567890123456789",
            "DISCORD_ALLOWED_USER_IDS": "345678901234567890",
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), DataHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ, environment, clear=True):
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request(
                    "POST",
                    "/discord/interactions",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": str(len(body)),
                        "X-Signature-Timestamp": timestamp,
                        "X-Signature-Ed25519": signature,
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {"type": 1})

                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request(
                    "POST",
                    "/discord/interactions",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": str(len(body)),
                        "X-Signature-Timestamp": timestamp,
                        "X-Signature-Ed25519": "00" * 64,
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
