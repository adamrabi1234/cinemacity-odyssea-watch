#!/usr/bin/env python3
"""Calculate a considerate polling interval around Cinema City's release window."""

from __future__ import annotations

import os
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


PRAGUE_TZ = ZoneInfo("Europe/Prague")


def fixed_interval_from_environment() -> int | None:
    raw = os.environ.get("WATCH_FIXED_INTERVAL_SECONDS", "").strip()
    if not raw:
        return None
    try:
        interval = int(raw)
    except ValueError as exc:
        raise ValueError("WATCH_FIXED_INTERVAL_SECONDS must be a positive integer.") from exc
    if interval <= 0:
        raise ValueError("WATCH_FIXED_INTERVAL_SECONDS must be greater than zero.")
    return interval


def adaptive_window(now: datetime) -> tuple[datetime, datetime, int]:
    """Return the active window start, end and interval in Prague local time."""
    date = now.date()
    midnight = datetime.combine(date, time(0, 0), tzinfo=PRAGUE_TZ)
    current_time = now.timetz().replace(tzinfo=None)
    weekday = now.weekday()

    if weekday == 0:
        if current_time < time(18, 0):
            return midnight, midnight + timedelta(hours=18), 3600
        return midnight + timedelta(hours=18), midnight + timedelta(days=1), 600

    if weekday == 1:
        if current_time < time(6, 0):
            return midnight, midnight + timedelta(hours=6), 3600
        if current_time < time(22, 0):
            return midnight + timedelta(hours=6), midnight + timedelta(hours=22), 600
        return midnight + timedelta(hours=22), midnight + timedelta(days=1), 3600

    if current_time < time(7, 0):
        return midnight, midnight + timedelta(hours=7), 3600
    if current_time < time(23, 0):
        return midnight + timedelta(hours=7), midnight + timedelta(hours=23), 1800
    return midnight + timedelta(hours=23), midnight + timedelta(days=1), 3600


def next_adaptive_check(now: datetime) -> datetime:
    """Find the next wall-clock-aligned check, always one minute after a slot."""
    local_now = now.astimezone(PRAGUE_TZ)
    cursor = local_now
    for _ in range(10):
        start, end, interval = adaptive_window(cursor)
        first = start + timedelta(minutes=1)
        if local_now < first:
            candidate = first
        else:
            elapsed = (local_now - first).total_seconds()
            steps = int(elapsed // interval) + 1
            candidate = first + timedelta(seconds=steps * interval)
        if candidate < end:
            return candidate
        cursor = end
    raise RuntimeError("Could not calculate the next adaptive check.")


def seconds_until_next_check(
    now: datetime,
    *,
    fixed_interval_seconds: int | None = None,
) -> int:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if fixed_interval_seconds is not None:
        if fixed_interval_seconds <= 0:
            raise ValueError("fixed_interval_seconds must be greater than zero")
        return fixed_interval_seconds

    local_now = now.astimezone(PRAGUE_TZ)
    next_check = next_adaptive_check(local_now)
    wait = (
        next_check.astimezone(UTC) - local_now.astimezone(UTC)
    ).total_seconds()
    return max(1, int(wait + 0.999999))


def main() -> None:
    try:
        interval = seconds_until_next_check(
            datetime.now(PRAGUE_TZ),
            fixed_interval_seconds=fixed_interval_from_environment(),
        )
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(interval)


if __name__ == "__main__":
    main()
