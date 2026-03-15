import logging
from types import NoneType
import aiohttp
from typing import Any, overload
from msgspec import Struct, field, json

# Configure logging
logger = logging.getLogger('middleman')
json_encode = json.Encoder()


class Sender(Struct):
    id: str = field(name="Id")
    character_id: str = field(name="CharacterId")
    username: str = field(name="Username")
    display_name: str = field(name="DisplayName")
    color: str = field(name="Color")
    platform: str = field(name="Platform")
    platform_id: str | None = field(name="PlatformId")
    is_broadcaster: bool = field(name="IsBroadcaster")
    is_moderator: bool = field(name="IsModerator")
    is_subscriber: bool = field(name="IsSubscriber")
    is_vip: bool = field(name="IsVip")
    is_game_administrator: bool = field(name="IsGameAdministrator")
    is_game_moderator: bool = field(name="IsGameModerator")
    sub_tier: int = field(name="SubTier")
    identifier: str = field(name="Identifier")

class RavenBotMessage(Struct):
    identifier: str = field(name="Identifier")
    sender: Sender = field(name="Sender")
    content: str = field(name="Content")
    correlation_id: str = field(name="CorrelationId")

class Recipient(Struct):
    """Represents the recipient information in a Ravenfall message."""
    user_id: str = field(name="UserId")
    character_id: str = field(name="CharacterId")
    platform: str = field(name="Platform")
    platform_id: str = field(name="PlatformId")
    platform_user_name: str = field(name="PlatformUserName")

class RavenfallMessage(Struct):
    """Represents a message received from Ravenfall."""
    identifier: str = field(name="Identifier")
    recipient: Recipient = field(name="Recipent")  # this typo is intentional
    format: str = field(name="Format")
    args: list[str | int | float] = field(name="Args")
    tags: list[str] = field(name="Tags")
    category: str = field(name="Category")
    correlation_id: str = field(name="CorrelationId")

class SendAndWaitResult(Struct):
    success: bool
    correlation_id: str = field(name="correlationId")
    responses: list[RavenfallMessage]
    complete: bool
    count: int
    expected_count: int = field(name="expectedCount")
    timeout: bool

class EnsureConnectionResult(Struct):
    success: bool
    message: str
    reconnected: bool
    connected: bool

class ConnectionStatus(Struct):
    connection_id: str = field(name="connectionId")
    client_connected: bool = field(name="clientConnected")
    server_connected: bool = field(name="serverConnected")
    time_until_close: int = field(name="timeUntilClose")

class ConnStatusResponse(Struct):
    success: bool
    status: ConnectionStatus

class ProxyMapping(Struct):
    client_port: int = field(name="clientPort")
    server_host: str = field(name="serverHost")
    server_port: int = field(name="serverPort")

class MessageProcessorConfig(Struct):
    enabled: bool
    url: str

class MiddlemanConfig(Struct):
    """Type definition for server configuration."""
    enableMessageLogging: bool
    disableTimeout: bool
    defaultTimeoutSeconds: int
    noIdentifierTimeoutSeconds: int
    apiPort: int
    identifierTimeouts: dict[str, int]
    proxyMappings: list[ProxyMapping]
    messageProcessor: MessageProcessorConfig

class ClientError(BaseException):
    pass

class MiddlemanError(BaseException):
    pass

class MiddlemanClient:
    def __init__(self, base_url: str):
        self.base_url: str = base_url.rstrip("/")

    def _raise_on_code(self, code: int, response_data: Any):
        if not isinstance(response_data, dict):
            raise ClientError(f"Invalid response from middleman API: {response_data}")
        if code == 400:
            raise ClientError(f"Middleman API returned error: {response_data}")
        elif code == 404:
            raise ClientError(f"Middleman API returned error: {response_data}")
        elif code == 500:
            raise MiddlemanError(f"Middleman API returned error: {response_data}")
        elif code == 200:
            pass
        else:
            raise ClientError(f"Middleman API returned error: {response_data}")

    @overload
    async def _get(self, endpoint: str, out_type: None = None) -> None: ...
    @overload
    async def _get[T](self, endpoint: str, out_type: type[T] = NoneType) -> T: ...

    async def _get[T](self, endpoint: str, out_type: type[T] | None = NoneType) -> T | None:
        headers = {'Content-Type': 'application/json'}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/{endpoint}", headers=headers) as response:
                response_text = await response.text()
                self._raise_on_code(response.status, response_text)
            if isinstance(out_type, NoneType):
                return None
            else:
                out_data = json.decode(response_text, type=out_type)
                return out_data

    @overload
    async def _post(self, endpoint: str, out_type: None = None, data: dict[str, Any] | None = None) -> None: ...
    @overload
    async def _post[T](self, endpoint: str, out_type: type[T] = NoneType, data: dict[str, Any] | None = None) -> T: ...
    
    async def _post[T](self, endpoint: str, out_type: type[T] | None = NoneType, data: dict[str, Any] | None = None) -> T | None:
        headers = {'Content-Type': 'application/json'}
        async with aiohttp.ClientSession() as session:
            encoded = json_encode.encode(data)
            async with session.post(f"{self.base_url}/{endpoint}", data=encoded, headers=headers) as response:
                response_text = await response.text()
                self._raise_on_code(response.status, response_text)
            if isinstance(out_type, NoneType):
                return None
            else:
                out_data = json.decode(response_text, type=out_type)
                return out_data

    async def force_reconnect(self, connection_id: str, timeout: int = 0) -> None:
        """
        Force the middleman to reconnect to Ravenfall.
        
        Args:
            connection_id: The connection ID to reconnect
            timeout: Timeout in seconds for the request
        """
        data = {
            "connectionId": connection_id,
            "timeout": timeout
        }
        await self._post('/api/reconnect', None, data)

    async def send_to_ravenbot(self, connection_id: str, message: RavenBotMessage) -> None:
        """
        Send RavenBot a message.
        
        Args:
            connection_id: The connection ID to send the message to
            message: The message to send
        """
        data = {
            "connectionId": connection_id,
            "data": json_encode.encode(message)
        }
        await self._post('/api/send-to-client', None, data)

    async def send_to_ravenfall(self, connection_id: str, message: RavenfallMessage) -> None:
        """
        Send Ravenfall a message.
        
        Args:
            connection_id: The connection ID to send the message to
            message: The message to send
        """
        data = {
            "connectionId": connection_id,
            "data": json_encode.encode(message)
        }
        await self._post('/api/send-to-server', None, data)


    async def send_to_ravenfall_and_wait_for_response(self, connection_id: str, message: SendAndWaitResult) -> SendAndWaitResult:
        """
        Send Ravenfall a message and wait for a response.
        
        Args:
            connection_id: The connection ID to send the message to
            message: The message to send
        """
        data = {
            "connectionId": connection_id,
            "data": json_encode.encode(message)
        }
        response = await self._post('/api/send-to-server-and-wait', SendAndWaitResult, data)
        return response

    async def ensure_connection(self, connection_id: str, timeout_extension: int = 30) -> EnsureConnectionResult:
        """
        Ensure the connection is active by extending its timeout.
        
        Args:
            connection_id: The connection ID to extend
            timeout_extension: The timeout extension in seconds (default: 30)
        """
        data = {
            "connectionId": connection_id,
            "timeout": timeout_extension
        }
        response = await self._post('/api/ensure-connected', EnsureConnectionResult, data)
        return response

    async def get_connection_status(self, connection_id: str) -> ConnStatusResponse:
        """
        Get the status of a connection.
        
        Args:
            connection_id: The connection ID to get the status for
        """
        response = await self._get('/api/connection-status?connectionId=' + connection_id, ConnStatusResponse)
        return response

    async def get_config(self) -> MiddlemanConfig:
        """
        Get the configuration of the middleman.
        """
        response = await self._get('/api/config', MiddlemanConfig)
        return response
