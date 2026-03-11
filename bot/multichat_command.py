"""
Example client for sending commands to the Ravenfall MultiChat server via HTTP.

This script demonstrates how to send commands to the chat bot using the HTTP endpoint.
Make sure the server is running and the COMMAND_SERVER_HOST and COMMAND_SERVER_PORT
environment variables are properly set.
"""
import aiohttp
import os
from typing import Any, TypedDict, NotRequired, Literal, cast
import logging

logger = logging.getLogger(__name__)

# Configuration - Update these values to match your setup
COMMAND_SERVER_HOST = os.getenv("MULTICHAT_COMMAND_SERVER_HOST", "localhost")
COMMAND_SERVER_PORT = int(os.getenv("MULTICHAT_COMMAND_SERVER_PORT", 8080))
BASE_URL = f"http://{COMMAND_SERVER_HOST}:{COMMAND_SERVER_PORT}"

async def get[T](url_suffix: str, t: type[T]) -> T:  # pyright: ignore[reportUnusedParameter]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/{url_suffix.lstrip('/')}") as response:
                response_data = cast(GenericApiResponse[T] | GenericApiErrorResponse, await response.json())
                if response_data["status"] != 'success':
                    raise ServerError(f"Failed to fetch: {response_data['error']}")
                response.raise_for_status()
                return response_data["data"]
    except Exception as e:
        logging.error(f"Failed to fetch: {str(e)}") 
        raise ServerError(f"Failed to fetch: {str(e)}")

async def post(url_suffix: str, payload: dict[str, Any]):  # pyright: ignore[reportExplicitAny]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{BASE_URL}/{url_suffix.lstrip('/')}", json=payload) as response:
                response_data = cast(GenericApiPostResponse | GenericApiErrorResponse, await response.json())
                if response_data["status"] != 'success':
                    raise ServerError(f"Failed to post: {response_data['error']}")
                response.raise_for_status()
    except Exception as e:
        logging.error(f"Failed to post: {str(e)}") 
        raise ServerError(f"Failed to post: {str(e)}")

async def send_multichat_command(
    text: str,
    user_id: str = "example_user_id",
    user_name: str = "example_user",
    channel_id: str = "example_channel_id",
    channel_name: str = "example_channel"
):
    """
    Send a command to the Ravenfall MultiChat server.
    
    Args:
        text: The command text to send (e.g., "?ping", "?sailall")
        user_id: The ID of the user sending the command
        user_name: The username of the user sending the command
        channel_id: The ID of the channel where the command should be processed
        channel_name: The name of the channel
        
    Returns:
        dict: The JSON response from the server
    """
    payload = {
        "text": text,
        "user_id": user_id,
        "user_name": user_name,
        "channel_id": channel_id,
        "channel_name": channel_name
    }
    
    logger.debug(f"Sent command to multichat: {text}, {user_name}, {user_id}, {channel_name}, {channel_id}")
    await post("command", payload)
        
async def track_item_use(
    user_name: str,
    char_index: int,
    item_id: str,
    amount: int,
):
    """Track item usage for a character.
    
    Args:
        user_name: The username of the character
        char_index: The character index
        item_id: The ID of the item used
        amount: The amount of items used
        
    Returns:
        dict: The JSON response from the server
    """
    payload = {
        "user_name": user_name,
        "char_index": char_index,
        "item_id": item_id,
        "amount": amount
    }
    await post("track_item_use", payload)

async def track_coin_use(
    user_name: str,
    amount: int,
):
    """Track coin usage for a character.
    
    Args:
        user_name: The username of the character
        amount: The amount of coins used
        
    Returns:
        dict: The JSON response from the server
    """
    payload = {
        "user_name": user_name,
        "amount": amount
    }
    await post("track_item_use", payload)

class GenericApiResponse[T](TypedDict):
    status: Literal['success']
    data: T

class GenericApiPostResponse(TypedDict):
    status: Literal['success']

class GenericApiErrorResponse(TypedDict):
    status: Literal['error']
    error: str

class DesyncInfo(TypedDict):
    towns: dict[str, float]  # Channel ID to desync data mapping
    last_updated: float  # Time since epoch

class DesyncResponse(TypedDict):
    status: int
    data: DesyncInfo

class TotalItemCountInfo(TypedDict):
    towns: dict[str, float]  # Channel ID to desync data mapping

class TotalItemCountResponse(TypedDict):
    status: int
    data: TotalItemCountInfo

class CharInfo(TypedDict):    
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
    
class CharInfoResponse(TypedDict):
    status: int
    data: list[CharInfo]
    error: str

class CharCoins(TypedDict):
    twitch_id: str
    user_name: str
    char_index: int
    coins: int

class CharCoinsResponse(TypedDict):
    status: int
    data: list[CharCoins]
    error: NotRequired[str]

class CharItem(TypedDict):
    id: str
    amount: int
    soulbound: bool
    equipped: bool

class CharItems(TypedDict):
    twitch_id: str
    user_name: str
    char_index: int
    items: list[CharItem]

class CharItemsResponse(TypedDict):
    status: Literal["success"]
    data: list[CharItems]

ScrollCounts = TypedDict('ScrollCounts', {
    "Exp Multiplier Scroll": int,
    "Ferry Scroll": int,
    "Raid Scroll": int,
    "Dungeon Scroll": int
})

class Scrolls(TypedDict):
    channel: ScrollCounts
    total: ScrollCounts

class ScrollsResponse(TypedDict):
    status: int 
    data: Scrolls

class ErrorResponse(TypedDict):
    status: int
    error: str

class ServerError(Exception):
    pass

async def get_desync_info() -> DesyncResponse:
    """Fetch desync information from the server.
    
    Returns:
        dict: The JSON response containing desync information
    """
    return await get("get_desync", DesyncResponse)


async def get_total_item_count() -> TotalItemCountResponse:
    """Fetch total item count from the server.
    
    Returns:
        dict: The JSON response containing total item count information
    """
    return await get("get_total_item_count", TotalItemCountResponse)

async def get_char_info() -> CharInfoResponse:
    """Fetch character information from the server.
    
    Returns:
        dict: The JSON response containing character information
    """
    return await get("get_char_data", CharInfoResponse)

async def get_char_items(channel_id: str) -> CharItemsResponse:
    """Fetch character items from the server.
    
    Args:
        channel_id: The channel ID to fetch items for
    
    Returns:
        dict: The JSON response containing character items
    """
    return await get(f"get_char_items/{channel_id}", CharItemsResponse)

async def get_char_coins(channel_id: str) -> CharCoinsResponse:
    """Fetch character coins from the server.
    
    Args:
        channel_id: The channel ID to fetch coins for
    
    Returns:
        dict: The JSON response containing character coins
    """
    return await get(f"get_char_coins/{channel_id}", CharCoinsResponse)

async def get_scroll_counts(channel_id: str) -> ScrollsResponse:
    """Fetch scroll counts from the server.
    
    Args:
        channel_id: The channel ID to fetch scroll counts for
    
    Returns:
        dict: The JSON response containing scroll counts
    """
    return await get(f"get_scrolls/{channel_id}", ScrollsResponse)

