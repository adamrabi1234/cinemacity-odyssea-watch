import os
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src.schedule import (
    PRAGUE_TZ,
    atomic_write_state,
    fixed_interval_from_environment,
    schedule_state,
    seconds_until_next_check,
)


def prague_datetime(weekday_date: str, hour: int, minute: int = 0) -> datetime:
    return datetime.fromisoformat(f"{weekday_date}T{hour:02d}:{minute:02d}:00").replace(
        tzinfo=PRAGUE_TZ
    )


class ScheduleTests(unittest.TestCase):
    def test_monday_evening_uses_ten_minutes(self):
        self.assertEqual(seconds_until_next_check(prague_datetime("2026-08-31", 18, 1)), 600)

    def test_monday_daytime_wakes_at_release_window_boundary(self):
        self.assertEqual(
            seconds_until_next_check(prague_datetime("2026-08-31", 17, 30)),
            1860,
        )

    def test_tuesday_morning_uses_ten_minutes(self):
        self.assertEqual(seconds_until_next_check(prague_datetime("2026-09-01", 6, 1)), 600)

    def test_tuesday_night_wakes_at_morning_release_window(self):
        self.assertEqual(
            seconds_until_next_check(prague_datetime("2026-09-01", 5, 50)),
            660,
        )

    def test_tuesday_afternoon_uses_ten_minutes(self):
        self.assertEqual(seconds_until_next_check(prague_datetime("2026-09-01", 14, 1)), 600)

    def test_regular_daytime_uses_thirty_minutes(self):
        self.assertEqual(seconds_until_next_check(prague_datetime("2026-09-02", 12, 1)), 1800)

    def test_outside_release_window_uses_one_hour(self):
        self.assertEqual(seconds_until_next_check(prague_datetime("2026-09-02", 23, 1)), 3600)

    def test_outside_window_check_is_one_minute_after_the_hour(self):
        self.assertEqual(
            seconds_until_next_check(prague_datetime("2026-09-02", 23, 30)),
            1860,
        )

    def test_monday_daytime_outside_window_uses_one_hour(self):
        self.assertEqual(seconds_until_next_check(prague_datetime("2026-08-31", 12, 1)), 3600)

    def test_tuesday_after_release_window_uses_one_hour(self):
        self.assertEqual(seconds_until_next_check(prague_datetime("2026-09-01", 22, 1)), 3600)

    def test_fixed_interval_override(self):
        self.assertEqual(
            seconds_until_next_check(
                prague_datetime("2026-09-02", 12),
                fixed_interval_seconds=123,
            ),
            123,
        )

    def test_blank_environment_uses_adaptive_schedule(self):
        with patch.dict(os.environ, {"WATCH_FIXED_INTERVAL_SECONDS": ""}):
            self.assertIsNone(fixed_interval_from_environment())

    def test_invalid_environment_is_rejected(self):
        with patch.dict(os.environ, {"WATCH_FIXED_INTERVAL_SECONDS": "often"}):
            with self.assertRaises(ValueError):
                fixed_interval_from_environment()

    def test_schedule_state_contains_nominal_interval_and_exact_next_check(self):
        state = schedule_state(prague_datetime("2026-09-01", 18, 54))
        self.assertEqual(state["interval_seconds"], 600)
        self.assertEqual(state["sleep_seconds"], 420)
        self.assertEqual(state["next_check_at"], "2026-09-01T19:01:00+02:00")

    def test_schedule_state_is_written_as_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            state = schedule_state(prague_datetime("2026-09-02", 12, 1))
            atomic_write_state(path, state)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), state)


if __name__ == "__main__":
    unittest.main()
