import json
import unittest

import requests
from nacl.signing import SigningKey

from src.discord_interactions import (
    CommandLimiter,
    DiscordConfig,
    DiscordInteractionError,
    authorize_command,
    command_payloads,
    load_discord_config,
    send_interaction_payloads,
    verify_request_signature,
)


APPLICATION_ID = "123456789012345678"
GUILD_ID = "234567890123456789"
USER_ID = "345678901234567890"
TOKEN = "abcdefghijklmnopqrstuvwxyz.ABCDEFGHIJKLMNOPQRSTUVWXYZ_123"


def config(public_key: str = "11" * 32) -> DiscordConfig:
    return DiscordConfig(
        application_id=APPLICATION_ID,
        public_key=public_key,
        guild_id=GUILD_ID,
        allowed_user_ids=frozenset({USER_ID}),
    )


def command_payload(**updates):
    payload = {
        "type": 2,
        "application_id": APPLICATION_ID,
        "guild_id": GUILD_ID,
        "token": TOKEN,
        "member": {"user": {"id": USER_ID}},
        "data": {"name": "kontrola"},
    }
    payload.update(updates)
    return payload


def showing(event_id: str, hour: str) -> dict:
    return {
        "event_id": event_id,
        "datetime": f"2026-09-08T{hour}:00+02:00",
        "auditorium": "IMAX VOLVO",
        "booking_url": f"https://tickets.cinemacity.cz/order/{event_id}",
    }


class FakeResponse:
    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self):
        self.calls = []

    def patch(self, url, **kwargs):
        self.calls.append(("PATCH", url, kwargs))
        return FakeResponse()

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse()


class DiscordInteractionTests(unittest.TestCase):
    def test_disabled_config_is_allowed(self):
        self.assertIsNone(load_discord_config({}))

    def test_complete_config_is_validated(self):
        key = SigningKey.generate().verify_key.encode().hex()
        loaded = load_discord_config(
            {
                "DISCORD_APPLICATION_ID": APPLICATION_ID,
                "DISCORD_PUBLIC_KEY": key,
                "DISCORD_ALLOWED_GUILD_ID": GUILD_ID,
                "DISCORD_ALLOWED_USER_IDS": f"{USER_ID}, 456789012345678901",
            }
        )
        self.assertEqual(loaded.application_id, APPLICATION_ID)
        self.assertIn(USER_ID, loaded.allowed_user_ids)

    def test_partial_config_is_rejected(self):
        with self.assertRaises(DiscordInteractionError):
            load_discord_config({"DISCORD_APPLICATION_ID": APPLICATION_ID})

    def test_valid_signature_is_accepted(self):
        signing_key = SigningKey.generate()
        body = json.dumps(command_payload()).encode()
        timestamp = "1788170400"
        signature = signing_key.sign(timestamp.encode() + body).signature.hex()
        verify_request_signature(
            signing_key.verify_key.encode().hex(),
            timestamp,
            signature,
            body,
            now=1788170401,
        )

    def test_invalid_or_stale_signature_is_rejected(self):
        signing_key = SigningKey.generate()
        body = b"{}"
        timestamp = "1788170400"
        signature = signing_key.sign(timestamp.encode() + body).signature.hex()
        with self.assertRaises(DiscordInteractionError):
            verify_request_signature(
                signing_key.verify_key.encode().hex(),
                timestamp,
                signature,
                b'{"changed":true}',
                now=1788170401,
            )
        with self.assertRaises(DiscordInteractionError):
            verify_request_signature(
                signing_key.verify_key.encode().hex(),
                timestamp,
                signature,
                body,
                now=1788171001,
            )

    def test_command_is_restricted_to_configured_guild_and_user(self):
        self.assertIsNone(authorize_command(command_payload(), config()))
        denied_guild = command_payload(guild_id="999999999999999999")
        self.assertIn("serveru", authorize_command(denied_guild, config()))
        denied_user = command_payload(member={"user": {"id": "999999999999999999"}})
        self.assertIn("povolený", authorize_command(denied_user, config()))

    def test_limiter_blocks_overlap_and_cooldown(self):
        limiter = CommandLimiter(cooldown_seconds=60)
        self.assertEqual(limiter.reserve(now=100), (True, None, 0))
        self.assertEqual(limiter.reserve(now=101)[1], "running")
        limiter.finish()
        allowed, reason, remaining = limiter.reserve(now=120)
        self.assertFalse(allowed)
        self.assertEqual(reason, "cooldown")
        self.assertEqual(remaining, 40)
        self.assertEqual(limiter.reserve(now=160), (True, None, 0))

    def test_command_response_contains_all_booking_links(self):
        snapshot = {
            "checked_at": "2026-09-01T06:01:00+02:00",
            "showings": [showing("one", "16:40"), showing("two", "20:30")],
        }
        payloads = command_payloads(snapshot)
        rendered = "\n".join(
            payload["embeds"][0]["description"] for payload in payloads
        )
        self.assertIn("https://tickets.cinemacity.cz/order/one", rendered)
        self.assertIn("https://tickets.cinemacity.cz/order/two", rendered)
        self.assertIn("2 aktuálních termínů", payloads[0]["content"])

    def test_interaction_response_edits_original_then_posts_followups(self):
        session = FakeSession()
        payloads = [{"content": "first"}, {"content": "second"}]
        send_interaction_payloads(config(), TOKEN, payloads, session=session)
        self.assertEqual([call[0] for call in session.calls], ["PATCH", "POST"])
        self.assertTrue(session.calls[0][1].endswith("/messages/@original"))
        self.assertEqual(session.calls[1][2]["json"]["flags"], 64)

    def test_invalid_interaction_token_is_rejected_without_request(self):
        session = FakeSession()
        with self.assertRaises(DiscordInteractionError):
            send_interaction_payloads(
                config(),
                "bad/token",
                [{"content": "no"}],
                session=session,
            )
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
