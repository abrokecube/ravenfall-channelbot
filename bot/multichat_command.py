"""
Example client for sending commands to the Ravenfall MultiChat server via HTTP.

This script demonstrates how to send commands to the chat bot using the HTTP endpoint.
Make sure the server is running and the COMMAND_SERVER_HOST and COMMAND_SERVER_PORT
environment variables are properly set.
"""
import aiohttp
import os
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

# Configuration - Update these values to match your setup
COMMAND_SERVER_HOST = os.getenv("MULTICHAT_COMMAND_SERVER_HOST", "localhost")
COMMAND_SERVER_PORT = int(os.getenv("MULTICHAT_COMMAND_SERVER_PORT", "8080"))
BASE_URL = f"http://{COMMAND_SERVER_HOST}:{COMMAND_SERVER_PORT}"

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
    url = f"{BASE_URL}/command"
    payload = {
        "text": text,
        "user_id": user_id,
        "user_name": user_name,
        "channel_id": channel_id,
        "channel_name": channel_name
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                response_data = await response.json()
                logger.debug(f"Sent command to multichat: {text}, {user_name}, {user_id}, {channel_name}, {channel_id}")
                return {
                    "status": response.status,
                    "data": response_data
                }
    except Exception as e:
        logger.error(f"Failed to send command: {str(e)}", exc_info=True)
        return {
            "status": 500,
            "error": f"Failed to send command: {str(e)}"
        }
