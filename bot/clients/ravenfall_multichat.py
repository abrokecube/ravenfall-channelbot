"""Example client for sending commands to the Ravenfall MultiChat server via HTTP.

This script demonstrates how to send commands to the chat bot using the HTTP endpoint.
Make sure the server is running and the COMMAND_SERVER_HOST and COMMAND_SERVER_PORT
environment variables are properly set.
"""

import logging
from typing import Any, cast

import aiohttp
from msgspec import Struct, field, json

logger = logging.getLogger(__name__)


class GenericApiResponse[T](Struct, tag_field="status", tag="success"):
    """Generic API response wrapper for successful responses."""

    data: T


class GenericApiPostResponse(Struct, tag_field="status", tag="success"):
    """Generic API response wrapper for successful POST requests."""


class GenericApiErrorResponse(Struct, tag_field="status", tag="error"):
    """Generic API response wrapper for error responses."""

    error: str


class DesyncInfo(Struct):
    """Information about town desynchronization."""

    towns: dict[str, float]
    last_updated: float


class DesyncResponse(Struct):
    """Response containing desync information."""

    status: int
    data: DesyncInfo


class TotalItemCountInfo(Struct):
    """Information about total item counts per town."""

    towns: dict[str, float]


class TotalItemCountResponse(Struct):
    """Response containing total item count information."""

    status: int
    data: TotalItemCountInfo


class CharInfo(Struct):
    """Character information including stats and inventory details."""

    name: str
    index: int
    user_name: str
    id: str
    channel_id: str
    channel_name: str
    desync_s: float
    last_update_time: float
    recommendations: list[str]
    total_item_count: int


class CharInfoResponse(Struct):
    """Response containing character information for multiple characters."""

    status: int
    data: list[CharInfo]
    error: str


class CharCoins(Struct):
    """Character coin information."""

    twitch_id: str
    user_name: str
    char_index: int
    coins: int


class CharCoinsResponse(Struct):
    """Response containing character coin information."""

    status: int
    data: list[CharCoins]


class CharItem(Struct):
    """Individual character item information."""

    id: str
    amount: int
    soulbound: bool
    equipped: bool


class CharItems(Struct):
    """Character items collection."""

    twitch_id: str
    user_name: str
    char_index: int
    items: list[CharItem]


class CharItemsResponse(Struct):
    """Response containing character items information."""

    status: str
    data: list[CharItems]


class ScrollCounts(Struct):
    """Counts of different scroll types."""

    exp_multiplier_scroll: int = field(name="Exp Multiplier Scroll")
    ferry_scroll: int = field(name="Ferry Scroll")
    raid_scroll: int = field(name="Raid Scroll")
    dungeon_scroll: int = field(name="Dungeon Scroll")


class Scrolls(Struct):
    """Scroll information for channel and total counts."""

    channel: ScrollCounts
    total: ScrollCounts


class ScrollsResponse(Struct):
    """Response containing scroll count information."""

    status: int
    data: Scrolls


class ErrorResponse(Struct):
    """Generic error response structure."""

    status: int
    error: str


class RavenfallTimeoutError(Exception):
    """Custom timeout exception for Ravenfall API requests."""


class RavenfallConnectionError(Exception):
    """Custom connection exception for Ravenfall API requests."""


class QueryException(BaseException):
    """Exception raised for query-related errors."""


class ServerError(Exception):
    """Exception raised for server-related errors."""


class RavenfallMultichatClient:
    """Client for interacting with the Ravenfall MultiChat HTTP API."""

    def __init__(self, base_url: str):
        """Initialize the client with the base URL of the MultiChat server.

        Args:
            base_url: The base URL of the MultiChat server (e.g., "http://localhost:8080")

        """
        self.base_url: str = base_url

    async def _get[T](
        self, url_suffix: str, _out_type: type[T], timeout_seconds: int = 3
    ) -> T:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_seconds)
        ) as session:
            try:
                async with session.get(
                    f"{self.base_url}/{url_suffix.lstrip('/')}"
                ) as response:
                    text = await response.text()
                    response_data = cast(
                        "GenericApiResponse[T] | GenericApiErrorResponse",
                        json.decode(
                            text,
                            type=GenericApiResponse[T] | GenericApiErrorResponse,
                        ),
                    )
                    if isinstance(response_data, GenericApiErrorResponse):
                        logger.error(f"Failed to fetch: {response_data.error}")
                        error_msg = f"Failed to fetch: {response_data.error}"
                        raise ServerError(error_msg)
                    response.raise_for_status()
                    return response_data.data
            except TimeoutError as e:
                logger.exception(f"Timeout fetching from {self.base_url}")
                raise RavenfallTimeoutError from e
            except aiohttp.ClientConnectorError as e:
                logger.exception(f"Connection error fetching from {self.base_url}")
                raise RavenfallConnectionError from e
            except Exception as e:
                logger.exception(f"Error fetching from {self.base_url}")
                raise

    async def _post(
        self,
        url_suffix: str,
        payload: dict[str, Any],  # pyright: ignore[reportExplicitAny]
        timeout_seconds: int = 3,
    ) -> None:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_seconds)
        ) as session:
            try:
                async with session.post(
                    f"{self.base_url}/{url_suffix.lstrip('/')}", json=payload
                ) as response:
                    text = await response.text()
                    response_data = cast(
                        "GenericApiPostResponse | GenericApiErrorResponse",
                        json.decode(
                            text, type=GenericApiPostResponse | GenericApiErrorResponse
                        ),
                    )
                    if isinstance(response_data, GenericApiErrorResponse):
                        logger.error(f"Failed to post: {response_data.error}")
                        error_msg = f"Failed to post: {response_data.error}"
                        raise ServerError(error_msg)
                    response.raise_for_status()
            except TimeoutError as e:
                logger.exception(f"Timeout posting to {self.base_url}")
                raise RavenfallTimeoutError from e
            except aiohttp.ClientConnectorError as e:
                logger.exception(f"Connection error posting to {self.base_url}")
                raise RavenfallConnectionError from e
            except Exception as e:
                logger.exception(f"Error posting to {self.base_url}")
                raise

    async def send_multichat_command(
        self,
        text: str,
        user_id: str = "example_user_id",
        user_name: str = "example_user",
        channel_id: str = "example_channel_id",
        channel_name: str = "example_channel",
        *,
        timeout_seconds: int = 3,
    ) -> None:
        """Send a command to the Ravenfall MultiChat server.

        Args:
            text: The command text to send (e.g., "?ping", "?sailall")
            user_id: The ID of the user sending the command
            user_name: The username of the user sending the command
            channel_id: The ID of the channel where the command should be processed
            channel_name: The name of the channel
            timeout_seconds: Timeout in seconds for the request

        """
        payload = {
            "text": text,
            "user_id": user_id,
            "user_name": user_name,
            "channel_id": channel_id,
            "channel_name": channel_name,
        }

        logger.debug(
            f"Sent command to multichat: {text}, {user_name}, {user_id}, {channel_name}, {channel_id}"
        )
        await self._post("command", payload, timeout_seconds=timeout_seconds)

    async def track_item_use(
        self,
        user_name: str,
        char_index: int,
        item_id: str,
        amount: int,
        *,
        timeout_seconds: int = 3,
    ) -> None:
        """Track item usage for a character.

        Args:
            user_name: The username of the character
            char_index: The character index
            item_id: The ID of the item used
            amount: The amount of items used
            timeout_seconds: Timeout in seconds for the request

        """
        payload = {
            "user_name": user_name,
            "char_index": char_index,
            "item_id": item_id,
            "amount": amount,
        }
        await self._post("track_item_use", payload, timeout_seconds=timeout_seconds)

    async def track_coin_use(
        self, user_name: str, amount: int, *, timeout_seconds: int = 3
    ) -> None:
        """Track coin usage for a character.

        Args:
            user_name: The username of the character
            amount: The amount of coins used
            timeout_seconds: Timeout in seconds for the request

        """
        payload = {"user_name": user_name, "amount": amount}
        await self._post("track_coin_use", payload, timeout_seconds=timeout_seconds)

    async def get_desync_info(self, *, timeout_seconds: int = 3) -> DesyncResponse:
        """Fetch desync information from the server.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            DesyncResponse: The response containing desync information

        """
        return await self._get(
            "get_desync", DesyncResponse, timeout_seconds=timeout_seconds
        )

    async def get_total_item_count(
        self, *, timeout_seconds: int = 3
    ) -> TotalItemCountResponse:
        """Fetch total item count from the server.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            TotalItemCountResponse: The response containing total item count information

        """
        return await self._get(
            "get_total_item_count",
            TotalItemCountResponse,
            timeout_seconds=timeout_seconds,
        )

    async def get_char_info(self, *, timeout_seconds: int = 3) -> CharInfoResponse:
        """Fetch character information from the server.

        Args:
            timeout_seconds: Timeout in seconds for the request

        Returns:
            CharInfoResponse: The response containing character information

        """
        return await self._get(
            "get_char_data", CharInfoResponse, timeout_seconds=timeout_seconds
        )

    async def get_char_items(
        self, channel_id: str, *, timeout_seconds: int = 3
    ) -> CharItemsResponse:
        """Fetch character items from the server.

        Args:
            channel_id: The channel ID to fetch items for
            timeout_seconds: Timeout in seconds for the request

        Returns:
            CharItemsResponse: The response containing character items

        """
        return await self._get(
            f"get_char_items/{channel_id}",
            CharItemsResponse,
            timeout_seconds=timeout_seconds,
        )

    async def get_char_coins(
        self, channel_id: str, *, timeout_seconds: int = 3
    ) -> CharCoinsResponse:
        """Fetch character coins from the server.

        Args:
            channel_id: The channel ID to fetch coins for
            timeout_seconds: Timeout in seconds for the request

        Returns:
            CharCoinsResponse: The response containing character coins

        """
        return await self._get(
            f"get_char_coins/{channel_id}",
            CharCoinsResponse,
            timeout_seconds=timeout_seconds,
        )

    async def get_scroll_counts(
        self, channel_id: str, *, timeout_seconds: int = 3
    ) -> ScrollsResponse:
        """Fetch scroll counts from the server.

        Args:
            channel_id: The channel ID to fetch scroll counts for
            timeout_seconds: Timeout in seconds for the request

        Returns:
            ScrollsResponse: The response containing scroll counts

        """
        return await self._get(
            f"get_scrolls/{channel_id}", ScrollsResponse, timeout_seconds=timeout_seconds
        )
