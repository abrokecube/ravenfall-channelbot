from __future__ import annotations
from typing import ClassVar

from pydantic import Field

from bot.services.config_service import ConfigModel


class AccountConfig(ConfigModel):
    """Configuration for the account synchronization system."""

    config_table_name: ClassVar[str | None] = "cogs.accounts"

    central_bot_id: str | None = Field(
        default=None, description="The ID of the bot acting as the central registry"
    )
    sync_enabled: bool = Field(
        default=False, description="Toggle for cross-bot account synchronization"
    )
