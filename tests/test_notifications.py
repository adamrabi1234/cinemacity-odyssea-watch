import json
import tempfile
import unittest
from pathlib import Path

import requests

from src.watch import WatchError, notify_discord, notify_watcher_status


WEBHOOK_URL = "https://discord.com/api/webhooks/123456/test-token"


def showing(event_id: str, hour: str) -> dict:
    return {
        "event_id": event_id,
        "datetime": f"2026-09-08T{hour}:00+02:00",
        "auditorium": "IMAX VOLVO",
        "booking_url": f"https://tickets.cinemacity.cz/order/{event_id}",
    }


def snapshot(*showings: dict) -> dict:
    return {
        "checked_at": "2026-09-01T06:01:00+02:00",
        "showings": list(showings),
    }


class FakeResponse:
    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return FakeResponse()


class DiscordNotificationTests(unittest.TestCase):
    def test_first_run_creates_baseline_without_sending(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            session = FakeSession()
            count = notify_discord(
                snapshot(showing("one", "16:40")),
                state_path,
                WEBHOOK_URL,
                session=session,
            )
            self.assertEqual(count, 0)
            self.assertEqual(session.calls, [])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["notified_event_ids"], ["one"])

    def test_new_showings_are_sent_with_booking_links(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps({"notified_event_ids": ["one"]}), encoding="utf-8"
            )
            session = FakeSession()
            count = notify_discord(
                snapshot(showing("one", "16:40"), showing("two", "20:30")),
                state_path,
                WEBHOOK_URL,
                session=session,
            )
            self.assertEqual(count, 1)
            self.assertEqual(len(session.calls), 1)
            payload = session.calls[0][1]["json"]
            self.assertIn("08.09.2026 20:30", payload["embeds"][0]["description"])
            self.assertIn("https://tickets.cinemacity.cz/order/two", payload["embeds"][0]["description"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["notified_event_ids"], ["one", "two"])

    def test_failed_notification_is_retried_next_time(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps({"notified_event_ids": ["one"]}), encoding="utf-8"
            )
            with self.assertRaises(WatchError):
                notify_discord(
                    snapshot(showing("one", "16:40"), showing("two", "20:30")),
                    state_path,
                    WEBHOOK_URL,
                    session=FakeSession(requests.ConnectionError("offline")),
                )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["notified_event_ids"], ["one"])

    def test_non_discord_webhook_is_rejected_without_a_request(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            session = FakeSession()
            with self.assertRaises(WatchError):
                notify_discord(
                    snapshot(showing("one", "16:40")),
                    state_path,
                    "https://example.com/api/webhooks/123/token",
                    session=session,
                )
            self.assertEqual(session.calls, [])
            self.assertFalse(state_path.exists())

    def test_failure_is_sent_once_and_recovery_is_sent_once(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            session = FakeSession()
            first = notify_watcher_status(
                state_path,
                WEBHOOK_URL,
                error="Cinema City API is offline",
                checked_at="2026-09-01T06:01:00+02:00",
                session=session,
            )
            repeated = notify_watcher_status(
                state_path,
                WEBHOOK_URL,
                error="Cinema City API is still offline",
                checked_at="2026-09-01T06:11:00+02:00",
                session=session,
            )
            recovered = notify_watcher_status(
                state_path,
                WEBHOOK_URL,
                error=None,
                checked_at="2026-09-01T06:21:00+02:00",
                session=session,
            )
            current = notify_watcher_status(
                state_path,
                WEBHOOK_URL,
                error=None,
                checked_at="2026-09-01T06:31:00+02:00",
                session=session,
            )
            self.assertEqual(
                (first, repeated, recovered, current),
                ("failure_sent", "failure_suppressed", "recovery_sent", "status_current"),
            )
            self.assertEqual(len(session.calls), 2)
            self.assertIn("kontrola selhala", session.calls[0][1]["json"]["content"])
            self.assertIn("opět funguje", session.calls[1][1]["json"]["content"])

    def test_failed_alert_is_retried_and_does_not_mark_it_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            with self.assertRaises(WatchError):
                notify_watcher_status(
                    state_path,
                    WEBHOOK_URL,
                    error="offline",
                    session=FakeSession(requests.ConnectionError("discord offline")),
                )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertFalse(state["failure_alert_sent"])

            session = FakeSession()
            outcome = notify_watcher_status(
                state_path,
                WEBHOOK_URL,
                error="offline",
                session=session,
            )
            self.assertEqual(outcome, "failure_sent")
            self.assertEqual(len(session.calls), 1)

    def test_failure_before_first_success_does_not_announce_existing_showings(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            session = FakeSession()
            notify_watcher_status(
                state_path,
                WEBHOOK_URL,
                error="offline",
                session=session,
            )
            notify_watcher_status(
                state_path,
                WEBHOOK_URL,
                error=None,
                session=session,
            )
            count = notify_discord(
                snapshot(showing("one", "16:40")),
                state_path,
                WEBHOOK_URL,
                session=session,
            )
            self.assertEqual(count, 0)
            self.assertEqual(len(session.calls), 2)


if __name__ == "__main__":
    unittest.main()
