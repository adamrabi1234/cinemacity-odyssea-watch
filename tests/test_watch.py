import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from filelock import FileLock

from src.watch import (
    WatchError,
    latest_from_showings,
    main,
    matches_target,
    run,
    select_cinema,
)


FIXTURE = Path(__file__).parent / "fixtures" / "film_events.json"


class FilteringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.films = {film["id"]: film for film in cls.payload["films"]}
        cls.events = {event["id"]: event for event in cls.payload["events"]}
        cls.config = {
            "film_name": "Odyssea",
            "required_attributes": ["70-mm"],
            "require_imax": True,
        }

    def test_correct_film_70mm_and_imax_matches(self):
        event = self.events["correct"]
        self.assertTrue(matches_target(event, self.films[event["filmId"]], self.config))

    def test_wrong_film_does_not_match(self):
        event = self.events["wrong-film-event"]
        self.assertFalse(matches_target(event, self.films[event["filmId"]], self.config))

    def test_non_70mm_showing_does_not_match(self):
        event = self.events["not-70mm"]
        self.assertFalse(matches_target(event, self.films[event["filmId"]], self.config))

    def test_non_imax_auditorium_does_not_match(self):
        event = self.events["not-imax"]
        self.assertFalse(matches_target(event, self.films[event["filmId"]], self.config))

    def test_latest_showing_is_chronologically_last(self):
        showings = [
            {"event_id": "two", "datetime": "2026-09-18T20:30:00+02:00"},
            {"event_id": "one", "datetime": "2026-09-01T09:00:00+02:00"},
            {"event_id": "three", "datetime": "2026-10-01T12:00:00+02:00"},
        ]
        self.assertEqual(latest_from_showings(showings)["event_id"], "three")

    def test_empty_showing_list_has_no_latest(self):
        self.assertIsNone(latest_from_showings([]))

    def test_flora_is_resolved_from_live_style_cinema_list(self):
        cinemas = [
            {"id": "1030", "displayName": "Praha Letňany, OC Letňany"},
            {"id": "1052", "displayName": "Praha Flora, OC FLORA"},
        ]
        self.assertEqual(select_cinema(cinemas, "Praha Flora", None)["id"], "1052")

    def test_api_failure_does_not_replace_last_good_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            latest = root / "latest.json"
            history = root / "history.json"
            config.write_text(
                json.dumps(
                    {
                        "api_base": "https://example.invalid/api",
                        "film_name": "Odyssea",
                        "cinema_name_contains": "Praha Flora",
                        "required_attributes": ["70-mm"],
                    }
                ),
                encoding="utf-8",
            )
            original = '{"checked_at":"last-good"}\n'
            latest.write_text(original, encoding="utf-8")

            with patch(
                "src.watch.collect_live_snapshot",
                side_effect=WatchError("simulated API failure"),
            ):
                with self.assertRaises(WatchError):
                    run(config, latest, history)

            self.assertEqual(latest.read_text(encoding="utf-8"), original)
            self.assertFalse(history.exists())

    def test_second_process_skips_when_check_lock_is_held(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "check.lock"
            with FileLock(str(lock_path), timeout=0):
                result = main(["--check-lock", str(lock_path)])
            self.assertEqual(result, 3)


if __name__ == "__main__":
    unittest.main()
