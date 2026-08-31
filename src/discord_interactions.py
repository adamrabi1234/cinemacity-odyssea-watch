"""Secure Discord slash-command support for an immediate watcher check."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from src.watch import WatchError, format_discord_line


APP_ROOT = Path(__file__).resolve().parent.parent
LATEST_PATH = APP_ROOT / "data" / "latest.json"
INTERACTION_PATH = "/discord/interactions"
COMMAND_NAME = "kontrola"
EPHEMERAL_FLAG = 1 << 6
MAX_REQUEST_BYTES = 64 * 1024
MAX_SIGNATURE_AGE_SECONDS = 300
DEFAULT_COOLDOWN_SECONDS = 60
DISCORD_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{20,}$")


class DiscordInteractionError(ValueError):
    """A Discord interaction cannot be safely accepted or processed."""


@dataclass(frozen=True)
class DiscordConfig:
    application_id: str
    public_key: str
    guild_id: str
    allowed_user_ids: frozenset[str]


def load_discord_config(environ: dict[str, str] | None = None) -> DiscordConfig | None:
    env = environ if environ is not None else os.environ
    names = (
        "DISCORD_APPLICATION_ID",
        "DISCORD_PUBLIC_KEY",
        "DISCORD_ALLOWED_GUILD_ID",
        "DISCORD_ALLOWED_USER_IDS",
    )
    values = {name: str(env.get(name, "")).strip() for name in names}
    if not any(values.values()):
        return None
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise DiscordInteractionError(
            "Discord command configuration is incomplete: " + ", ".join(missing)
        )

    application_id = values["DISCORD_APPLICATION_ID"]
    guild_id = values["DISCORD_ALLOWED_GUILD_ID"]
    user_ids = frozenset(
        item.strip()
        for item in values["DISCORD_ALLOWED_USER_IDS"].split(",")
        if item.strip()
    )
    if not application_id.isdigit() or not guild_id.isdigit():
        raise DiscordInteractionError("Discord application and guild IDs must be numeric.")
    if not user_ids or any(not user_id.isdigit() for user_id in user_ids):
        raise DiscordInteractionError("Discord allowed user IDs must be numeric.")
    try:
        public_key_bytes = bytes.fromhex(values["DISCORD_PUBLIC_KEY"])
        VerifyKey(public_key_bytes)
    except (ValueError, TypeError) as exc:
        raise DiscordInteractionError("DISCORD_PUBLIC_KEY is not a valid Ed25519 key.") from exc

    return DiscordConfig(
        application_id=application_id,
        public_key=values["DISCORD_PUBLIC_KEY"],
        guild_id=guild_id,
        allowed_user_ids=user_ids,
    )


def verify_request_signature(
    public_key: str,
    timestamp: str,
    signature: str,
    body: bytes,
    *,
    now: float | None = None,
) -> None:
    """Validate Discord's signature and reject stale replayed requests."""
    try:
        timestamp_number = int(timestamp)
        signature_bytes = bytes.fromhex(signature)
        verify_key = VerifyKey(bytes.fromhex(public_key))
    except (TypeError, ValueError) as exc:
        raise DiscordInteractionError("Invalid Discord signature headers.") from exc
    current_time = time.time() if now is None else now
    if abs(current_time - timestamp_number) > MAX_SIGNATURE_AGE_SECONDS:
        raise DiscordInteractionError("Discord interaction timestamp is stale.")
    try:
        verify_key.verify(timestamp.encode("ascii") + body, signature_bytes)
    except (BadSignatureError, ValueError) as exc:
        raise DiscordInteractionError("Invalid Discord request signature.") from exc


def interaction_user_id(payload: dict[str, Any]) -> str:
    member = payload.get("member")
    if isinstance(member, dict) and isinstance(member.get("user"), dict):
        return str(member["user"].get("id") or "")
    user = payload.get("user")
    if isinstance(user, dict):
        return str(user.get("id") or "")
    return ""


def authorize_command(payload: dict[str, Any], config: DiscordConfig) -> str | None:
    """Return a Czech denial message, otherwise authorize the command."""
    if str(payload.get("application_id") or "") != config.application_id:
        return "Tento příkaz nepatří této aplikaci."
    if str(payload.get("guild_id") or "") != config.guild_id:
        return "Tento příkaz je povolený pouze na nastaveném Discord serveru."
    if interaction_user_id(payload) not in config.allowed_user_ids:
        return "Tento příkaz pro tebe není povolený."
    data = payload.get("data")
    if not isinstance(data, dict) or str(data.get("name") or "") != COMMAND_NAME:
        return "Neznámý příkaz."
    return None


class CommandLimiter:
    """Prevent overlapping checks and accidental request bursts."""

    def __init__(self, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._running = False
        self._last_started = 0.0

    def reserve(self, *, now: float | None = None) -> tuple[bool, str | None, int]:
        current = time.monotonic() if now is None else now
        with self._lock:
            if self._running:
                return False, "running", 0
            remaining = max(0, int(self.cooldown_seconds - (current - self._last_started)))
            if self._last_started and remaining > 0:
                return False, "cooldown", remaining
            self._running = True
            self._last_started = current
            return True, None, 0

    def finish(self) -> None:
        with self._lock:
            self._running = False


def command_payloads(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Build bounded ephemeral interaction responses with all current showings."""
    showings = [item for item in snapshot.get("showings", []) if isinstance(item, dict)]
    count = len(showings)
    if not showings:
        return [
            {
                "content": "✅ Kontrola dokončena. Aktuálně nebyl nalezen žádný termín.",
                "allowed_mentions": {"parse": []},
            }
        ]

    lines = [format_discord_line(showing) for showing in showings]
    descriptions: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in lines:
        extra = len(line) + (1 if current else 0)
        if current and current_length + extra > 3800:
            descriptions.append("\n".join(current))
            current = []
            current_length = 0
        current.append(line)
        current_length += len(line) + (1 if current_length else 0)
    if current:
        descriptions.append("\n".join(current))

    checked_at = str(snapshot.get("checked_at") or "neznámý čas")
    payloads: list[dict[str, Any]] = []
    for index, description in enumerate(descriptions):
        part = "" if len(descriptions) == 1 else f" — část {index + 1}/{len(descriptions)}"
        payloads.append(
            {
                "content": f"🎬 **Kontrola dokončena: {count} aktuálních termínů**{part}",
                "embeds": [
                    {
                        "description": description,
                        "footer": {"text": f"Zkontrolováno: {checked_at}"},
                        "color": 0xE21C2A,
                    }
                ],
                "allowed_mentions": {"parse": []},
            }
        )
    return payloads


def interaction_url(config: DiscordConfig, token: str, *, original: bool) -> str:
    if not DISCORD_TOKEN_RE.fullmatch(token):
        raise DiscordInteractionError("Discord interaction token has an invalid format.")
    suffix = "/messages/@original" if original else ""
    return (
        "https://discord.com/api/v10/webhooks/"
        f"{config.application_id}/{quote(token, safe='')}{suffix}"
    )


def send_interaction_payloads(
    config: DiscordConfig,
    token: str,
    payloads: list[dict[str, Any]],
    *,
    session: requests.Session | None = None,
) -> None:
    client = session or requests.Session()
    first, *followups = payloads
    response = client.patch(
        interaction_url(config, token, original=True), json=first, timeout=15
    )
    response.raise_for_status()
    for payload in followups:
        followup = dict(payload)
        followup["flags"] = EPHEMERAL_FLAG
        response = client.post(
            interaction_url(config, token, original=False),
            json=followup,
            timeout=15,
        )
        response.raise_for_status()


def run_check_subprocess(
    *, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
) -> tuple[int, str, str]:
    result = runner(
        [sys.executable, str(APP_ROOT / "src" / "watch.py")],
        cwd=APP_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def complete_command(
    config: DiscordConfig,
    token: str,
    limiter: CommandLimiter,
    *,
    session: requests.Session | None = None,
) -> None:
    """Run the watcher after a deferred response and edit that response."""
    try:
        returncode, stdout, stderr = run_check_subprocess()
        if stdout:
            print(stdout.rstrip(), flush=True)
        if stderr:
            print(stderr.rstrip(), file=sys.stderr, flush=True)
        if returncode == 0:
            snapshot = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
            payloads = command_payloads(snapshot)
        elif returncode == 3:
            payloads = [
                {
                    "content": "⏳ Jiná kontrola právě probíhá. Zkus příkaz znovu za chvíli.",
                    "allowed_mentions": {"parse": []},
                }
            ]
        else:
            payloads = [
                {
                    "content": (
                        "❌ Ruční kontrola selhala. Poslední platná data zůstávají "
                        "dostupná a podrobnost je uložená v logu serveru."
                    ),
                    "allowed_mentions": {"parse": []},
                }
            ]
        send_interaction_payloads(config, token, payloads, session=session)
    except requests.RequestException as exc:
        print(f"ERROR: Discord response delivery failed: {exc}", file=sys.stderr, flush=True)
    except (OSError, ValueError, WatchError, subprocess.SubprocessError) as exc:
        print(f"ERROR: Discord command completion failed: {exc}", file=sys.stderr, flush=True)
        try:
            send_interaction_payloads(
                config,
                token,
                [
                    {
                        "content": "❌ Odpověď na ruční kontrolu se nepodařilo dokončit.",
                        "allowed_mentions": {"parse": []},
                    }
                ],
                session=session,
            )
        except (DiscordInteractionError, requests.RequestException):
            pass
    finally:
        limiter.finish()


def ephemeral_message(content: str) -> dict[str, Any]:
    return {
        "type": 4,
        "data": {
            "content": content,
            "flags": EPHEMERAL_FLAG,
            "allowed_mentions": {"parse": []},
        },
    }
