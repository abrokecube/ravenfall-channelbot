from __future__ import annotations

from pydantic import BaseModel, Field


class CurrencyConfig(BaseModel):
    """Configuration for the currency service."""

    name_singular: str = Field(
        default="Coin", description="Singular name of the currency"
    )
    name_plural: str = Field(default="Coins", description="Plural name of the currency")
