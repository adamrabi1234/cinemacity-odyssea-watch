#!/usr/bin/env python3
"""Create a live snapshot of matching Cinema City showings."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unicodedata
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from filelock import FileLock, Timeout


PRAGUE_TZ = ZoneInfo("Europe/Prague")
DEFAULT_CONFIG = Path("config.json")
DEFAULT_LATEST = Path("data/latest.json")
DEFAULT_HISTORY = Path("data/history.json")
DEFAULT_NOTIFICATION_STATE = Path("data/notification-state.json")
DEFAULT_CHECK_LOCK = Path("data/check.lock")


class WatchError(RuntimeError):
    """An error that must abort the run without replacing the live snapshot."""


def normalize(value: object) -> str:
    """Normalize API text for stable, case-insensitive comparisons."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.casefold().split())


def require_list(container: dict[str, Any], key: str, context: str) -> list[Any]:
    value = container.get(key)
    if not isinstance(value, list):
        raise WatchError(f"Cinema City API response for {context} has no list '{key}'.")
    return value


class ApiClient:
    """Small validating client for Cinema City's public data API."""

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.request_count = 0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "cinemacity-odyssea-watch/1.0 (+self-hosted watcher)",
            }
        )

    def get_body(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = self.session.get(url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise WatchError(f"Cinema City API request failed: {url}: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise WatchError(f"Cinema City API returned invalid JSON: {response.url}") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("body"), dict):
            raise WatchError(f"Cinema City API response has no object 'body': {response.url}")

        self.request_count += 1
        return payload["body"]


def load_json(path: Path, *, optional: bool = False) -> dict[str, Any] | None:
    if optional and not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WatchError(f"Required file does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise WatchError(f"Cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WatchError(f"JSON root must be an object: {path}")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Replace a JSON file only after the complete new document is on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    except OSError as exc:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise WatchError(f"Cannot atomically write {path}: {exc}") from exc


def format_discord_line(showing: dict[str, Any]) -> str:
    try:
        showing_time = datetime.fromisoformat(str(showing["datetime"])).astimezone(PRAGUE_TZ)
    except (KeyError, TypeError, ValueError) as exc:
        raise WatchError("A new showing has no valid datetime for notification.") from exc
    booking_url = str(showing.get("booking_url") or "").strip()
    if not booking_url.startswith("https://"):
        raise WatchError("A new showing has no safe HTTPS booking URL.")
    auditorium = str(showing.get("auditorium") or "IMAX")
    return (
        f"**{showing_time:%d.%m.%Y %H:%M}** · {auditorium} · "
        f"[Rezervovat]({booking_url})"
    )


def validate_discord_webhook_url(webhook_url: str) -> None:
    """Reject accidental or malicious non-Discord notification destinations."""
    parsed = urlsplit(webhook_url)
    allowed_hosts = {
        "discord.com",
        "discordapp.com",
        "ptb.discord.com",
        "canary.discord.com",
    }
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/api/webhooks/")
    ):
        raise WatchError("DISCORD_WEBHOOK_URL is not a valid Discord HTTPS webhook URL.")


def post_discord_payloads(
    webhook_url: str,
    payloads: list[dict[str, Any]],
    *,
    session: requests.Session | None = None,
) -> None:
    """Send already bounded payloads to one validated Discord webhook."""
    validate_discord_webhook_url(webhook_url)
    client = session or requests.Session()
    try:
        for payload in payloads:
            response = client.post(webhook_url, json=payload, timeout=15)
            response.raise_for_status()
    except requests.RequestException as exc:
        raise WatchError(f"Discord notification failed: {exc}") from exc


def empty_notification_state(at: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "initialized_at": at,
        "updated_at": at,
        "showings_initialized": False,
        "notified_event_ids": [],
    }


def notify_watcher_status(
    state_path: Path,
    webhook_url: str,
    *,
    error: str | None,
    checked_at: str | None = None,
    session: requests.Session | None = None,
) -> str:
    """Send one failure transition and one recovery, suppressing repeated errors."""
    validate_discord_webhook_url(webhook_url)
    timestamp = checked_at or datetime.now(PRAGUE_TZ).replace(microsecond=0).isoformat()
    state = load_json(state_path, optional=True) or empty_notification_state(timestamp)
    state["schema_version"] = 2
    previous_status = str(state.get("watcher_status") or "unknown")
    failure_alert_sent = bool(state.get("failure_alert_sent"))

    if error is not None:
        safe_error = " ".join(str(error).split()).replace("`", "'")[:1000]
        state["watcher_status"] = "error"
        state["last_error_at"] = timestamp
        state["last_error_message"] = safe_error
        state["updated_at"] = timestamp
        if previous_status == "error" and failure_alert_sent:
            atomic_write_json(state_path, state)
            return "failure_suppressed"

        payload = {
            "username": "Cinema City watcher",
            "content": "🚨 **Cinema City watcher: kontrola selhala**",
            "embeds": [
                {
                    "description": (
                        f"Čas: **{timestamp}**\n"
                        f"Chyba: `{safe_error}`\n\n"
                        "Poslední platná data zůstávají dostupná. Další kontrola "
                        "proběhne automaticky."
                    ),
                    "color": 0xE21C2A,
                }
            ],
            "allowed_mentions": {"parse": []},
        }
        try:
            post_discord_payloads(webhook_url, [payload], session=session)
        except WatchError:
            state["failure_alert_sent"] = False
            atomic_write_json(state_path, state)
            raise
        state["failure_alert_sent"] = True
        atomic_write_json(state_path, state)
        return "failure_sent"

    if previous_status == "error" and failure_alert_sent:
        payload = {
            "username": "Cinema City watcher",
            "content": "✅ **Cinema City watcher opět funguje**",
            "embeds": [
                {
                    "description": (
                        f"Živá kontrola Cinema City byla úspěšná v **{timestamp}**."
                    ),
                    "color": 0x2ECC71,
                }
            ],
            "allowed_mentions": {"parse": []},
        }
        post_discord_payloads(webhook_url, [payload], session=session)
        outcome = "recovery_sent"
    else:
        outcome = "status_current"

    state["watcher_status"] = "ok"
    state["failure_alert_sent"] = False
    state["last_success_at"] = timestamp
    state["updated_at"] = timestamp
    atomic_write_json(state_path, state)
    return outcome


def discord_payloads(showings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build payloads below Discord's documented message/embed limits."""
    lines = [format_discord_line(showing) for showing in showings]
    descriptions: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in lines:
        line_length = len(line) + (1 if current else 0)
        if current and current_length + line_length > 3800:
            descriptions.append("\n".join(current))
            current = []
            current_length = 0
        current.append(line)
        current_length += len(line) + (1 if current_length else 0)
    if current:
        descriptions.append("\n".join(current))

    payloads: list[dict[str, Any]] = []
    for index, description in enumerate(descriptions):
        part = (
            ""
            if len(descriptions) == 1
            else f" — část {index + 1}/{len(descriptions)}"
        )
        payloads.append(
            {
                "username": "Cinema City watcher",
                "content": (
                    f"🎬 **Nové termíny Odyssea IMAX 70 mm ({len(showings)})**{part}"
                ),
                "embeds": [
                    {
                        "description": description,
                        "color": 0xE21C2A,
                    }
                ],
                "allowed_mentions": {"parse": []},
            }
        )
    return payloads


def notify_discord(
    snapshot: dict[str, Any],
    state_path: Path,
    webhook_url: str,
    *,
    session: requests.Session | None = None,
) -> int:
    """Notify about event IDs not yet recorded in persistent notification state."""
    validate_discord_webhook_url(webhook_url)
    showings = {
        str(item.get("event_id")): item
        for item in snapshot.get("showings", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    state = load_json(state_path, optional=True)
    checked_at = str(snapshot.get("checked_at") or datetime.now(PRAGUE_TZ).isoformat())

    if state is None:
        state = empty_notification_state(checked_at)

    if not bool(state.get("showings_initialized", True)):
        state["schema_version"] = 2
        state["showings_initialized"] = True
        state["notified_event_ids"] = sorted(showings)
        state["updated_at"] = checked_at
        atomic_write_json(state_path, state)
        return 0

    notified = state.get("notified_event_ids")
    if not isinstance(notified, list):
        raise WatchError("Notification state has no valid notified_event_ids list.")
    known_ids = {str(event_id) for event_id in notified}
    new_showings = [showing for event_id, showing in showings.items() if event_id not in known_ids]
    new_showings.sort(key=lambda showing: str(showing.get("datetime") or ""))
    if not new_showings:
        return 0

    post_discord_payloads(
        webhook_url,
        discord_payloads(new_showings),
        session=session,
    )

    state["schema_version"] = 2
    state["showings_initialized"] = True
    state["notified_event_ids"] = sorted(known_ids | set(showings))
    state["updated_at"] = checked_at
    atomic_write_json(state_path, state)
    return len(new_showings)


def select_cinema(
    cinemas: list[dict[str, Any]], name_fragment: str, fallback_id: str | None
) -> dict[str, Any]:
    wanted = normalize(name_fragment)
    matches = [
        cinema
        for cinema in cinemas
        if wanted in normalize(cinema.get("displayName")) and cinema.get("id")
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(str(item.get("displayName")) for item in matches)
        raise WatchError(f"Cinema name '{name_fragment}' is ambiguous: {names}")
    if fallback_id:
        return {"id": str(fallback_id), "displayName": name_fragment}
    raise WatchError(f"Cinema '{name_fragment}' was not found in the live API response.")


def is_imax_event(event: dict[str, Any]) -> bool:
    searchable = [event.get("auditorium"), event.get("auditoriumTinyName")]
    searchable.extend(event.get("attributeIds") or [])
    return any("imax" in normalize(value) for value in searchable)


def matches_target(
    event: dict[str, Any], film: dict[str, Any], config: dict[str, Any]
) -> bool:
    if normalize(film.get("name")) != normalize(config["film_name"]):
        return False
    event_attributes = {normalize(value) for value in event.get("attributeIds") or []}
    required = {normalize(value) for value in config.get("required_attributes", [])}
    if not required.issubset(event_attributes):
        return False
    if config.get("require_imax", False) and not is_imax_event(event):
        return False
    return True


def parse_event_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise WatchError(f"Invalid eventDateTime in Cinema City API: {value!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=PRAGUE_TZ)
    return parsed.astimezone(PRAGUE_TZ)


def compact_film(film: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": film.get("id"),
        "name": film.get("name"),
        "length_minutes": film.get("length"),
        "link": film.get("link"),
        "poster_link": film.get("posterLink"),
        "release_year": film.get("releaseYear"),
        "release_date": film.get("releaseDate"),
        "attributes": film.get("attributeIds") or [],
    }


def build_showing(
    event: dict[str, Any], film: dict[str, Any], cinema: dict[str, Any]
) -> dict[str, Any]:
    local_datetime = parse_event_datetime(event.get("eventDateTime"))
    event_id = str(event.get("id") or "")
    if not event_id:
        raise WatchError("Cinema City event is missing its ID.")
    presentation_code = str(event.get("presentationCode") or event_id)
    attributes = event.get("attributeIds") or []
    return {
        "event_id": event_id,
        "presentation_code": presentation_code,
        "datetime": local_datetime.isoformat(timespec="seconds"),
        "event_datetime_api": event.get("eventDateTime"),
        "date": local_datetime.date().isoformat(),
        "time": local_datetime.strftime("%H:%M"),
        "business_day": event.get("businessDay"),
        "cinema": {
            "id": str(event.get("cinemaId") or cinema.get("id")),
            "name": cinema.get("displayName"),
        },
        "auditorium": event.get("auditorium"),
        "auditorium_tiny_name": event.get("auditoriumTinyName"),
        "film": compact_film(film),
        "attributes": attributes,
        "is_70_mm": "70-mm" in {normalize(value) for value in attributes},
        "is_imax": is_imax_event(event),
        "languages": event.get("languages"),
        "sold_out": event.get("soldOut") if "soldOut" in event else None,
        "availability_ratio": event.get("availabilityRatio"),
        "booking_url": f"https://tickets.cinemacity.cz/order/{presentation_code}",
        "api_booking_links": {
            "booking_link": event.get("bookingLink"),
            "booking_router_launch_link": event.get("bookingRouterLaunchLink"),
            "composite_booking_link": event.get("compositeBookingLink"),
        },
    }


def latest_from_showings(showings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not showings:
        return None
    return max(showings, key=lambda showing: showing["datetime"])


def collect_live_snapshot(
    config: dict[str, Any], client: ApiClient, started_at: datetime | None = None
) -> dict[str, Any]:
    started_at = started_at or datetime.now(PRAGUE_TZ)
    until = str(config.get("until") or "9999-12-31")
    language = str(config.get("language") or "cs_CZ")
    required_attributes = list(config.get("required_attributes") or [])
    hint_attribute = str(required_attributes[0]) if required_attributes else ""
    common = {"lang": language}

    cinema_body = client.get_body(
        f"cinemas/with-event/until/{until}", {**common, "attr": ""}
    )
    cinemas = require_list(cinema_body, "cinemas", "cinema discovery")
    typed_cinemas = [item for item in cinemas if isinstance(item, dict)]
    cinema = select_cinema(
        typed_cinemas,
        str(config["cinema_name_contains"]),
        str(config.get("cinema_id_fallback") or "") or None,
    )
    cinema_id = str(cinema["id"])

    dates_body = client.get_body(
        f"dates/in-cinema/{cinema_id}/until/{until}",
        {**common, "attr": hint_attribute},
    )
    dates = require_list(dates_body, "dates", "date discovery")
    if any(not isinstance(day, str) for day in dates):
        raise WatchError("Cinema City date discovery returned a non-string date.")

    found: dict[str, dict[str, Any]] = {}
    matching_film_ids: set[str] = set()
    for day in dates:
        day_body = client.get_body(
            f"film-events/in-cinema/{cinema_id}/at-date/{day}",
            {**common, "attr": hint_attribute},
        )
        films = require_list(day_body, "films", f"film list for {day}")
        events = require_list(day_body, "events", f"event list for {day}")
        film_map = {
            str(film["id"]): film
            for film in films
            if isinstance(film, dict) and film.get("id")
        }
        for event in events:
            if not isinstance(event, dict):
                continue
            film = film_map.get(str(event.get("filmId") or ""))
            if not film or not matches_target(event, film, config):
                continue
            showing = build_showing(event, film, cinema)
            if parse_event_datetime(event.get("eventDateTime")) < started_at:
                continue
            matching_film_ids.add(str(film["id"]))
            found[showing["event_id"]] = showing

    showings = sorted(found.values(), key=lambda showing: showing["datetime"])
    completed_local = datetime.now(PRAGUE_TZ).replace(microsecond=0)
    completed_utc = completed_local.astimezone(UTC)
    return {
        "schema_version": 1,
        "checked_at": completed_local.isoformat(),
        "checked_at_utc": completed_utc.isoformat().replace("+00:00", "Z"),
        "source": {
            "name": "Cinema City CZ public JSON API",
            "api_base": client.base_url,
            "live_requests_completed": client.request_count,
            "until": until,
            "dates_returned": dates,
        },
        "query": {
            "film_name": config["film_name"],
            "cinema_name_contains": config["cinema_name_contains"],
            "required_attributes": required_attributes,
            "require_imax": bool(config.get("require_imax")),
        },
        "resolved": {
            "cinema_id": cinema_id,
            "cinema_name": cinema.get("displayName"),
            "film_ids": sorted(matching_film_ids),
        },
        "matching_showings_count": len(showings),
        "latest_showing": deepcopy(latest_from_showings(showings)),
        "showings": showings,
    }


def showing_summary(showing: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": showing.get("event_id"),
        "datetime": showing.get("datetime"),
        "auditorium": showing.get("auditorium"),
        "sold_out": showing.get("sold_out"),
    }


def latest_marker(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot or not isinstance(snapshot.get("latest_showing"), dict):
        return None
    latest = snapshot["latest_showing"]
    return {"event_id": latest.get("event_id"), "datetime": latest.get("datetime")}


def update_history(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    existing: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    checked_at = str(current["checked_at"])
    history = deepcopy(existing) if existing else {
        "schema_version": 1,
        "created_at": checked_at,
        "updated_at": checked_at,
        "events": {},
        "changes": [],
    }
    events_history = history.setdefault("events", {})
    changes = history.setdefault("changes", [])
    if not isinstance(events_history, dict) or not isinstance(changes, list):
        raise WatchError("data/history.json has an unsupported structure.")

    previous_showings = {
        str(item.get("event_id")): item
        for item in (previous or {}).get("showings", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    current_showings = {
        str(item.get("event_id")): item
        for item in current.get("showings", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    added = sorted(set(current_showings) - set(previous_showings))
    removed = sorted(set(previous_showings) - set(current_showings))
    previous_latest = latest_marker(previous)
    current_latest = latest_marker(current)
    latest_changed = previous_latest != current_latest
    changed = bool(added or removed or latest_changed)

    for event_id in added:
        record = events_history.get(event_id)
        if not isinstance(record, dict):
            record = {
                "first_seen_at": checked_at,
                "last_known_showing": showing_summary(current_showings[event_id]),
            }
            events_history[event_id] = record
        record["active"] = True
        record["disappeared_at"] = None
        record["last_known_showing"] = showing_summary(current_showings[event_id])

    for event_id in removed:
        record = events_history.get(event_id)
        if not isinstance(record, dict):
            record = {
                "first_seen_at": (previous or {}).get("checked_at"),
                "last_known_showing": showing_summary(previous_showings[event_id]),
            }
            events_history[event_id] = record
        record["active"] = False
        record["disappeared_at"] = checked_at

    if changed:
        changes.append(
            {
                "detected_at": checked_at,
                "added_event_ids": added,
                "removed_event_ids": removed,
                "previous_latest_showing": previous_latest,
                "current_latest_showing": current_latest,
            }
        )
        history["updated_at"] = checked_at
    return history, changed or existing is None


def validate_config(config: dict[str, Any]) -> None:
    for key in ("api_base", "film_name", "cinema_name_contains"):
        if not config.get(key):
            raise WatchError(f"Configuration key '{key}' is required.")
    if not isinstance(config.get("required_attributes", []), list):
        raise WatchError("Configuration key 'required_attributes' must be a list.")


def run(config_path: Path, latest_path: Path, history_path: Path) -> dict[str, Any]:
    config = load_json(config_path)
    assert config is not None
    validate_config(config)
    previous = load_json(latest_path, optional=True)
    existing_history = load_json(history_path, optional=True)
    client = ApiClient(
        str(config["api_base"]), float(config.get("timeout_seconds", 25))
    )
    snapshot = collect_live_snapshot(config, client)
    history, history_changed = update_history(previous, snapshot, existing_history)

    # No API-derived file is touched until all live requests and validation finish.
    if history_changed:
        atomic_write_json(history_path, history)
    atomic_write_json(latest_path, snapshot)
    return snapshot


def run_main_cycle(args: argparse.Namespace) -> int:
    """Run one complete watcher cycle while the caller holds the process lock."""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    attempted_at = datetime.now(PRAGUE_TZ).replace(microsecond=0).isoformat()
    try:
        snapshot = run(args.config, args.latest, args.history)
    except (WatchError, KeyError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if webhook_url:
            try:
                outcome = notify_watcher_status(
                    args.notification_state,
                    webhook_url,
                    error=str(exc),
                    checked_at=attempted_at,
                )
            except WatchError as notification_error:
                print(f"ERROR: {notification_error}", file=sys.stderr)
            else:
                if outcome == "failure_sent":
                    print("Discord failure notification sent.")
                else:
                    print("Repeated watcher failure suppressed from Discord.")
        return 1

    latest = snapshot.get("latest_showing")
    print(
        f"Live API check succeeded at {snapshot['checked_at']}; "
        f"found {snapshot['matching_showings_count']} matching future showings."
    )
    if latest:
        print(
            "Latest: "
            f"{latest['datetime']} | {latest.get('auditorium')} | "
            f"event {latest['event_id']} | sold_out={latest.get('sold_out')}"
        )
    else:
        print("Latest: none")

    if webhook_url:
        try:
            status_outcome = notify_watcher_status(
                args.notification_state,
                webhook_url,
                error=None,
                checked_at=str(snapshot["checked_at"]),
            )
            notification_count = notify_discord(
                snapshot,
                args.notification_state,
                webhook_url,
            )
        except WatchError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if status_outcome == "recovery_sent":
            print("Discord recovery notification sent.")
        if notification_count:
            print(f"Discord notification sent for {notification_count} new showings.")
        elif args.notification_state.exists():
            print("Discord notification state is current; no new showings.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--latest", type=Path, default=DEFAULT_LATEST)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument(
        "--notification-state", type=Path, default=DEFAULT_NOTIFICATION_STATE
    )
    parser.add_argument("--check-lock", type=Path, default=DEFAULT_CHECK_LOCK)
    args = parser.parse_args(argv)

    args.check_lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(args.check_lock), timeout=0):
            return run_main_cycle(args)
    except Timeout:
        print("Watcher check skipped because another check is already running.")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
