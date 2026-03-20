import logging
from types import NoneType
import aiohttp
from aiohttp import web
from typing import Any, overload, Callable, cast, Final
from collections.abc import Awaitable
from msgspec import Struct, field, json
from datetime import datetime
import asyncio
from utils.logging_fomatter import setup_logging
from enum import StrEnum

# Configure logging
logger = logging.getLogger('ravenfall_middleman')
json_encode = json.Encoder()
setup_logging()

class Sender(Struct):
    id: str = field(name="Id")
    character_id: str = field(name="CharacterId")
    username: str = field(name="Username")
    display_name: str = field(name="DisplayName")
    color: str | None = field(name="Color")
    platform: str | None = field(name="Platform")
    platform_id: str = field(name="PlatformId")
    is_broadcaster: bool = field(name="IsBroadcaster")
    is_moderator: bool = field(name="IsModerator")
    is_subscriber: bool = field(name="IsSubscriber")
    is_vip: bool = field(name="IsVip")
    is_game_administrator: bool = field(name="IsGameAdministrator")
    is_game_moderator: bool = field(name="IsGameModerator")
    sub_tier: int = field(name="SubTier")
    identifier: str | None = field(name="Identifier")

class RavenBotMessage(Struct):
    identifier: str = field(name="Identifier")
    sender: Sender = field(name="Sender")
    content: str = field(name="Content")
    correlation_id: str | None = field(name="CorrelationId")

class Recipient(Struct):
    """Represents the recipient information in a Ravenfall message."""
    user_id: str = field(name="UserId")
    character_id: str = field(name="CharacterId")
    platform: str | None = field(name="Platform")
    platform_id: str = field(name="PlatformId")
    platform_user_name: str = field(name="PlatformUserName")

class RavenfallMessage(Struct):
    """Represents a message received from Ravenfall."""
    identifier: str = field(name="Identifier")
    recipient: Recipient = field(name="Recipent")  # this typo is intentional
    format: str = field(name="Format")
    args: list[str | int | float | dict[str, Any]] = field(name="Args")
    tags: list[str] = field(name="Tags")
    category: str | None = field(name="Category")
    correlation_id: str | None = field(name="CorrelationId")

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

class StreamMessageBase(Struct):
    client_addr: str = field(name="clientAddr")
    server_addr: str = field(name="serverAddr")
    connection_id: str = field(name="connectionId")
    correlation_id: str = field(name="correlationId")
    is_api: bool = field(name="isApi")
    timestamp: datetime

class MessageOrigin(StrEnum):
    RAVENFALL = "SERVER"
    RAVENBOT = "CLIENT"
    API_RAVENFALL = "API-SERVER"
    API_RAVENBOT = "API-CLIENT"

class RavenfallStreamMessage(StreamMessageBase, tag_field="source", tag="SERVER"):
    message: RavenfallMessage
    origin: MessageOrigin = MessageOrigin.RAVENFALL

class RavenBotStreamMessage(StreamMessageBase, tag_field="source", tag="CLIENT"):
    message: RavenBotMessage
    origin: MessageOrigin = MessageOrigin.RAVENBOT

class RavenfallApiStreamMessage(RavenfallStreamMessage, tag="API-SERVER"):
    origin: MessageOrigin = MessageOrigin.API_RAVENFALL

class RavenBotApiStreamMessage(RavenBotStreamMessage, tag="API-CLIENT"):
    origin: MessageOrigin = MessageOrigin.API_RAVENBOT

StreamMessageType = RavenfallStreamMessage | RavenBotStreamMessage | RavenfallApiStreamMessage | RavenBotApiStreamMessage

class ClientError(BaseException):
    pass

class MiddlemanError(BaseException):
    pass

class MiddlemanClient:
    def __init__(self, base_url: str) -> None:
        self.base_url: str = base_url.rstrip("/")
        self._websocket: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._ravenbot_message_hooks: list[Callable[[RavenBotStreamMessage], Awaitable[None]]] = []
        self._ravenfall_message_hooks: list[Callable[[RavenfallStreamMessage], Awaitable[None]]] = []
        self._ws_task: asyncio.Task[None] | None = None
        self._connected: bool = False

    def _raise_on_code(self, code: int, response_data: Any) -> None:
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
    async def _get[T](self, endpoint: str, out_type: type[T] = NoneType) -> T: ...  # ty:ignore[invalid-parameter-default]  # pyrefly: ignore[bad-function-definition]

    async def _get[T](self, endpoint: str, out_type: type[T] | None = None) -> T | None:
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
    async def _post[T](self, endpoint: str, out_type: type[T] = NoneType, data: dict[str, Any] | None = None) -> T: ...  # ty:ignore[invalid-parameter-default]  # pyrefly: ignore[bad-function-definition]
    
    async def _post[T](self, endpoint: str, out_type: type[T] | None = None, data: dict[str, Any] | None = None) -> T | None:
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

    def add_ravenfall_message_hook(self, hook: Callable[[RavenfallStreamMessage], Awaitable[None]]) -> None:
        """
        Add a hook function to receive WebSocket messages.
        
        Args:
            hook: An async function that takes a RavenfallStreamMessage instance
        """
        self._ravenfall_message_hooks.append(hook)

    def remove_ravenfall_message_hook(self, hook: Callable[[RavenfallStreamMessage], Awaitable[None]]) -> None:
        """
        Remove a message hook function.
        
        Args:
            hook: The hook function to remove
        """
        if hook in self._ravenfall_message_hooks:
            self._ravenfall_message_hooks.remove(hook)

    def add_ravenbot_message_hook(self, hook: Callable[[RavenBotStreamMessage], Awaitable[None]]) -> None:
        """
        Add a hook function to receive WebSocket messages.
        
        Args:
            hook: An async function that takes a RavenBotStreamMessage instance
        """
        self._ravenbot_message_hooks.append(hook)

    def remove_ravenbot_message_hook(self, hook: Callable[[RavenBotStreamMessage], Awaitable[None]]) -> None:
        """
        Remove a message hook function.
        
        Args:
            hook: The hook function to remove
        """
        if hook in self._ravenbot_message_hooks:
            self._ravenbot_message_hooks.remove(hook)

    async def connect_websocket(self) -> None:
        """
        Connect to the middleman's WebSocket stream.
        """
        if self._connected:
            logger.warning("WebSocket is already connected")
            return

        ws_url = self.base_url.replace('http://', 'ws://').replace('https://', 'wss://') + '/ws'
        
        try:
            self._session = aiohttp.ClientSession()
            self._websocket = await self._session.ws_connect(ws_url)
            self._connected = True
            logger.info(f"Connected to WebSocket at {ws_url}")
            
            # Start the message receiving task
            self._ws_task = asyncio.create_task(self._receive_messages())
            
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {e}")
            if self._session:
                await self._session.close()
                self._session = None
            raise MiddlemanError(f"WebSocket connection failed: {e}")

    async def disconnect_websocket(self) -> None:
        """
        Disconnect from the WebSocket stream.
        """
        if not self._connected:
            logger.warning("WebSocket is not connected")
            return

        self._connected = False
        
        if self._ws_task:
            _ = self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        if self._websocket:
            _ = await self._websocket.close()
            self._websocket = None

        if self._session:
            await self._session.close()
            self._session = None
            
        logger.info("Disconnected from WebSocket")

    async def _receive_messages(self) -> None:
        """
        Internal method to receive and process messages from the WebSocket.
        """
        try:
            while self._connected and self._websocket:
                message_data_str: bytes = b''
                try:
                    message = await self._websocket.receive()
                    
                    if message.type == aiohttp.WSMsgType.TEXT:
                        message_data_str = cast(bytes, message.data)
                        parsed_message = json.decode(
                            message_data_str, 
                            type=StreamMessageType
                        )
                        
                        if isinstance(parsed_message, RavenBotStreamMessage):
                            for hook in self._ravenbot_message_hooks:
                                try:
                                    await hook(parsed_message)
                                except Exception as e:
                                    logger.error(f"Error in message hook: {e}")
                        else:
                            for hook in self._ravenfall_message_hooks:
                                try:
                                    await hook(parsed_message)
                                except Exception as e:
                                    logger.error(f"Error in message hook: {e}")
                    
                    elif message.type == aiohttp.WSMsgType.ERROR:
                        logger.error(f"WebSocket error: {self._websocket.exception()}")
                        break
                    
                    elif message.type == aiohttp.WSMsgType.CLOSE:
                        logger.info("WebSocket connection closed")
                        break
                        
                except Exception as e:
                    logger.error(f"Error receiving WebSocket message: {e}")
                    logger.error(f"Message data: {message_data_str}")
                    # break
                    
        except asyncio.CancelledError:
            logger.info("WebSocket message receiver task cancelled")
        finally:
            self._connected = False

    @property
    def is_websocket_connected(self) -> bool:
        """
        Check if the WebSocket is connected.
        """
        return self._connected and self._websocket is not None and not self._websocket.closed


class FrozenSender(Sender, frozen=True):  # pyright: ignore[reportGeneralTypeIssues]  # ty:ignore[invalid-frozen-dataclass-subclass]
    pass

class FrozenRavenBotMessage(Struct, frozen=True):
    identifier: str = field(name="Identifier")
    sender: FrozenSender = field(name="Sender")
    content: str = field(name="Content")
    correlation_id: str | None = field(name="CorrelationId")

class FrozenRecipient(Recipient, frozen=True):  # pyright: ignore[reportGeneralTypeIssues]  # ty:ignore[invalid-frozen-dataclass-subclass]
    pass

class FrozenRavenfallMessage(Struct, frozen=True):
    """Represents a message received from Ravenfall."""
    identifier: str = field(name="Identifier")
    recipient: FrozenRecipient = field(name="Recipent")  # this typo is intentional
    format: str = field(name="Format")
    args: list[str | int | float | dict[str, Any]] = field(name="Args")
    tags: list[str] = field(name="Tags")
    category: str | None = field(name="Category")
    correlation_id: str | None = field(name="CorrelationId")

class ProcessorMessageBase(Struct, kw_only=True):
    client_addr: Final[str] = field(name="clientAddr")
    server_addr: Final[str] = field(name="serverAddr")
    connection_id: Final[str] = field(name="connectionId")
    correlation_id: Final[str] = field(name="correlationId")
    is_api: Final[bool] = field(name="isApi")
    timestamp: Final[datetime]
    _block: bool = False

    def block(self):
        self._block = True

class RavenfallProcessorMessage(ProcessorMessageBase, tag_field="source", tag="SERVER"):
    message: RavenfallMessage
    original_message: Final[FrozenRavenfallMessage] = field(name="originalMessage")
    origin: Final[MessageOrigin] = MessageOrigin.RAVENFALL

class RavenBotProcessorMessage(ProcessorMessageBase, tag_field="source", tag="CLIENT"):
    message: RavenBotMessage
    original_message: Final[FrozenRavenBotMessage] = field(name="originalMessage")
    origin: Final[MessageOrigin] = MessageOrigin.RAVENBOT

class RavenfallApiProcessorMessage(RavenfallProcessorMessage, tag_field="source", tag="API-SERVER"):
    origin: Final[MessageOrigin] = MessageOrigin.RAVENFALL  # pyright: ignore[reportGeneralTypeIssues]  # ty:ignore[override-of-final-variable]  # pyrefly:ignore[bad-override]

class RavenBotApiProcessorMessage(RavenBotProcessorMessage, tag_field="source", tag="API-CLIENT"):
    origin: Final[MessageOrigin] = MessageOrigin.RAVENBOT  # pyright: ignore[reportGeneralTypeIssues]  # ty:ignore[override-of-final-variable]  # pyrefly:ignore[bad-override]

ProcessorMessageType = RavenfallProcessorMessage | RavenBotProcessorMessage | RavenfallApiProcessorMessage | RavenBotApiProcessorMessage

class MessageProcessorServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 9000):
        self.host: str = host
        self.port: int = port
        self._app: web.Application = web.Application()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._ravenbot_message_hooks: list[Callable[[RavenBotProcessorMessage], Awaitable[None]]] = []
        self._ravenfall_message_hooks: list[Callable[[RavenfallProcessorMessage], Awaitable[None]]] = []
        self._setup_routes()

    def _setup_routes(self) -> None:
        __ = self._app.router.add_get("/process", self._websocket_handler)

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        __ = await ws.prepare(request)
        logger.info(f"Message processor client connected from {request.remote}")

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    message_data = msg.data  # pyright: ignore[reportAny]
                    try:
                        parsed_message = json.decode(
                            message_data,  # pyright: ignore[reportAny]
                            type=ProcessorMessageType
                        )
                        if isinstance(parsed_message, RavenBotProcessorMessage):
                            for hook in self._ravenbot_message_hooks:
                                try:
                                    await hook(parsed_message)
                                except Exception as e:
                                    logger.error(f"Error in message processor hook: {e}")
                        else:
                            for hook in self._ravenfall_message_hooks:
                                try:
                                    await hook(parsed_message)
                                except Exception as e:
                                    logger.error(f"Error in message processor hook: {e}")

                        response = {
                            "correlationId": parsed_message.correlation_id,
                            "block": parsed_message._block,  # pyright: ignore[reportPrivateUsage]
                            "message": parsed_message.message
                        }
                        await ws.send_bytes(json_encode.encode(response))

                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
                        logger.error(f"Message data: {message_data}")
                        try:
                            data_dict = json.decode(message_data, type=dict)
                            correlation_id = data_dict.get("correlationId", None)
                            if correlation_id is not None:
                                response = {
                                    "correlationId": correlation_id,
                                    "block": False,
                                    "error": str(e),
                                    "message": None
                                }
                                await ws.send_bytes(json_encode.encode(response))
                        except Exception as e2:
                            logger.error(f"Error sending error response: {e2}")

                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
                    break

                elif msg.type == aiohttp.WSMsgType.CLOSE:
                    logger.info("Message processor client disconnected")
                    break

        except Exception as e:
            logger.error(f"Error in WebSocket handler: {e}")
        finally:
            __ = await ws.close()
            logger.info("Message processor WebSocket connection closed")

        return ws

    def add_ravenfall_message_hook(self, hook: Callable[[RavenfallProcessorMessage], Awaitable[None]]) -> None:
        self._ravenfall_message_hooks.append(hook)

    def remove_ravenfall_message_hook(self, hook: Callable[[RavenfallProcessorMessage], Awaitable[None]]) -> None:
        if hook in self._ravenfall_message_hooks:
            self._ravenfall_message_hooks.remove(hook)

    def add_ravenbot_message_hook(self, hook: Callable[[RavenBotProcessorMessage], Awaitable[None]]) -> None:
        self._ravenbot_message_hooks.append(hook)

    def remove_ravenbot_message_hook(self, hook: Callable[[RavenBotProcessorMessage], Awaitable[None]]) -> None:
        if hook in self._ravenbot_message_hooks:
            self._ravenbot_message_hooks.remove(hook)

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        logger.info(f"Message processor server started on ws://{self.host}:{self.port}/process")

    async def stop(self) -> None:
        if self._site:
            await self._site.stop()
            self._site = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        logger.info("Message processor server stopped")
