import asyncio
import datetime
from datetime import timezone
import json
import logging
import signal
import uuid
from typing import Any, Callable, TypedDict, NotRequired, cast, Literal
from collections.abc import Coroutine
from dataclasses import dataclass, field
from websockets.asyncio.server import Server
from websockets import ServerConnection
import websockets
from .models import RavenBotMessage, RavenfallMessage

# Configure logging
logger = logging.getLogger('new_message_processor')


@dataclass
class MessageMetadata:
    """Metadata about a message being processed."""
    source: str = "unknown"
    connection_id: str = "unknown"
    correlation_id: str = ""
    is_api: bool = False
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(timezone.utc).isoformat())
    custom_metadata: dict[str, Any] = field(default_factory=dict)
    client_addr: str = ""
    server_addr: str = ""

class BlockResponse(TypedDict):
    block: bool

class ProcessorResponse(TypedDict):
    """Response format for message processor callbacks."""
    block: NotRequired[bool]  # If True, the message will be blocked
    message: NotRequired[dict[str, RavenBotMessage | RavenfallMessage | BlockResponse]]  # Modified message content (optional)
    error: NotRequired[str]  # Error message (optional)
    correlation_id: str  # Correlation ID for tracking

class ProcessorMessage(TypedDict):
    source: Literal["CLIENT", "SERVER", "API-CLIENT", "API-SERVER"]
    client_addr: str
    server_addr: str
    connection_id: str
    correlation_id: str
    is_api: bool
    timestamp: str
    message: RavenBotMessage | RavenfallMessage

# Define types for callbacks
MessageCallback = Callable[
    [RavenBotMessage | RavenfallMessage | BlockResponse, MessageMetadata, 'ClientInfo'],  # message_data, metadata, client_info
    Coroutine[Any, Any, RavenBotMessage | RavenfallMessage | BlockResponse | None]  # Return None to keep current message, or return new message data
]
ConnectionCallback = Callable[['ClientInfo'], Coroutine[Any, Any, None]]

@dataclass
class ClientInfo:
    """Information about a connected WebSocket client."""
    websocket: ServerConnection
    client_id: str
    remote_address: str
    connection_time: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    metadata: dict[str, Any] = field(default_factory=dict)

class MessageProcessor:
    def __init__(self, host: str = '0.0.0.0', port: int = 8000, max_message_size: int = 10 * 1024 * 1024):
        """
        Initializes the MessageProcessor with WebSocket server configuration.
        
        Args:
            host: Host to bind the WebSocket server to
            port: Port to listen on
            max_message_size: Maximum message size in bytes (default: 10MB)
        """
        self.host: str = host
        self.port: int = port
        self.max_message_size: int = max_message_size
        self.server: None | Server = None
        self.clients: dict[str, ClientInfo] = {}
        self.running: bool = False
        
        # Callback lists
        self.message_callbacks: list[MessageCallback] = []
        self.connection_callbacks: list[ConnectionCallback] = []
        self.disconnection_callbacks: list[ConnectionCallback] = []
        
        logger.info(f"MessageProcessor initialized on {host}:{port}")

    # Callback registration methods
    def add_message_callback(self, callback: MessageCallback) -> None:
        """Register a callback to process incoming messages."""
        self.message_callbacks.append(callback)
        
    def add_connection_callback(self, callback: ConnectionCallback) -> None:
        """Register a callback for new client connections."""
        self.connection_callbacks.append(callback)
        
    def add_disconnection_callback(self, callback: ConnectionCallback) -> None:
        """Register a callback for client disconnections."""
        self.disconnection_callbacks.append(callback)
    
    async def process_message(self, message: str, client_info: ClientInfo) -> str:
        """
        Processes an incoming message and returns a response.

        This method can be overridden by subclasses or extended using callbacks.
        
        Args:
            message: The incoming message as a string (expected to be JSON).
            client_info: Information about the client that sent the message.
            
        Returns:
            str: The processed message as a JSON string with a trailing newline.
        """
        try:
            # Parse the message as JSON
            try:
                message_data = cast(ProcessorMessage, json.loads(message))
                
                # Create message metadata
                metadata = MessageMetadata(
                    source=message_data['source'],
                    connection_id=message_data['connection_id'],
                    correlation_id=message_data['correlation_id'],
                    is_api=message_data['is_api'],
                    custom_metadata=message_data.get('custom_metadata', {}),
                    timestamp=message_data['timestamp']
                )
                
                # The remaining data is the actual message content
                message_content: RavenfallMessage | RavenBotMessage = message_data['message']
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse message as JSON: {e}")
                raise ValueError(f"Invalid JSON message: {message}")

            # Log processing information
            if metadata.is_api:
                logger.info(f"Processing API-originated message (Correlation ID: {metadata.correlation_id})")

            logger.debug(
                f"Processing message from {metadata.source} " +
                f"(client: {client_info.client_id}, " +
                f"connection: {metadata.connection_id}, " +
                f"correlation: {metadata.correlation_id})"
            )
            logger.debug(f"Message data: {message_content}")

            # Process message through callbacks
            processor_response: ProcessorResponse | None = None
            
            for callback in self.message_callbacks:
                try:
                    result = await callback(message_content, metadata, client_info)
                    if not result:
                        continue
                                                
                    # Check for block flag
                    if result.get('block') is True:
                        logger.debug(f"Message blocked by callback for connection {metadata.connection_id}")
                        return json.dumps({
                            "block": True,
                            "correlation_id": metadata.correlation_id
                        }) + '\n'
                        
                    message_content = cast(RavenBotMessage | RavenfallMessage, result)
                                                
                except Exception as e:
                    error_msg = f"Error in message callback: {e}"
                    logger.error(error_msg, exc_info=True)
                    processor_response = {
                        'error': error_msg,
                        'correlation_id': metadata.correlation_id
                    }
                    break
                    
            # If there was an error in processing, return it
            if processor_response and 'error' in processor_response:
                return json.dumps(processor_response) + '\n'

            # Prepare response with metadata
            response = {
                "message": message_content,  # Include all message data
                "correlation_id": metadata.correlation_id,
                "status": "processed"
            }
            
            # Only include source and connection_id if they're not empty
            if metadata.source and metadata.source != "unknown":
                response["source"] = metadata.source
            if metadata.connection_id and metadata.connection_id != "unknown":
                response["connection_id"] = metadata.connection_id
            
            return json.dumps(response) + '\n'
            
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON: {str(e)}"
            logger.error(f"{error_msg}. Message: {message[:200]}")
            # Return the original message with a newline if it didn't have one
            if not message.endswith('\n'):
                message += '\n'
            return message
        except Exception as e:
            error_msg = f"Error processing message: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return json.dumps({"error": error_msg, "original_message": message}) + '\n'
    
    async def _handle_client(self, websocket: ServerConnection) -> None:
        """Handle a new WebSocket client connection.
        
        Args:
            websocket: The WebSocket connection instance
        """
        client_id = str(uuid.uuid4())
        remote_address = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        
        client_info = ClientInfo(
            websocket=websocket,
            client_id=client_id,
            remote_address=remote_address
        )
        
        self.clients[client_id] = client_info
        logger.info(f"Client connected: {client_id} from {remote_address}")
        
        # Notify connection callbacks
        for callback in self.connection_callbacks:
            try:
                await callback(client_info)
            except Exception as e:
                logger.error(f"Error in connection callback: {e}", exc_info=True)
        
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    message = message.decode('utf-8')
                
                # Process the message
                response = await self.process_message(message, client_info)
                
                # Send the response back to the client
                if response:
                    await websocket.send(response)
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client {client_id} disconnected")
        except Exception as e:
            logger.error(f"Error with client {client_id}: {e}", exc_info=True)
        finally:
            # Clean up
            _ = self.clients.pop(client_id, None)
            
            # Notify disconnection callbacks
            for callback in self.disconnection_callbacks:
                try:
                    await callback(client_info)
                except Exception as e:
                    logger.error(f"Error in disconnection callback: {e}", exc_info=True)
    
    def start(self) -> None:
        """Start the WebSocket server.
        
        Note: This method is synchronous. The server will run in the current event loop.
        """
        if self.running:
            logger.warning("Server is already running")
            return
            
        loop = asyncio.get_event_loop()
        
        # Create server coroutine
        server_coro = websockets.serve(
            self._handle_client,
            self.host,
            self.port,
            max_size=self.max_message_size,
            ping_interval=20,  # 20 seconds
            ping_timeout=20,   # 20 seconds
            close_timeout=5,   # 5 seconds
            logger=logger
        )
        
        # Start the server, handling both running and new event loops
        if loop.is_running():
            # If loop is already running, create a task to start the server
            async def start_server():
                self.server = await server_coro
                self.running = True
                logger.info(f"WebSocket server started on ws://{self.host}:{self.port}")
            
            # Schedule the server to start
            __ = loop.create_task(start_server())
        else:
            # If no loop is running, use run_until_complete
            self.server = loop.run_until_complete(server_coro)
            self.running = True
            logger.info(f"WebSocket server started on ws://{self.host}:{self.port}")
            
            # Only set up signal handlers if we're not in a running loop
            try:
                loop.add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(self.astop()))
                loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(self.astop()))
            except NotImplementedError:
                # Windows compatibility
                pass
    
    async def astop(self) -> None:
        """Asynchronously stop the WebSocket server."""
        if not self.running:
            return
            
        logger.info("Stopping WebSocket server...")
        self.running = False
        
        # Create a list to store any cleanup tasks
        cleanup_tasks: list[Coroutine[Any, Any, None]] = []
        
        # Close all client connections first
        if self.clients:
            logger.info(f"Closing {len(self.clients)} client connections...")
            for client_info in list(self.clients.values()):
                cleanup_tasks.append(client_info.websocket.close())
        
        # Close the server to prevent new connections
        if self.server:
            self.server.close()
            self.server = None
        
        # Wait for all cleanup tasks to complete with a timeout
        if cleanup_tasks:
            try:
                # Use asyncio.shield only if the event loop is still running
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    _ = await asyncio.wait_for(asyncio.gather(*cleanup_tasks, return_exceptions=True), timeout=2.0)
            except (asyncio.TimeoutError, RuntimeError, asyncio.CancelledError) as e:
                logger.debug(f"Cleanup completed with: {type(e).__name__}: {e}")
        
        # Clear remaining state
        self.server = None
        self.clients.clear()
            
        logger.info("WebSocket server stopped")
            
    def stop(self) -> None:
        """Synchronously stop the WebSocket server."""
        loop = asyncio.get_event_loop()
        if loop.is_running():
            _ = loop.create_task(self.astop())
        else:
            loop.run_until_complete(self.astop())
