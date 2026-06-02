from collections.abc import Collection
from typing import Literal, TypedDict

import aiohttp
from msgspec import Struct, json


class WebOpsError(Exception):
    """WebOps Exception."""


class Character(TypedDict):
    username: str
    id: str


class RedeemItemsResponse(Struct):
    success: Literal[True]
    redeemed: dict[str, int]


class TotalLoyaltyResponse(Struct):
    status: Literal[True]
    total_points: int
    breakdown: dict[str, int]


class WebOpsClient:
    """Ravenfall web automations."""

    def __init__(self, base_url: str = "http://127.0.0.1:7102"):
        """Init."""
        self.base_url: str = base_url.rstrip("/")

    async def redeem_items(
        self, item_id: str, quantity: int, characters: list[Character]
    ):
        """Redeem items for a list of characters.

        Args:
            item_id: UUID of the item to redeem.
            quantity: Amount of item to redeem.
            characters: list of dicts with 'username' and 'id'.

        """
        url = f"{self.base_url}/redeem"
        payload = {"item_id": item_id, "quantity": quantity, "characters": characters}
        async with (
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60 * 15)
            ) as session,
            session.post(url, json=payload) as response,
        ):
            text = await response.text()
            if response.status != 200:  # noqa: PLR2004
                msg = f"Redemption failed: {response.status} - {text}"
                raise WebOpsError(msg)
            return json.decode(text, type=RedeemItemsResponse)

    async def get_total_loyalty_points(self, usernames: Collection[str]):
        """Get total loyalty points for a list of usernames."""
        url = f"{self.base_url}/loyalty/points"
        payload = {"usernames": usernames}
        async with (
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60 * 15)
            ) as session,
            session.post(url, json=payload) as response,
        ):
            text = await response.text()
            if response.status != 200:  # noqa: PLR2004
                msg = f"Failed to get points: {response.status} - {text}"
                raise WebOpsError(msg)
            return json.decode(text, type=TotalLoyaltyResponse)
