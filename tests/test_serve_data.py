import json
import tempfile
import unittest
from pathlib import Path

from src.serve_data import read_json


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


if __name__ == "__main__":
    unittest.main()
