#!/usr/bin/env python3
"""Calculate a considerate polling interval around Cinema City's release window."""

from __future__ import annotations

import math
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


PRAGUE_TZ = ZoneInfo("Europe/Prague")


def fixed_interval_from_environment() -> int | None:
    raw = os.environ.get("WATCH_INTERVAL_SECONDS", "").strip()
    if not raw:
        return None
    try:
        interval = int(raw)
    except ValueError as exc:
        raise ValueError("WATCH_INTERVAL_SECONDS must be a positive integer.") from exc
    if interval <= 0:
        raise ValueError("WATCH_INTERVAL_SECONDS must be greater than zero.")
    return interval


def adaptive_interval_seconds(now: datetime) -> int:
    """Return the interval for the current Europe/Prague release window."""
    weekday = now.weekday()
    current_time = now.timetz().replace(tzinfo=None)

    if weekday == 0 and current_time >= time(18, 0):
        return 10 * 60
    if weekday == 1 and time(6, 0) <= current_time < time(14, 0):
        return 10 * 60
    if weekday == 1 and time(14, 0) <= current_time < time(22, 0):
        return 30 * 60
    if time(7, 0) <= current_time < time(23, 0):
        return 60 * 60
    return 4 * 60 * 60


def schedule_boundaries(now: datetime) -> list[datetime]:
    """List upcoming times at which the adaptive interval may change."""
    boundaries: list[datetime] = []
    for days_ahead in range(8):
        date = (now + timedelta(days=days_ahead)).date()
        times = {time(7, 0), time(23, 0)}
        if date.weekday() == 0:
            times.add(time(18, 0))
        if date.weekday() == 1:
            times.update({time(6, 0), time(14, 0), time(22, 0)})
        for boundary_time in times:
            boundary = datetime.combine(date, boundary_time, tzinfo=PRAGUE_TZ)
            if boundary > now:
                boundaries.append(boundary)
    return sorted(boundaries)


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
    interval = adaptive_interval_seconds(local_now)
    boundaries = schedule_boundaries(local_now)
    if boundaries:
        seconds_to_boundary = math.ceil((boundaries[0] - local_now).total_seconds())
        interval = min(interval, seconds_to_boundary)
    return max(1, interval)


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
