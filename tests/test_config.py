"""Run with: python -m unittest tests.test_config"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bot.config import ChannelConfig, ConfigError, Settings, get_settings, load_channels
from tests.test_channel_bugfixes import set_env


class SettingsFromEnv(unittest.TestCase):
    def test_defaults_match_the_old_getenv_calls(self):
        settings = Settings.from_env({})
        self.assertEqual(settings.bot_command_prefix, "!")
        self.assertEqual(settings.owner_twitch_username, "abrokecube")
        self.assertEqual(settings.private_server_port, 8080)
        self.assertEqual(settings.watcher_urls, ("http://127.0.0.1:8110",))
        self.assertEqual(settings.webops_url, "http://pc2-mobile:7102")
        self.assertEqual(settings.pastebin_provider, "pastes")
        self.assertEqual(settings.max_ravenfall_total_mib, 10240)
        self.assertEqual(settings.max_ravenfall_mib, 5120)
        self.assertIsNone(settings.state_data_backup_retention_count)

    def test_values_keep_their_env_names(self):
        settings = Settings.from_env({
            "BOT_USER_ID": "1",
            "PRIVATE_SERVER_PORT": "7110",
            "STATE_DATA_BACKUP_RETENTION_COUNT": "100",
            "WATCHER_URLS": "http://a:1,http://b:2",
            "PASTEBIN_PROVIDER": "BorkedBin",
            "BORKEDBIN_HOST": "https://bin.example/",
        })
        self.assertEqual(settings.bot_user_id, "1")
        self.assertEqual(settings.private_server_port, 7110)
        self.assertEqual(settings.state_data_backup_retention_count, 100)
        self.assertEqual(settings.watcher_urls, ("http://a:1", "http://b:2"))
        self.assertEqual(settings.pastebin_provider, "borkedbin")
        self.assertEqual(settings.borkedbin_host, "https://bin.example")

    def test_flags(self):
        for value, expected in (("1", True), ("true", True), ("TRUE", True), ("0", False), ("", False)):
            with self.subTest(value):
                settings = Settings.from_env({"DISABLE_RAVENFALL_INTEGRATION": value})
                self.assertIs(settings.disable_ravenfall_integration, expected)
        self.assertTrue(Settings.from_env({"COMMAND_TESTING": "1"}).command_testing)
        self.assertFalse(Settings.from_env({"COMMAND_TESTING": "true"}).command_testing)

    def test_service_urls(self):
        settings = Settings.from_env({
            "MULTICHAT_COMMAND_SERVER_HOST": "pc3-server",
            "MULTICHAT_COMMAND_SERVER_PORT": "7200",
            "RF_MIDDLEMAN_HOST": "pc3-server/",
            "RF_MIDDLEMAN_PORT": "7101",
        })
        self.assertEqual(settings.multichat_url, "http://pc3-server:7200")
        self.assertEqual(settings.middleman_url, "http://pc3-server:7101")

    def test_service_urls_need_host_and_port(self):
        settings = Settings.from_env({"MULTICHAT_COMMAND_SERVER_HOST": "a", "RF_MIDDLEMAN_PORT": "1"})
        with self.assertRaises(ConfigError):
            settings.multichat_url
        with self.assertRaises(ConfigError):
            settings.middleman_url

    def test_settings_are_cached_until_cleared(self):
        set_env(self, OWNER_TWITCH_USERNAME="first")
        self.assertEqual(get_settings().owner_twitch_username, "first")
        os.environ["OWNER_TWITCH_USERNAME"] = "second"
        self.assertEqual(get_settings().owner_twitch_username, "first")
        get_settings.cache_clear()
        self.assertEqual(get_settings().owner_twitch_username, "second")


class Imports(unittest.TestCase):
    def test_bot_modules_import_without_any_settings(self):
        names = (
            "MULTICHAT_COMMAND_SERVER_HOST", "MULTICHAT_COMMAND_SERVER_PORT",
            "RF_MIDDLEMAN_HOST", "RF_MIDDLEMAN_PORT",
        )
        env = {key: value for key, value in os.environ.items() if key not in names}
        code = (
            "import bot.config, bot.middleman, bot.multichat_command, bot.ravenfallchannel, bot.ravenfallmanager\n"
            "assert bot.config.get_settings.cache_info().currsize == 0, 'settings were read at import'\n"
        )
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class Channels(unittest.TestCase):
    def test_required_keys_only(self):
        config = ChannelConfig.from_dict({"channel_id": 123, "channel_name": "Town", "rf_query_url": "http://a/b//"})
        self.assertEqual(config.channel_id, "123")
        self.assertEqual(config.channel_name, "Town")
        self.assertEqual(config.rf_query_url, "http://a/b")
        self.assertEqual(config.ravenbot_prefixes, ("!",))
        self.assertEqual(config.restart_period, 3600)
        self.assertEqual(config.restart_timeout, 120)
        self.assertIs(config.auto_restart, False)
        self.assertIs(config.auto_end_ravebridge, True)
        self.assertIs(config.town_level_notifications, True)
        self.assertIs(config.channel_points_redeems, False)
        self.assertIsNone(config.ravenfall_loc_strings_path)
        self.assertEqual(config.command_prefix, "!")

    def test_missing_required_key(self):
        with self.assertRaises(KeyError):
            ChannelConfig.from_dict({"channel_id": "1", "channel_name": "town"})

    def test_prefix_forms(self):
        base = {"channel_id": "1", "channel_name": "town", "rf_query_url": "http://a"}
        for prefix, expected in (("!", ("!",)), (["!", "?"], ("!", "?")), (("!",), ("!",))):
            with self.subTest(prefix):
                config = ChannelConfig.from_dict({**base, "ravenbot_prefix": prefix})
                self.assertEqual(config.ravenbot_prefixes, expected)

    def test_unknown_keys_are_ignored(self):
        config = ChannelConfig.from_dict({
            "channel_id": "1", "channel_name": "town", "rf_query_url": "http://a", "something_new": True,
        })
        self.assertFalse(hasattr(config, "something_new"))

    def test_example_file_loads(self):
        channels = load_channels("channels_example.json")
        raw = json.loads(Path("channels_example.json").read_text())
        self.assertEqual(len(channels), len(raw))
        self.assertEqual(channels[0].channel_name, raw[0]["channel_name"])
        self.assertEqual(channels[0].restart_period, raw[0]["restart_period"])
        self.assertTrue(channels[0].channel_points_redeems)

    def test_load_channels_reads_the_given_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "channels.json"
            path.write_text(json.dumps([{"channel_id": "1", "channel_name": "a", "rf_query_url": "http://a"}]))
            self.assertEqual([x.channel_id for x in load_channels(str(path))], ["1"])
