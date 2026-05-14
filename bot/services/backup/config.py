from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from bot.services.config_service import ConfigModel


class BackupItem(ConfigModel):
    """A specific file or directory to be backed up."""

    input_path: str
    output_subpath: str


class BackupConfig(ConfigModel):
    """Configuration for the backup service."""

    config_table_name: ClassVar[str | None] = "services.backup"

    rclone_binary: str = "rclone"
    remote_name: str = "gdrive"
    remote_root: str = "backups"
    folder_limit: int = 5
    interval_seconds: int = 3600
    registered_items: list[BackupItem] = Field(default_factory=list)
