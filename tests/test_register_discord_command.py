import os
import unittest
from unittest.mock import patch

from src.register_discord_command import main


class RegisterDiscordCommandsTests(unittest.TestCase):
    def test_bulk_registration_replaces_old_command_with_two_new_commands(self):
        environment = {
            "DISCORD_APPLICATION_ID": "123456789012345678",
            "DISCORD_ALLOWED_GUILD_ID": "234567890123456789",
            "DISCORD_BOT_TOKEN": "test-token-for-registration",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "src.register_discord_command.requests.put"
        ) as request:
            request.return_value.json.return_value = [
                {"name": "newdates", "id": "1"},
                {"name": "alldates", "id": "2"},
            ]
            self.assertEqual(main(), 0)

        request.assert_called_once()
        payload = request.call_args.kwargs["json"]
        self.assertEqual({command["name"] for command in payload}, {"newdates", "alldates"})
        self.assertNotIn("kontrola", {command["name"] for command in payload})


if __name__ == "__main__":
    unittest.main()
