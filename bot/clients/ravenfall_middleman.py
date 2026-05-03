"""Client library for interacting with the Ravenfall middleman server.

This module provides classes and utilities for connecting to and communicating
with the Ravenfall middleman WebSocket server, including message handling,
connection management, and API interactions.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime  # noqa: TC003
from enum import StrEnum
from types import NoneType
from typing import TYPE_CHECKING, Any, Final, cast, overload

import aiohttp
from aiohttp import web
from msgspec import Struct, field, json

from utils.logging_fomatter import setup_logging

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Configure logging
LOGGER = logging.getLogger(__name__)
json_encode = json.Encoder()
setup_logging()


class Sender(Struct):
    """Represents the sender information in a RavenBot message."""

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
    """Represents a message sent to RavenBot."""

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
    """Represents the result of a send-and-wait operation."""

    success: bool
    correlation_id: str = field(name="correlationId")
    responses: list[RavenfallMessage]
    complete: bool
    count: int
    expected_count: int = field(name="expectedCount")
    timeout: bool


class EnsureConnectionResult(Struct):
    """Represents the result of a connection ensure operation."""

    success: bool
    message: str
    reconnected: bool
    connected: bool


class ConnectionStatus(Struct):
    """Represents the status of a WebSocket connection."""

    connection_id: str = field(name="connectionId")
    client_connected: bool = field(name="clientConnected")
    server_connected: bool = field(name="serverConnected")
    time_until_close: int = field(name="timeUntilClose")


class ConnStatusResponse(Struct):
    """Response wrapper for connection status queries."""

    success: bool
    status: ConnectionStatus


class ProxyMapping(Struct):
    """Represents a proxy mapping configuration."""

    client_port: int = field(name="clientPort")
    server_host: str = field(name="serverHost")
    server_port: int = field(name="serverPort")


class MessageProcessorConfig(Struct):
    """Configuration for the message processor."""

    enabled: bool
    url: str


class MiddlemanConfig(Struct):
    """Middleman server configuration."""

    enable_message_logging: bool = field(name="enableMessageLogging")
    disable_timeout: bool = field(name="disableTimeout")
    default_timeout_seconds: int = field(name="defaultTimeoutSeconds")
    no_identifier_timeout_seconds: int = field(name="noIdentifierTimeoutSeconds")
    api_port: int = field(name="apiPort")
    identifier_timeouts: dict[str, int] = field(name="identifierTimeouts")
    proxy_mappings: list[ProxyMapping] = field(name="proxyMappings")
    message_processor: MessageProcessorConfig = field(name="messageProcessor")


class StreamMessageBase(Struct):
    """Base class for stream messages with common metadata."""

    client_addr: str = field(name="clientAddr")
    server_addr: str = field(name="serverAddr")
    connection_id: str = field(name="connectionId")
    correlation_id: str = field(name="correlationId")
    is_api: bool = field(name="isApi")
    timestamp: datetime


class MessageOrigin(StrEnum):
    """Enumeration of possible message origins."""

    RAVENFALL = "SERVER"
    RAVENBOT = "CLIENT"
    API_RAVENFALL = "API-SERVER"
    API_RAVENBOT = "API-CLIENT"


class RavenfallStreamMessage(StreamMessageBase, tag_field="source", tag="SERVER"):
    """Stream message originating from Ravenfall server."""

    message: RavenfallMessage
    origin: MessageOrigin = MessageOrigin.RAVENFALL


class RavenBotStreamMessage(StreamMessageBase, tag_field="source", tag="CLIENT"):
    """Stream message originating from RavenBot client."""

    message: RavenBotMessage
    origin: MessageOrigin = MessageOrigin.RAVENBOT


class RavenfallApiStreamMessage(RavenfallStreamMessage, tag="API-SERVER"):
    """API stream message originating from Ravenfall server."""

    origin: MessageOrigin = MessageOrigin.API_RAVENFALL


class RavenBotApiStreamMessage(RavenBotStreamMessage, tag="API-CLIENT"):
    """API stream message originating from RavenBot client."""

    origin: MessageOrigin = MessageOrigin.API_RAVENBOT


StreamMessageType = (
    RavenfallStreamMessage
    | RavenBotStreamMessage
    | RavenfallApiStreamMessage
    | RavenBotApiStreamMessage
)


class ClientError(BaseException):
    """Exception raised for client-side errors."""


class MiddlemanError(BaseException):
    """Exception raised for middleman server errors."""


class MiddlemanClient:
    """Client for interacting with the Ravenfall middleman server."""

    def __init__(self, base_url: str) -> None:
        """Initialize the middleman client.

        Args:
            base_url: Base URL of middleman server

        """
        self.base_url: str = base_url.rstrip("/")
        self._websocket: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._ravenbot_message_hooks: list[
            Callable[[RavenBotStreamMessage], Awaitable[None]]
        ] = []
        self._ravenfall_message_hooks: list[
            Callable[[RavenfallStreamMessage], Awaitable[None]]
        ] = []
        self._ws_task: asyncio.Task[None] | None = None
        self._connected: bool = False

    def _raise_on_code(self, code: int, response_data: Any) -> None:
        if not isinstance(response_data, dict):
            msg = f"Invalid response from middleman API: {response_data}"
            raise ClientError(msg)
        if code in {400, 404}:
            msg = f"Middleman API returned error: {response_data}"
            raise ClientError(msg)
        if code == 500:  # noqa: PLR2004
            msg = f"Middleman API returned error: {response_data}"
            raise MiddlemanError(msg)
        if code == 200:  # noqa: PLR2004
            pass
        else:
            msg = f"Middleman API returned error: {response_data}"
            raise ClientError(msg)

    @overload
    async def _get(self, endpoint: str, out_type: None = None) -> None: ...
    @overload
    async def _get[T](self, endpoint: str, out_type: type[T] = NoneType) -> T: ...  # ty:ignore[invalid-parameter-default]  # pyrefly: ignore[bad-function-definition]

    async def _get[T](self, endpoint: str, out_type: type[T] | None = None) -> T | None:
        headers = {"Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/{endpoint}",
                headers=headers,
            ) as response:
                response_text = await response.text()
                self._raise_on_code(response.status, response_text)
            if isinstance(out_type, NoneType):
                return None
            return json.decode(response_text, type=out_type)

    @overload
    async def _post(
        self,
        endpoint: str,
        out_type: None = None,
        data: dict[str, Any] | None = None,
    ) -> None: ...
    @overload
    async def _post[T](
        self,
        endpoint: str,
        out_type: type[T] = NoneType,
        data: dict[str, Any] | None = None,
    ) -> T: ...  # ty:ignore[invalid-parameter-default]  # pyrefly: ignore[bad-function-definition]

    async def _post[T](
        self,
        endpoint: str,
        out_type: type[T] | None = None,
        data: dict[str, Any] | None = None,
    ) -> T | None:
        headers = {"Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            encoded = json_encode.encode(data)
            async with session.post(
                f"{self.base_url}/{endpoint}",
                data=encoded,
                headers=headers,
            ) as response:
                response_text = await response.text()
                self._raise_on_code(response.status, response_text)
            if isinstance(out_type, NoneType):
                return None
            return json.decode(response_text, type=out_type)

    async def force_reconnect(self, connection_id: str, timeout_seconds: int = 0) -> None:
        """Force the middleman to reconnect to Ravenfall.

        Args:
            connection_id: The connection ID to reconnect
            timeout_seconds: Timeout in seconds for the request

        """
        data = {"connectionId": connection_id, "timeout": timeout_seconds}
        await self._post("/api/reconnect", None, data)

    async def send_to_ravenbot(
        self,
        connection_id: str,
        message: RavenBotMessage,
    ) -> None:
        """Send RavenBot a message.

        Args:
            connection_id: The connection ID to send the message to
            message: The message to send

        """
        data = {"connectionId": connection_id, "data": json_encode.encode(message)}
        await self._post("/api/send-to-client", None, data)

    async def send_to_ravenfall(
        self,
        connection_id: str,
        message: RavenfallMessage,
    ) -> None:
        """Send Ravenfall a message.

        Args:
            connection_id: The connection ID to send the message to
            message: The message to send

        """
        data = {"connectionId": connection_id, "data": json_encode.encode(message)}
        await self._post("/api/send-to-server", None, data)

    async def send_to_ravenfall_and_wait_for_response(
        self,
        connection_id: str,
        message: SendAndWaitResult,
    ) -> SendAndWaitResult:
        """Send Ravenfall a message and wait for a response.

        Args:
            connection_id: The connection ID to send the message to
            message: The message to send

        """
        data = {"connectionId": connection_id, "data": json_encode.encode(message)}
        return await self._post("/api/send-to-server-and-wait", SendAndWaitResult, data)

    async def ensure_connection(
        self,
        connection_id: str,
        timeout_extension: int = 30,
    ) -> EnsureConnectionResult:
        """Ensure the connection is active by extending its timeout.

        Args:
            connection_id: The connection ID to extend
            timeout_extension: The timeout extension in seconds (default: 30)

        """
        data = {"connectionId": connection_id, "timeout": timeout_extension}
        return await self._post("/api/ensure-connected", EnsureConnectionResult, data)

    async def get_connection_status(self, connection_id: str) -> ConnStatusResponse:
        """Get the status of a connection.

        Args:
            connection_id: The connection ID to get the status for

        """
        return await self._get(
            "/api/connection-status?connectionId=" + connection_id,
            ConnStatusResponse,
        )

    async def get_config(self) -> MiddlemanConfig:
        """Get the configuration of the middleman."""
        return await self._get("/api/config", MiddlemanConfig)

    def add_ravenfall_message_hook(
        self,
        hook: Callable[[RavenfallStreamMessage], Awaitable[None]],
    ) -> None:
        """Add a hook function to receive WebSocket messages.

        Args:
            hook: An async function that takes a RavenfallStreamMessage instance

        """
        self._ravenfall_message_hooks.append(hook)

    def remove_ravenfall_message_hook(
        self,
        hook: Callable[[RavenfallStreamMessage], Awaitable[None]],
    ) -> None:
        """Remove a message hook function.

        Args:
            hook: The hook function to remove

        """
        if hook in self._ravenfall_message_hooks:
            self._ravenfall_message_hooks.remove(hook)

    def add_ravenbot_message_hook(
        self,
        hook: Callable[[RavenBotStreamMessage], Awaitable[None]],
    ) -> None:
        """Add a hook function to receive WebSocket messages.

        Args:
            hook: An async function that takes a RavenBotStreamMessage instance

        """
        self._ravenbot_message_hooks.append(hook)

    def remove_ravenbot_message_hook(
        self,
        hook: Callable[[RavenBotStreamMessage], Awaitable[None]],
    ) -> None:
        """Remove a message hook function.

        Args:
            hook: The hook function to remove

        """
        if hook in self._ravenbot_message_hooks:
            self._ravenbot_message_hooks.remove(hook)

    async def _establish_ws_connection(self, *, is_reconnect: bool = False) -> None:
        """Establish the WebSocket connection to the server."""
        ws_url = (
            self.base_url.replace("http://", "ws://").replace("https://", "wss://")
            + "/ws"
        )
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        self._websocket = await self._session.ws_connect(ws_url)
        if is_reconnect:
            LOGGER.info("Reconnected to WebSocket successfully")
        else:
            LOGGER.info("Connected to WebSocket at %s", ws_url)

    async def connect_websocket(self) -> None:
        """Connect to the middleman's WebSocket stream."""
        if self._connected:
            LOGGER.warning("WebSocket is already connected")
            return

        self._connected = True

        try:
            await self._establish_ws_connection(is_reconnect=False)
        except Exception as e:
            LOGGER.exception("Failed to connect to WebSocket")

        # Start the message receiving task
        self._ws_task = asyncio.create_task(self._receive_messages())

    async def disconnect_websocket(self) -> None:
        """Disconnect from the WebSocket stream."""
        if not self._connected:
            LOGGER.warning("WebSocket is not connected")
            return

        self._connected = False

        if self._ws_task:
            _ = self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None

        if self._websocket:
            _ = await self._websocket.close()
            self._websocket = None

        if self._session:
            await self._session.close()
            self._session = None

        LOGGER.info("Disconnected from WebSocket")

    async def _handle_ws_text_message(self, text_data: bytes):
        parsed_message = cast(
            "StreamMessageType",
            json.decode(
                text_data,
                type=StreamMessageType,
            ),
        )

        if isinstance(parsed_message, RavenBotStreamMessage):
            for hook in self._ravenbot_message_hooks:
                try:
                    await hook(parsed_message)
                except Exception:
                    LOGGER.exception("Error in message hook")
        else:
            for hook in self._ravenfall_message_hooks:
                try:
                    await hook(parsed_message)
                except Exception:
                    LOGGER.exception("Error in message hook")

    async def _receive_messages(self) -> None:
        """Receive and process messages from the WebSocket."""
        try:
            while self._connected:
                if not self._websocket or self._websocket.closed:
                    LOGGER.info("Attempting to reconnect WebSocket...")
                    try:
                        await self._establish_ws_connection(is_reconnect=True)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        LOGGER.warning("Failed to reconnect to WebSocket: %s", e)
                        await asyncio.sleep(5)
                        continue

                if self._websocket is None:
                    continue

                message_data_str: bytes = b""
                try:
                    message = await self._websocket.receive()

                    if message.type == aiohttp.WSMsgType.TEXT:
                        message_data_str = cast("bytes", message.data)
                        await self._handle_ws_text_message(message_data_str)

                    elif message.type == aiohttp.WSMsgType.ERROR:
                        LOGGER.error(f"WebSocket error: {self._websocket.exception()}")
                        self._websocket = None
                        await asyncio.sleep(1)

                    elif message.type in {
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                    }:
                        LOGGER.warning("WebSocket connection closed unexpectedly")
                        self._websocket = None
                        await asyncio.sleep(1)

                    else:
                        LOGGER.debug(f"Message type {message.type} was ignored")

                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception(
                        "Error receiving WebSocket message! "
                        f"Message data: {message_data_str}",
                    )
                    self._websocket = None
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            LOGGER.info("WebSocket message receiver task cancelled")
        finally:
            self._connected = False

    @property
    def is_websocket_connected(self) -> bool:
        """Check if the WebSocket is connected."""
        return (
            self._connected and self._websocket is not None and not self._websocket.closed
        )


class FrozenSender(Sender, frozen=True):  # pyright: ignore[reportGeneralTypeIssues]  # ty:ignore[invalid-frozen-dataclass-subclass]
    """Frozen version of Sender for immutable message data."""


class FrozenRavenBotMessage(Struct, frozen=True):
    """Frozen version of RavenBotMessage for immutable message data."""

    identifier: str = field(name="Identifier")
    sender: FrozenSender = field(name="Sender")
    content: str = field(name="Content")
    correlation_id: str | None = field(name="CorrelationId")


class FrozenRecipient(Recipient, frozen=True):  # pyright: ignore[reportGeneralTypeIssues]  # ty:ignore[invalid-frozen-dataclass-subclass]
    """Frozen version of Recipient for immutable message data."""


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
    """Base class for processor messages with common metadata."""

    client_addr: Final[str] = field(name="clientAddr")
    server_addr: Final[str] = field(name="serverAddr")
    connection_id: Final[str] = field(name="connectionId")
    correlation_id: Final[str] = field(name="correlationId")
    is_api: Final[bool] = field(name="isApi")
    timestamp: Final[datetime]
    _block: bool = False

    def block(self) -> None:
        """Mark this message to be blocked from further processing."""
        self._block = True


class RavenfallProcessorMessage(ProcessorMessageBase, tag_field="source", tag="SERVER"):
    """Processor message originating from Ravenfall server."""

    message: RavenfallMessage
    original_message: Final[FrozenRavenfallMessage] = field(name="originalMessage")
    origin: Final[MessageOrigin] = MessageOrigin.RAVENFALL


class RavenBotProcessorMessage(ProcessorMessageBase, tag_field="source", tag="CLIENT"):
    """Processor message originating from RavenBot client."""

    message: RavenBotMessage
    original_message: Final[FrozenRavenBotMessage] = field(name="originalMessage")
    origin: Final[MessageOrigin] = MessageOrigin.RAVENBOT


class RavenfallApiProcessorMessage(
    RavenfallProcessorMessage,
    tag_field="source",
    tag="API-SERVER",
):
    """API processor message originating from Ravenfall server."""

    origin: Final[MessageOrigin] = MessageOrigin.API_RAVENFALL  # pyright: ignore[reportGeneralTypeIssues]  # ty:ignore[override-of-final-variable]  # pyrefly:ignore[bad-override]


class RavenBotApiProcessorMessage(
    RavenBotProcessorMessage,
    tag_field="source",
    tag="API-CLIENT",
):
    """API processor message originating from RavenBot client."""

    origin: Final[MessageOrigin] = MessageOrigin.API_RAVENBOT  # pyright: ignore[reportGeneralTypeIssues]  # ty:ignore[override-of-final-variable]  # pyrefly:ignore[bad-override]


ProcessorMessageType = (
    RavenfallProcessorMessage
    | RavenBotProcessorMessage
    | RavenfallApiProcessorMessage
    | RavenBotApiProcessorMessage
)


class MessageProcessorServer:
    """WebSocket server for processing Ravenfall messages."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9000):
        """Initialize the message processor server.

        Args:
            host: Host address to bind to (default: "127.0.0.1")
            port: Port to listen on (default: 9000)

        """
        self.host: str = host
        self.port: int = port
        self._app: web.Application = web.Application()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._ravenbot_message_hooks: list[
            Callable[[RavenBotProcessorMessage], Awaitable[None]]
        ] = []
        self._ravenfall_message_hooks: list[
            Callable[[RavenfallProcessorMessage], Awaitable[None]]
        ] = []
        self._active_client_count: int = 0
        self._setup_routes()

    def _setup_routes(self) -> None:
        __ = self._app.router.add_get("/process", self._websocket_handler)

    async def _handle_ws_message(self, ws: web.WebSocketResponse, text_data: bytes):
        try:
            parsed_message = cast(
                "ProcessorMessageType",
                json.decode(
                    text_data,
                    type=ProcessorMessageType,
                ),
            )
            if isinstance(parsed_message, RavenBotProcessorMessage):
                for hook in self._ravenbot_message_hooks:
                    try:
                        await hook(parsed_message)
                    except Exception:
                        LOGGER.exception(
                            "Error in message processor hook",
                        )
            else:
                for hook in self._ravenfall_message_hooks:
                    try:
                        await hook(parsed_message)
                    except Exception:
                        LOGGER.exception(
                            "Error in message processor hook",
                        )

            response = {
                "correlationId": parsed_message.correlation_id,
                "block": parsed_message._block,
                "message": parsed_message.message,
            }
            await ws.send_bytes(json_encode.encode(response))

        except Exception as e:
            LOGGER.exception(f"Error processing message. Data: {text_data}")
            try:
                data_dict = cast("dict[str, Any]", json.decode(text_data, type=dict))
                correlation_id = data_dict.get("correlationId")
                if correlation_id is not None:
                    response = {
                        "correlationId": correlation_id,
                        "block": False,
                        "error": str(e),
                        "message": None,
                    }
                    await ws.send_bytes(json_encode.encode(response))
            except Exception:
                LOGGER.exception("Error sending error response")

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        __ = await ws.prepare(request)
        self._active_client_count += 1
        LOGGER.info(
            "Message processor client connected from %s (active clients=%d)",
            request.remote,
            self._active_client_count,
        )

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_ws_message(ws, cast("bytes", msg.data))

                elif msg.type == aiohttp.WSMsgType.ERROR:
                    LOGGER.error(f"WebSocket error: {ws.exception()}")
                    break

                elif msg.type == aiohttp.WSMsgType.CLOSE:
                    LOGGER.info("Message processor client disconnected")
                    break

                else:
                    LOGGER.debug(f"Unknown message type {msg.type}")

        except Exception:
            LOGGER.exception("Error in WebSocket handler")
        finally:
            self._active_client_count = max(0, self._active_client_count - 1)
            __ = await ws.close()
            LOGGER.info(
                "Message processor WebSocket connection closed (active clients=%d)",
                self._active_client_count,
            )

        return ws

    @property
    def connected_client_count(self) -> int:
        """Return the number of currently connected processor clients."""
        return self._active_client_count

    def add_ravenfall_message_hook(
        self,
        hook: Callable[[RavenfallProcessorMessage], Awaitable[None]],
    ) -> None:
        """Add a hook function to receive Ravenfall processor messages.

        Args:
            hook: An async function that takes a RavenfallProcessorMessage instance

        """
        self._ravenfall_message_hooks.append(hook)

    def remove_ravenfall_message_hook(
        self,
        hook: Callable[[RavenfallProcessorMessage], Awaitable[None]],
    ) -> None:
        """Remove a Ravenfall message hook function.

        Args:
            hook: The hook function to remove

        """
        if hook in self._ravenfall_message_hooks:
            self._ravenfall_message_hooks.remove(hook)

    def add_ravenbot_message_hook(
        self,
        hook: Callable[[RavenBotProcessorMessage], Awaitable[None]],
    ) -> None:
        """Add a hook function to receive RavenBot processor messages.

        Args:
            hook: An async function that takes a RavenBotProcessorMessage instance

        """
        self._ravenbot_message_hooks.append(hook)

    def remove_ravenbot_message_hook(
        self,
        hook: Callable[[RavenBotProcessorMessage], Awaitable[None]],
    ) -> None:
        """Remove a RavenBot message hook function.

        Args:
            hook: The hook function to remove

        """
        if hook in self._ravenbot_message_hooks:
            self._ravenbot_message_hooks.remove(hook)

    async def start(self) -> None:
        """Start the message processor server."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        LOGGER.info(
            "Message processor server started on ws://%s:%s/process",
            self.host,
            self.port,
        )

    async def stop(self) -> None:
        """Stop the message processor server."""
        if self._site:
            await self._site.stop()
            self._site = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        LOGGER.info("Message processor server stopped")
