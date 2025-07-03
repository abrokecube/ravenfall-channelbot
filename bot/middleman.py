import asyncio
import json
import logging
import os
import aiohttp
from typing import Dict, Optional, Tuple, Any, TypedDict
from dataclasses import dataclass

# Configuration
MIDDLEMAN_API_HOST = os.getenv('RF_MIDDLEMAN_HOST', None)
MIDDLEMAN_API_PORT = os.getenv('RF_MIDDLEMAN_PORT', None)

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
    logger.debug(f"Sending message to client: {message}")
    response, status = await _call_middleman_api('/api/send-to-client', 'POST', data)
    return response

async def send_to_server(connection_id: str, message: str) -> Dict:
    """Send a message to the server through a specific connection."""
    data = {
        "connectionId": connection_id,
        "data": message
    }
    logger.debug(f"Sending message to server: {message}")
    response, status = await _call_middleman_api('/api/send-to-server', 'POST', data)
    return response


async def send_to_server_and_wait_response(connection_id: str, message: str, correlation_id: str = "", timeout: int = 30) -> Dict:
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
    logger.debug(f"Sending message to server and waiting for response: {message}")    
    if correlation_id:
        data["correlationId"] = correlation_id
        
    response, status = await _call_middleman_api('/api/send-and-wait-response', 'POST', data)
    logger.debug(f"Response from server: {response}")
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

@dataclass
class ConnectionStatus:
    connection_id: str = ""
    client_connected: bool = False
    server_connected: bool = False
    time_until_close: int = -1


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
    
    # Convert camelCase keys from API to snake_case for our class
    status_dict = response.get('status', {})
    status_data = ConnectionStatus(
        connection_id=status_dict.get('connectionId', ''),
        client_connected=status_dict.get('clientConnected', False),
        server_connected=status_dict.get('serverConnected', False),
        time_until_close=status_dict.get('timeUntilClose', 0)
    )
    return status_data, None

class ServerConfig(TypedDict):
    """Type definition for server configuration."""
    enableMessageLogging: bool
    disableTimeout: bool
    defaultTimeoutSeconds: int
    noIdentifierTimeoutSeconds: int
    apiPort: int
    identifier_timeouts: dict[str, int]
    proxy_mappings: list[dict[str, Any]]
    messageProcessor: dict[str, Any]


async def get_config() -> tuple[ServerConfig | None, str | None]:
    """
    Get the server configuration.
    
    Returns:
        Tuple of (ServerConfig, error_message). If successful, error_message is None.
        On error, ServerConfig is None and error_message contains the error.
    """
    response, status = await _call_middleman_api('/api/config', 'GET')
    
    if status != 200:
        return None, response.get('error', 'Unknown error')
    
    if not response.get('success', False):
        return None, response.get('error', 'Failed to get config')
    
    return response.get('config'), None

