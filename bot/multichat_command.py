"""
Client for the Ravenfall MultiChat server's HTTP API.

The server is set by MULTICHAT_COMMAND_SERVER_HOST and MULTICHAT_COMMAND_SERVER_PORT.
"""
from typing import Dict, Any, List, TypedDict, Optional
import logging

from .clients.session import get_session
from .config import get_settings

logger = logging.getLogger(__name__)

async def _post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    async with get_session().post(f"{get_settings().multichat_url}/{path}", json=payload) as response:
        response_data = await response.json()
        return {
            "status": response.status,
            "data": response_data
        }

async def _get(path: str) -> Dict[str, Any]:
    async with get_session().get(f"{get_settings().multichat_url}/{path}") as response:
        response_data = await response.json()
        return {
            "status": response.status,
            "data": response_data
        }

async def send_multichat_command(
    text: str,
    user_id: str = "example_user_id",
    user_name: str = "example_user",
    channel_id: str = "example_channel_id",
    channel_name: str = "example_channel"
) -> Dict[str, Any]:
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
    
    try:
        result = await _post("command", payload)
        logger.debug(f"Sent command to multichat: {text}, {user_name}, {user_id}, {channel_name}, {channel_id}")
        return result
    except Exception as e:
        logger.error(f"Failed to send command: {str(e)}", exc_info=True)
        return {
            "status": 500,
            "error": f"Failed to send command: {str(e)}"
        }
        
async def track_item_use(
    user_name: str,
    char_index: int,
    item_id: str,
    amount: int,
) -> Dict[str, Any]:
    payload = {
        "user_name": user_name,
        "char_index": char_index,
        "item_id": item_id,
        "amount": amount
    }
    try:
        return await _post("track_item_use", payload)
    except Exception as e:
        logger.error(f"Failed to send command: {str(e)}", exc_info=True)
        return {
            "status": 500,
            "error": f"Failed to send command: {str(e)}"
        }

async def track_coin_use(
    user_name: str,
    amount: int,
) -> Dict[str, Any]:
    payload = {
        "user_name": user_name,
        "amount": amount
    }
    try:
        return await _post("track_coin_use", payload)
    except Exception as e:
        logger.error(f"Failed to send command: {str(e)}", exc_info=True)
        return {
            "status": 500,
            "error": f"Failed to send command: {str(e)}"
        }


class DesyncInfo(TypedDict):
    towns: Dict[str, float]  # Channel ID to desync data mapping
    last_updated: float  # Time since epoch

class DesyncResponse(TypedDict):
    status: int
    data: DesyncInfo
    error: Optional[str]

class TotalItemCountInfo(TypedDict):
    towns: Dict[str, float]  # Channel ID to desync data mapping

class TotalItemCountResponse(TypedDict):
    status: int
    data: TotalItemCountInfo
    error: Optional[str]

class CharInfo(TypedDict):    
    name: str
    index: int
    user_name: str
    id: str
    channel_id: str
    channel_name: str
    desync_s: float
    last_update_time: float
    recommendations: List[str]
    total_item_count: int
    
class CharInfoResponse(TypedDict):
    status: int
    data: List[CharInfo]
    error: Optional[str]

class CharCoins(TypedDict):
    twitch_id: str
    user_name: str
    char_index: int
    coins: int

class CharCoinsResponse(TypedDict):
    status: int
    data: List[CharCoins]
    error: Optional[str]

class CharItem(TypedDict):
    id: str
    amount: int
    soulbound: bool
    equipped: bool

class CharItems(TypedDict):
    twitch_id: str
    user_name: str
    char_index: int
    items: List[CharItem]

class CharItemsResponse(TypedDict):
    status: int
    data: List[CharItems]
    error: Optional[str]

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

async def get_desync_info() -> DesyncResponse:
    """Fetch desync information from the server.
    
    Returns:
        dict: The JSON response containing desync information
    """
    try:
        result = await _get("get_desync")
        return {
            "status": result["status"],
            "data": result["data"]["data"]
        }
    except Exception as e:
        return {
            "status": 500,
            "error": f"Failed to fetch: {str(e)}"
        }

async def get_total_item_count() -> TotalItemCountResponse:
    """Fetch total item count from the server.
    
    Returns:
        dict: The JSON response containing desync information
    """
    try:
        result = await _get("get_total_item_count")
        return {
            "status": result["status"],
            "data": result["data"]["data"]
        }
    except Exception as e:
        return {
            "status": 500,
            "error": f"Failed to fetch: {str(e)}"
        }

async def get_char_info() -> CharInfoResponse:
    try:
        result = await _get("get_char_data")
        return {
            "status": result["status"],
            "data": result["data"]["data"]
        }
    except Exception as e:
        return {
            "status": 500,
            "error": f"Failed to fetch: {str(e)}"
        }

async def get_char_items(channel_id: str) -> CharItemsResponse:
    try:
        result = await _get(f"get_char_items/{channel_id}")
        return {
            "status": result["status"],
            "data": result["data"]["data"]
        }
    except Exception as e:
        return {
            "status": 500,
            "error": f"Failed to fetch: {str(e)}"
        }

async def get_char_coins(channel_id: str) -> CharCoinsResponse:
    try:
        result = await _get(f"get_char_coins/{channel_id}")
        return {
            "status": result["status"],
            "data": result["data"]["data"]
        }
    except Exception as e:
        return {
            "status": 500,
            "error": f"Failed to fetch: {str(e)}"
        }

async def get_scroll_counts(channel_id: str) -> ScrollsResponse:
    try:
        return await _get(f"get_scrolls/{channel_id}")
    except Exception as e:
        return {
            "status": 500,
            "error": f"Failed to fetch: {str(e)}"
        }

