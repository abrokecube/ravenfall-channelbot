from pydantic import BaseModel


class InstanceConfig(BaseModel):
    """Ravenfall instance config.

    'channel_name' maps to 'twitch_login' on the ravenfall config
    """

    channel_name: str
    sandboxie_box_name: str | None = None
    start_command: str
    auto_restart_period_seconds: float | None = None
    restart_warning_times: list[float] = [120, 30]
    restart_unblock_min_seconds: float = 45
    restart_timeout_seconds: float = 120
    message_on_restart_timeout: str = "@abrokecube"
    max_memory_usage_gb: float | None = None


class WatcherConfig(BaseModel):
    """Ravenfall watcher cog config."""

    instances: list[InstanceConfig]
    ravenfall_folder: str
    max_total_memory_use_gb: float | None = None
    default_max_instance_memory_usage_gb: float = 6.0
    memory_kill_min_threshold_gb: float = 2.0
