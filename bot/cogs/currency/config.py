from __future__ import annotations

from pydantic import BaseModel, Field


class CurrencyConfig(BaseModel):
    """Configuration for the currency system."""

    name_singular: str = Field(
        default="Coin", description="Singular name of the currency"
    )
    name_plural: str = Field(default="Coins", description="Plural name of the currency")
    remote_enabled: bool = Field(
        default=True, description="Whether to include balances from remote bots"
    )
