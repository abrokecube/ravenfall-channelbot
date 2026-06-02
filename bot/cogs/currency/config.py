from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from bot.services.config_service import ConfigModel


class CurrencyConfig(ConfigModel):
    """Configuration for the currency system."""

    config_table_name: ClassVar[str | None] = "services.currency"

    name_singular: str = Field(
        default="Coin", description="Singular name of the currency"
    )
    name_plural: str = Field(default="Coins", description="Plural name of the currency")
    remote_enabled: bool = Field(
        default=True, description="Whether to include balances from remote bots"
    )
