import asyncio
import logging
import json
from typing import Any, Callable, cast
from collections.abc import Awaitable
import aiohttp
from datetime import datetime

class AutoReconnectingWebSocket:
    """
    A WebSocket client that automatically reconnects with exponential backoff.
    
    Features:
    - Automatic reconnection with exponential backoff
    - Message queuing when disconnected
    - Event-based callbacks
    - Connection state tracking
    """
    
    def __init__(
        self,
        url: str,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,  # pyright: ignore [reportExplicitAny]
        on_connect: Callable[[], Awaitable[None]] | None = None,
        on_disconnect: Callable[[], Awaitable[None]] | None = None,
        on_error: Callable[[Exception], Awaitable[None]] | None = None,
        reconnect_interval: float = 1.0,
        max_reconnect_interval: float = 30.0,
        max_retries: int | None = None,
        logger: logging.Logger | None = None,
        **session_kwargs: Any  # pyright: ignore [reportExplicitAny, reportAny]
    ):
        """
        Initialize the AutoReconnectingWebSocket.
        
        Args:
            url: WebSocket server URL
            on_message: Callback for received messages
            on_connect: Callback when connection is established
            on_disconnect: Callback when connection is lost
            on_error: Callback for errors
            reconnect_interval: Initial reconnection delay in seconds
            max_reconnect_interval: Maximum reconnection delay in seconds
            max_retries: Maximum number of reconnection attempts (None for unlimited)
            logger: Custom logger instance
            **session_kwargs: Additional arguments for aiohttp.ClientSession
        """
        self.url: str = url
        self.on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = on_message  # pyright: ignore [reportExplicitAny]
        self.on_connect: Callable[[], Awaitable[None]] | None = on_connect
        self.on_disconnect: Callable[[], Awaitable[None]] | None = on_disconnect
        self.on_error: Callable[[Exception], Awaitable[None]] | None = on_error
        self.reconnect_interval: float = reconnect_interval
        self.max_reconnect_interval: float = max_reconnect_interval
        self.max_retries: int | None = max_retries
        self.retry_count: int = 0
        self.logger: logging.Logger = logger or logging.getLogger(f"AutoReconnectingWebSocket: {url}")
        self.session_kwargs: dict[str, Any] = session_kwargs  # pyright: ignore [reportExplicitAny]
        
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._reconnect_task: asyncio.Task[Any] | None = None  # pyright: ignore [reportExplicitAny]
        self._listen_task: asyncio.Task[Any] | None = None  # pyright: ignore [reportExplicitAny]
        self._message_queue: asyncio.Queue[dict[str, Any] | str | bytes] = asyncio.Queue()  # pyright: ignore [reportExplicitAny]
        self._is_connected: bool = False
        self._should_reconnect: bool = True
        self._last_message_time: datetime | None = None
        self._connection_lock: asyncio.Lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        """Return True if the WebSocket is currently connected."""
        return self._is_connected and self._ws is not None and not self._ws.closed

    async def connect(self) -> None:
        """Connect to the WebSocket server."""
        async with self._connection_lock:
            if self.is_connected:
                return
                
            self._should_reconnect = True
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def disconnect(self) -> None:
        """Disconnect from the WebSocket server and clean up."""
        async with self._connection_lock:
            self._should_reconnect = False
            
            if self._reconnect_task and not self._reconnect_task.done():
                _ = self._reconnect_task.cancel()
                try:
                    await self._reconnect_task
                except asyncio.CancelledError:
                    pass
            
            if self._listen_task and not self._listen_task.done():
                _ = self._listen_task.cancel()
                try:
                    await self._listen_task
                except asyncio.CancelledError:
                    pass
            
            if self._ws and not self._ws.closed:
                _ = await self._ws.close()
            
            if self._session and not self._session.closed:
                _ = await self._session.close()
            
            self._ws = None
            self._session = None
            self._is_connected = False
            
            if self.on_disconnect:
                try:
                    await self.on_disconnect()
                except Exception as e:
                    self.logger.error(f"Error in on_disconnect callback: {e}", exc_info=True)

    async def send(self, message: dict[str, Any] | str | bytes) -> None:  # pyright: ignore [reportExplicitAny]
        """
        Send a message through the WebSocket.
        
        Args:
            message: Message to send (dict, str, or bytes)
        """
        if (not self.is_connected) or not self._ws:
            self.logger.warning("WebSocket not connected, queuing message")
            await self._message_queue.put(message)
            return
            
        try:
            if isinstance(message, dict):
                message_str = json.dumps(message)
                await self._ws.send_str(message_str)
            elif isinstance(message, str):
                await self._ws.send_str(message)
            else:  # Must be bytes due to type hint exhaustion
                await self._ws.send_bytes(message)
                
            self._last_message_time = datetime.now()
            
        except ConnectionResetError:
            self.logger.warning("Connection reset while sending message, will reconnect")
            await self._handle_disconnect()
            # Requeue the message
            await self._message_queue.put(message)
        except Exception as e:
            self.logger.error(f"Error sending message: {e}", exc_info=True)
            if self.on_error:
                try:
                    await self.on_error(e)
                except Exception as callback_error:
                    self.logger.error(f"Error in on_error callback: {callback_error}", exc_info=True)

    async def _connect_websocket(self) -> bool:
        """Establish WebSocket connection."""
        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(**self.session_kwargs)  # pyright: ignore [reportAny]
                
            self._ws = await self._session.ws_connect(
                self.url,
                heartbeat=30,
                timeout=aiohttp.ClientWSTimeout(ws_close=30)  # pyright: ignore [reportCallIssue]  # ty:ignore[unknown-argument]
            )
            
            self._is_connected = True
            self.retry_count = 0
            self._last_message_time = datetime.now()
            
            # Start listening for messages
            self._listen_task = asyncio.create_task(self._listen())
            
            # Process any queued messages
            await self._process_message_queue()
            
            if self.on_connect:
                try:
                    await self.on_connect()
                except Exception as e:
                    self.logger.error(f"Error in on_connect callback: {e}", exc_info=True)
            
            self.logger.info(f"WebSocket connected to {self.url}")
            return True
            
        except Exception as e:
            self.logger.error(f"WebSocket connection error: {e}")
            if self.on_error:
                try:
                    await self.on_error(e)
                except Exception as callback_error:
                    self.logger.error(f"Error in on_error callback: {callback_error}", exc_info=True)
            return False

    async def _reconnect_loop(self) -> None:
        """Handle reconnection logic with exponential backoff."""
        while self._should_reconnect:
            if not self.is_connected:
                connected = await self._connect_websocket()
                if not connected:
                    # Exponential backoff with jitter
                    delay: float = cast(float, min(
                        self.reconnect_interval * (2 ** (min(self.retry_count, 10))),  # pyright: ignore [reportAny]
                        self.max_reconnect_interval
                    ) * (0.5 + (0.5 * (1 + 0.1 * (self.retry_count % 10)))))  # Add some jitter
                    
                    self.retry_count += 1
                    if self.max_retries is not None and self.retry_count > self.max_retries:
                        self.logger.error("Max retries reached, giving up")
                        return
                        
                    self.logger.info(f"Reconnecting in {delay:.1f} seconds... (attempt {self.retry_count})")
                    await asyncio.sleep(delay)
            else:
                await asyncio.sleep(1)

    async def _listen(self) -> None:
        """Listen for incoming WebSocket messages."""
        if not self._ws:
            return
        try:
            async for msg in self._ws:
                raw_data: str | bytes = cast(str | bytes, msg.data)
                self._last_message_time = datetime.now()
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self.logger.debug(f"Received text message: {raw_data}")
                    try:
                        data: Any = json.loads(raw_data)  # pyright: ignore [reportExplicitAny, reportAny]
                        if self.on_message:
                            try:
                                await self.on_message(data)  # pyright: ignore [reportAny]
                            except Exception as e:
                                self.logger.error(f"Error in on_message callback: {e}", exc_info=True)
                    except json.JSONDecodeError:
                        self.logger.warning(f"Received non-JSON message: {raw_data}")
                        
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    self.logger.debug(f"Received binary message: {len(raw_data)} bytes")
                    
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self.logger.error(f"WebSocket error: {self._ws.exception()}")
                    break
                    
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                    self.logger.info("WebSocket connection closed by server")
                    break
                    
        except asyncio.CancelledError:
            self.logger.debug("Listen task cancelled")
            raise
            
        except Exception as e:
            self.logger.error(f"Error in WebSocket listener: {e}", exc_info=True)
            
        finally:
            await self._handle_disconnect()

    async def _handle_disconnect(self) -> None:
        """Handle disconnection and schedule reconnection."""
        if self._is_connected:
            self._is_connected = False
            
            if self._ws and not self._ws.closed:
                _ = await self._ws.close()
            
            if self.on_disconnect:
                try:
                    await self.on_disconnect()
                except Exception as e:
                    self.logger.error(f"Error in on_disconnect callback: {e}", exc_info=True)
            
            self.logger.warning("WebSocket disconnected, will attempt to reconnect")

    async def _process_message_queue(self) -> None:
        """Process any messages that were queued while disconnected."""
        while not self._message_queue.empty():
            message = await self._message_queue.get()
            await self.send(message)

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):  # pyright: ignore [reportUnknownParameterType, reportMissingParameterType]
        """Async context manager exit."""
        await self.disconnect()

    async def close(self) -> None:
        """Alias for disconnect()."""
        await self.disconnect()

    async def wait_until_connected(self, timeout: float | None = None) -> bool:
        """
        Wait until the WebSocket is connected.
        
        Args:
            timeout: Maximum time to wait in seconds (None for no timeout)
            
        Returns:
            bool: True if connected, False if timed out
        """
        start_time = asyncio.get_event_loop().time()
        while not self.is_connected:
            elapsed = asyncio.get_event_loop().time() - start_time
            if timeout is not None and elapsed >= timeout:
                return False
            await asyncio.sleep(0.1)
        return True
