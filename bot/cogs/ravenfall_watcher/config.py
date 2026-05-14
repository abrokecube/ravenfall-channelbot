from typing import TYPE_CHECKING, ClassVar

from pydantic import Field, field_validator

from bot.services.config_service import ConfigModel

if TYPE_CHECKING:
    pass


class InstanceConfig(ConfigModel):
    """Ravenfall instance config.

    'channel_name' maps to 'twitch_login' on the ravenfall config
    """

    channel_name: str
    sandboxie_box_name: str | None = None
    start_command: str
    auto_restart_period_seconds: float | None = None
    restart_warning_times: list[float] = Field(default_factory=lambda: [120, 30])
    restart_unblock_min_seconds: float = 45
    restart_timeout_seconds: float = 120
    message_on_restart_timeout: str = "@abrokecube"
    max_memory_usage_gb: float | None = None
    ravenbot_prefix: str = "!"
    ravenbot_channel_id: str | None = None
    max_dungeon_time_seconds: float = 900
    max_dungeon_prepare_time_seconds: float = 30


class WatcherConfig(ConfigModel):
    """Ravenfall watcher cog config."""

    config_table_name: ClassVar[str | None] = "cogs.ravenfall_watcher"

    instances: list[InstanceConfig]
    ravenfall_folder: str
    ravenfall_executable_name: str = "Ravenfall.exe"
    ravenbot_folder: str
    ravenbot_executable_name: str = "RavenBot.exe"
    max_total_memory_use_gb: float | None = None
    default_max_instance_memory_usage_gb: float = 6.0
    memory_kill_min_threshold_gb: float = 2.0
    commands_to_watch: set[str] = {
        "coins",
        "count",
        "damage",
        "dmg",
        "dps",
        "effects",
        "ferry",
        "items",
        "multiplier",
        "online",
        "res",
        "resources",
        "rested",
        "status",
        "stats",
        "town",
        "townres",
        "training",
        "value",
        "version",
        "village",
        "villagers",
        "where",
        "consume",
        "disenchant",
        "drink",
        "eat",
        "enchant",
        "gift",
        "join",
        "leave",
        "scrolls",
    }

    @field_validator("commands_to_watch", mode="after")
    @classmethod
    def _lowercase_commands_to_watch(cls, value: set[str]):
        if isinstance(value, str):
            return {value.lower()}
        return {str(item).lower() for item in value}
