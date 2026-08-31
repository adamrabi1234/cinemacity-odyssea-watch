#!/usr/bin/env python3
"""Replace guild commands with /newdates and /alldates."""

from __future__ import annotations

import os
import sys

import requests


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    application_id = required_env("DISCORD_APPLICATION_ID")
    guild_id = required_env("DISCORD_ALLOWED_GUILD_ID")
    bot_token = required_env("DISCORD_BOT_TOKEN")
    if not application_id.isdigit() or not guild_id.isdigit():
        raise SystemExit("Discord application and guild IDs must be numeric.")

    url = (
        "https://discord.com/api/v10/applications/"
        f"{application_id}/guilds/{guild_id}/commands"
    )
    payload = [
        {
            "name": "newdates",
            "description": "Zkontroluje a zobrazí pouze nově přidané termíny",
            "type": 1,
        },
        {
            "name": "alldates",
            "description": "Zkontroluje a zobrazí všechny aktuální termíny",
            "type": 1,
        },
    ]
    try:
        response = requests.put(
            url,
            headers={
                "Authorization": f"Bot {bot_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        detail = ""
        if getattr(exc, "response", None) is not None:
            detail = f" HTTP {exc.response.status_code}: {exc.response.text[:500]}"
        print(f"Discord command registration failed:{detail}", file=sys.stderr)
        return 1

    commands = response.json()
    registered = ", ".join(
        f"/{command.get('name', 'unknown')} ({command.get('id', 'unknown')})"
        for command in commands
    )
    print(f"Registered guild commands: {registered}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
