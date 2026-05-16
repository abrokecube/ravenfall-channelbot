from __future__ import annotations

from pydantic import BaseModel, Field


class AccountConfig(BaseModel):
    """Configuration for the account synchronization system."""

    central_bot_id: str | None = Field(
        default=None, description="The ID of the bot acting as the central registry"
    )
    sync_enabled: bool = Field(
        default=False, description="Toggle for cross-bot account synchronization"
    )
