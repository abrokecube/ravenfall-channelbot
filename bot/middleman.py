import asyncio
import json
import logging
import os
import aiohttp
from typing import Dict, Optional, Tuple

# Configuration
MIDDLEMAN_API_HOST = os.getenv('RF_MIDDLEMAN_HOST', None)
MIDDLEMAN_API_PORT = os.getenv('RF_MIDDLEMAN_PORT', None)

power_saving = os.getenv("RF_MIDDLEMAN_POWER_SAVING", "false")
if power_saving.lower() == "true":
    POWER_SAVING = True
else:
    POWER_SAVING = False

# Configure logging
logger = logging.getLogger('middleman')

# Global variable to control the server loop
stop_event = asyncio.Event()

def handle_sigint():
    """Handle Ctrl+C signal to gracefully shut down the server."""
    logger.info("Shutting down server...")
    stop_event.set()

async def _call_middleman_api(endpoint: str, method: str = 'GET', data: Optional[Dict] = None) -> Tuple[Dict, int]:
    """Make an API call to the middleman server."""
    url = f"http://{MIDDLEMAN_API_HOST.rstrip('/')}:{MIDDLEMAN_API_PORT}/{endpoint.lstrip('/')}"
    headers = {'Content-Type': 'application/json'}
    
    logger.debug(f"API Request: {method} {url}")
    
    try:
        async with aiohttp.ClientSession() as session:
            if method.upper() == 'GET':
                async with session.get(url, headers=headers) as response:
                    response_data = await response.json()
                    # logger.debug(f"API Response (Status: {response.status}): {json.dumps(response_data)}")
                    return response_data, response.status
            else:
                async with session.post(url, json=data, headers=headers) as response:
                    response_data = await response.json()
                    # logger.debug(f"API Response (Status: {response.status}): {json.dumps(response_data)}")
                    return response_data, response.status
    except Exception as e:
        logger.error(f"Error calling middleman API: {str(e)}", exc_info=True)
        return {"error": f"Failed to connect to middleman API: {str(e)}"}, 500

async def force_reconnect(connection_id: str, timeout: int = 0) -> Dict:
    """Force a reconnection for the specified connection."""
    data = {
        "connectionId": connection_id,
        "timeout": timeout
    }
    response, status = await _call_middleman_api('/api/reconnect', 'POST', data)
    return response

async def send_to_client(connection_id: str, message: str) -> Dict:
    """Send a message to a specific client."""
    data = {
        "connectionId": connection_id,
        "data": message
    }
    response, status = await _call_middleman_api('/api/send-to-client', 'POST', data)
    return response

async def send_to_server(connection_id: str, message: str) -> Dict:
    """Send a message to the server through a specific connection."""
    data = {
        "connectionId": connection_id,
        "data": message
    }
    response, status = await _call_middleman_api('/api/send-to-server', 'POST', data)
    return response


async def send_and_wait_response(connection_id: str, message: str, correlation_id: str = "", timeout: int = 30) -> Dict:
    """
    Send a message to the server and wait for a response with the given correlation ID.
    
    Args:
        connection_id: The connection ID to send the message through
        message: The message to send to the server
        correlation_id: Optional correlation ID to match the response. If not provided, one will be generated.
        timeout: Maximum time in seconds to wait for a response (default: 30)
        
    Returns:
        Dict containing the response data or error information
    """
    data = {
        "connectionId": connection_id,
        "data": message,
        "timeout": timeout
    }
    
    if correlation_id:
        data["correlationId"] = correlation_id
        
    response, status = await _call_middleman_api('/api/send-and-wait-response', 'POST', data)
    return response

async def ensure_connected(connection_id: str, timeout: int = 0) -> Dict:
    """
    Ensure the connection to the server is active.
    
    Args:
        connection_id: The connection ID to check/ensure
        timeout: Optional timeout in seconds for the connection (0 for default)
        
    Returns:
        Dict containing the result of the operation
    """
    data = {
        "connectionId": connection_id,
        "timeout": timeout
    }
    response, status = await _call_middleman_api('/api/ensure-connected', 'POST', data)
    return response

class ConnectionStatus(TypedDict):
    """Type definition for connection status response."""
    connectionId: str
    clientConnected: bool
    serverConnected: bool
    timeUntilClose: int  # seconds until disconnect, -1 if no timeout set


async def get_connection_status(connection_id: str) -> tuple[ConnectionStatus | None, str | None]:
    """
    Get the status of a connection.
    
    Args:
        connection_id: The ID of the connection to check
        
    Returns:
        Tuple of (ConnectionStatus, error_message). If successful, error_message is None.
        On error, ConnectionStatus is None and error_message contains the error.
    """
    response, status = await _call_middleman_api(f'/api/connection-status?connectionId={connection_id}', 'GET')
    
    if status != 200:
        return None, response.get('error', 'Unknown error')
    
    if not response.get('success', False):
        return None, response.get('error', 'Failed to get connection status')
    
    status_data: ConnectionStatus = response.get('status', {})
    return status_data, None
