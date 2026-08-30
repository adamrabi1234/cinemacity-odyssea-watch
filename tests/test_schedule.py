import os
import unittest
from datetime import datetime
from unittest.mock import patch

from src.schedule import PRAGUE_TZ, fixed_interval_from_environment, seconds_until_next_check


def prague_datetime(weekday_date: str, hour: int, minute: int = 0) -> datetime:
    return datetime.fromisoformat(f"{weekday_date}T{hour:02d}:{minute:02d}:00").replace(
        tzinfo=PRAGUE_TZ
    )


class ScheduleTests(unittest.TestCase):
    def test_monday_evening_uses_ten_minutes(self):
        self.assertEqual(seconds_until_next_check(prague_datetime("2026-08-31", 18)), 600)

    def test_monday_daytime_wakes_at_release_window_boundary(self):
        self.assertEqual(
            seconds_until_next_check(prague_datetime("2026-08-31", 17, 30)),
            1800,
        )

    def test_tuesday_morning_uses_ten_minutes(self):
        self.assertEqual(seconds_until_next_check(prague_datetime("2026-09-01", 6)), 600)

    def test_tuesday_night_wakes_at_morning_release_window(self):
        self.assertEqual(
            seconds_until_next_check(prague_datetime("2026-09-01", 5, 50)),
            600,
        )

    def test_tuesday_afternoon_uses_thirty_minutes(self):
        self.assertEqual(seconds_until_next_check(prague_datetime("2026-09-01", 14)), 1800)

    def test_regular_daytime_uses_one_hour(self):
        self.assertEqual(seconds_until_next_check(prague_datetime("2026-09-02", 12)), 3600)

    def test_regular_night_uses_four_hours(self):
        self.assertEqual(seconds_until_next_check(prague_datetime("2026-09-02", 23)), 14400)

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


if __name__ == "__main__":
    unittest.main()
