"""Typed settings from environment variables, and town config from channels.json.

Nothing is read at import. `get_settings()` reads the environment the first time
it is called, so `load_dotenv()` has to run before that, as it does in `main.py`.
Each variable keeps the name it has in `.env.example`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import cache
from typing import Any, Mapping


class ConfigError(Exception):
    """A setting is needed but isn't set."""


def _optional_int(env: Mapping[str, str], name: str) -> int | None:
    value = env.get(name)
    return None if value is None else int(value)


@dataclass(frozen=True)
class Settings:
    log_level: str = "info"
    bot_command_prefix: str = "!"
    command_testing: bool = False
    disable_ravenfall_integration: bool = False

    twitch_app_id: str = ""
    twitch_app_secret: str | None = None
    bot_user_id: str | None = None
    owner_twitch_id: str | None = None
    owner_twitch_username: str = "abrokecube"
    ravenbot_user_id: str | None = None

    ravenfall_api_user: str | None = None
    ravenfall_api_pass: str | None = None

    sandboxie_start_path: str | None = None
    ravenfall_folder: str | None = None
    ravenbot_folder: str | None = None
    ravenfall_sandboxed_folder: str | None = None
    state_data_backup_retention_count: int | None = None

    private_server_host: str = "0.0.0.0"
    private_server_port: int = 8080
    rf_middleman_processor_host: str | None = None
    rf_middleman_processor_port: str | None = None

    prometheus_url: str | None = None
    multichat_command_server_host: str | None = None
    multichat_command_server_port: int | None = None
    rf_middleman_host: str | None = None
    rf_middleman_port: str | None = None
    watcher_urls: tuple[str, ...] = ("http://127.0.0.1:8110",)
    webops_url: str = "http://pc2-mobile:7102"

    pastebin_provider: str = "pastes"
    borkedbin_host: str = "https://bin.borkedcube.moe"
    borkedbin_api_key: str = ""

    max_ravenfall_total_mib: int = 10240
    max_ravenfall_mib: int = 5120
    item_values_path: str = "data/item_values.json"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        defaults = cls()
        watcher_urls = env.get("WATCHER_URLS")
        return cls(
            log_level=env.get("LOG_LEVEL", defaults.log_level),
            bot_command_prefix=env.get("BOT_COMMAND_PREFIX", defaults.bot_command_prefix),
            command_testing=env.get("COMMAND_TESTING") == "1",
            disable_ravenfall_integration=env.get("DISABLE_RAVENFALL_INTEGRATION", "").lower() in ("1", "true"),
            twitch_app_id=env.get("TWITCH_APP_ID", defaults.twitch_app_id),
            twitch_app_secret=env.get("TWITCH_APP_SECRET"),
            bot_user_id=env.get("BOT_USER_ID"),
            owner_twitch_id=env.get("OWNER_TWITCH_ID"),
            owner_twitch_username=env.get("OWNER_TWITCH_USERNAME", defaults.owner_twitch_username),
            ravenbot_user_id=env.get("RAVENBOT_USER_ID"),
            ravenfall_api_user=env.get("RAVENFALL_API_USER"),
            ravenfall_api_pass=env.get("RAVENFALL_API_PASS"),
            sandboxie_start_path=env.get("SANDBOXIE_START_PATH"),
            ravenfall_folder=env.get("RAVENFALL_FOLDER"),
            ravenbot_folder=env.get("RAVENBOT_FOLDER"),
            ravenfall_sandboxed_folder=env.get("RAVENFALL_SANDBOXED_FOLDER"),
            state_data_backup_retention_count=_optional_int(env, "STATE_DATA_BACKUP_RETENTION_COUNT"),
            private_server_host=env.get("PRIVATE_SERVER_HOST", defaults.private_server_host),
            private_server_port=int(env.get("PRIVATE_SERVER_PORT", defaults.private_server_port)),
            rf_middleman_processor_host=env.get("RF_MIDDLEMAN_PROCESSOR_HOST"),
            rf_middleman_processor_port=env.get("RF_MIDDLEMAN_PROCESSOR_PORT"),
            prometheus_url=env.get("PROMETHEUS_URL"),
            multichat_command_server_host=env.get("MULTICHAT_COMMAND_SERVER_HOST"),
            multichat_command_server_port=_optional_int(env, "MULTICHAT_COMMAND_SERVER_PORT"),
            rf_middleman_host=env.get("RF_MIDDLEMAN_HOST"),
            rf_middleman_port=env.get("RF_MIDDLEMAN_PORT"),
            watcher_urls=tuple(watcher_urls.split(",")) if watcher_urls is not None else defaults.watcher_urls,
            webops_url=env.get("WEBOPS_URL", defaults.webops_url),
            pastebin_provider=env.get("PASTEBIN_PROVIDER", defaults.pastebin_provider).lower(),
            borkedbin_host=env.get("BORKEDBIN_HOST", defaults.borkedbin_host).strip("/"),
            borkedbin_api_key=env.get("BORKEDBIN_API_KEY", defaults.borkedbin_api_key),
            max_ravenfall_total_mib=int(env.get("MAX_RAVENFALL_TOTAL_MIB", defaults.max_ravenfall_total_mib)),
            max_ravenfall_mib=int(env.get("MAX_RAVENFALL_MIB", defaults.max_ravenfall_mib)),
            item_values_path=env.get("ITEM_VALUES_PATH", defaults.item_values_path),
        )

    @property
    def multichat_url(self) -> str:
        if not self.multichat_command_server_host or self.multichat_command_server_port is None:
            raise ConfigError("MULTICHAT_COMMAND_SERVER_HOST and MULTICHAT_COMMAND_SERVER_PORT must be set")
        return f"http://{self.multichat_command_server_host}:{self.multichat_command_server_port}"

    @property
    def middleman_url(self) -> str:
        if not self.rf_middleman_host or not self.rf_middleman_port:
            raise ConfigError("RF_MIDDLEMAN_HOST and RF_MIDDLEMAN_PORT must be set")
        return f"http://{self.rf_middleman_host.rstrip('/')}:{self.rf_middleman_port}"


@cache
def get_settings() -> Settings:
    """The process's settings. A test that changes the environment calls `get_settings.cache_clear()`."""
    return Settings.from_env()


def _prefixes(value: str | list | tuple) -> tuple:
    return (value,) if isinstance(value, str) else tuple(value)


@dataclass(frozen=True)
class ChannelConfig:
    """One town from channels.json. channels_example.json shows the format."""

    channel_id: str
    channel_name: str
    rf_query_url: str
    ravenbot_prefixes: tuple = ("!",)
    custom_town_msg: str = ""
    welcome_message: str = ""
    receive_global_alerts: bool = False
    sandboxie_box: str = ""
    ravenfall_start_script: str = ""
    auto_restart: bool = False
    event_notifications: bool = False
    auto_end_ravebridge: bool = True
    restart_period: int = 3600
    middleman_connection_id: str = ""
    ravenfall_loc_strings_path: str | None = None
    auto_restore_raids: bool = False
    restart_timeout: int = 120
    town_level_notifications: bool = True
    channel_points_redeems: bool = False
    pause_monitoring: bool = False
    command_prefix: str = "!"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ChannelConfig:
        """Builds a town from a channels.json entry. Keys this bot doesn't know are ignored."""
        defaults = cls("", "", "")
        return cls(
            channel_id=str(raw["channel_id"]),
            channel_name=raw["channel_name"],
            rf_query_url=raw["rf_query_url"].rstrip("/"),
            ravenbot_prefixes=_prefixes(raw.get("ravenbot_prefix", defaults.ravenbot_prefixes)),
            custom_town_msg=raw.get("custom_town_msg", defaults.custom_town_msg),
            welcome_message=raw.get("welcome_message", defaults.welcome_message),
            receive_global_alerts=bool(raw.get("receive_global_alerts", defaults.receive_global_alerts)),
            sandboxie_box=raw.get("sandboxie_box", defaults.sandboxie_box),
            ravenfall_start_script=raw.get("ravenfall_start_script", defaults.ravenfall_start_script),
            auto_restart=bool(raw.get("auto_restart", defaults.auto_restart)),
            event_notifications=bool(raw.get("event_notifications", defaults.event_notifications)),
            auto_end_ravebridge=bool(raw.get("auto_end_ravebridge", defaults.auto_end_ravebridge)),
            restart_period=int(raw.get("restart_period", defaults.restart_period)),
            middleman_connection_id=raw.get("middleman_connection_id", defaults.middleman_connection_id),
            ravenfall_loc_strings_path=raw.get("ravenfall_loc_strings_path", defaults.ravenfall_loc_strings_path),
            auto_restore_raids=bool(raw.get("auto_restore_raids", defaults.auto_restore_raids)),
            restart_timeout=int(raw.get("restart_timeout", defaults.restart_timeout)),
            town_level_notifications=bool(raw.get("town_level_notifications", defaults.town_level_notifications)),
            channel_points_redeems=bool(raw.get("channel_points_redeems", defaults.channel_points_redeems)),
            pause_monitoring=bool(raw.get("pause_monitoring", defaults.pause_monitoring)),
            command_prefix=raw.get("command_prefix", defaults.command_prefix),
        )


def load_channels(path: str = "channels.json") -> list[ChannelConfig]:
    with open(path, "r") as file:
        return [ChannelConfig.from_dict(raw) for raw in json.load(file)]
