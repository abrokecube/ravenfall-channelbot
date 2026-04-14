"""Remote bot service for inter-bot communication via REST API."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, NamedTuple, cast, override

import aiohttp
from aiohttp import web
from msgspec import Struct, convert, defstruct, json, structs
from pydantic import BaseModel, Field

from bot.core.components import BaseService
from bot.services.config_service import ConfigService, ConfigSubscriberMixin

if TYPE_CHECKING:
    from collections.abc import Callable

    from bot.core.components import Cog

LOGGER = logging.getLogger(__name__)

type RemoteMethod[T: Struct] = Callable[..., T | Awaitable[T]]

DEFAULT_ENCODER = json.Encoder()


def dumps(x: Any) -> str:  # pyright: ignore[reportExplicitAny, reportAny]
    """Default JSON encoder using msgspec."""
    return DEFAULT_ENCODER.encode(x).decode(encoding="utf-8")


class RegisteredMethod(NamedTuple):
    """Data structure for a registered remote method."""

    method: RemoteMethod[Struct]
    return_type: type[Struct]
    cog_instance: Cog
    struct: type[Struct]


class ErrorResponse(Struct, tag_field="status", tag="success"):
    """Error response for remote bot calls."""

    error: str


class SuccessResponse[T: Struct](Struct, tag_field="status", tag="success"):
    """Success response for remote bot calls."""

    data: T


class RemoteCallRequestBody(Struct):
    """Request to call a method on a remote bot."""

    cog_name: str
    method_name: str
    kwargs: dict[str, Any] = {}  # pyright: ignore[reportExplicitAny]


class RemoteBotConfig(BaseModel):
    """Configuration for a remote bot instance.

    Attributes:
        name: Unique identifier for the remote bot
        base_url: Base URL of the remote bot's API
        api_key: Optional API key for authentication

    """

    name: str
    base_url: str = Field(..., min_length=1)
    api_key: str | None = None


class RemoteBotInstance:
    """Runtime instance of a remote bot configuration.

    Attributes:
        name: Unique identifier for the remote bot
        base_url: Base URL of the remote bot's API
        api_key: Optional API key for authentication

    """

    def __init__(self, name: str, base_url: str, api_key: str | None = None) -> None:
        """Initialize remote bot instance.

        Args:
            name: Unique identifier for the remote bot
            base_url: Base URL of the remote bot's API
            api_key: Optional API key for authentication

        """
        self.name: str = name
        self.base_url: str = base_url.rstrip("/")
        self.api_key: str | None = api_key


class RemoteBotService(BaseService, ConfigSubscriberMixin):
    """Service for managing remote bot communication.

    Provides HTTP server for incoming remote requests and HTTP client
    for making requests to remote bots. Handles authentication, method
    registry, and automatic msgspec.Struct conversion.

    Attributes:
        host: Host address for the HTTP server
        port: Port for the HTTP server
        remote_bots: Dictionary of configured remote bot instances
        method_registry: Registry of remotely callable methods

    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8001) -> None:
        """Initialize the remote bot service.

        Args:
            host: Host address for the HTTP server
            port: Port for the HTTP server

        """
        super().__init__()
        self.host: str = host
        self.port: int = port
        self.remote_bots: dict[str, RemoteBotInstance] = {}
        self.method_registry: dict[str, dict[str, RegisteredMethod]] = {}
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._session: aiohttp.ClientSession | None = None
        self._api_key: str | None = None

    @override
    async def setup(self) -> None:
        """Set up the HTTP server and load configuration."""
        # Load configuration
        config_service = self.global_context.require_service(ConfigService)

        if config_service:
            try:
                self.subscribe("remote_bots", list[RemoteBotConfig])
            except KeyError:
                LOGGER.debug("No remote_bots configuration found")

        # Create aiohttp application
        self._app = web.Application()
        __ = self._app.add_routes(
            [web.post("/api/remote-call", self._handle_remote_call)]
        )

        # Start HTTP server
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        LOGGER.info(f"Remote bot service started on {self.host}:{self.port}")

        # Create HTTP client session
        self._session = aiohttp.ClientSession()

    @override
    async def teardown(self) -> None:
        """Tear down the HTTP server and client."""
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        if self._session:
            await self._session.close()
        LOGGER.info("Remote bot service stopped")

    def register_method[T: Struct](
        self,
        cog_name: str,
        method_name: str,
        bound_method: RemoteMethod[T],
        return_type: type[T],
        cog_instance: Cog,
    ) -> None:
        """Register a remotely callable method.

        Args:
            cog_name: Name of the Cog
            method_name: Name of the method
            bound_method: Bound method instance
            return_type: Return type of the method
            cog_instance: Instance of the Cog

        """
        if cog_name not in self.method_registry:
            self.method_registry[cog_name] = {}
        sig = inspect.signature(bound_method)
        method_sig: list[tuple[str, type]] = []
        for param_name, param in sig.parameters.items():
            if param.annotation is inspect.Parameter.empty:  # pyright: ignore[reportAny]
                msg = (
                    f"Parameter '{param_name}' in method '{cog_name}.{method_name}' "
                    "must have a type annotation"
                )
                raise ValueError(msg)
            if not isinstance(param.annotation, type):  # pyright: ignore[reportAny]
                msg = (
                    f"Parameter '{param_name}' in method '{cog_name}.{method_name}' "
                    "has an invalid type annotation"
                )
                raise TypeError(msg)
            method_sig.append((param_name, param.annotation))
        def_struct = defstruct(method_name, method_sig)
        self.method_registry[cog_name][method_name] = RegisteredMethod(
            bound_method,
            return_type,
            cog_instance,
            def_struct,
        )
        LOGGER.debug(f"Registered remote method: {cog_name}.{method_name}")

    @override
    def on_config_changed(self, config: object, changed_fields: set[str]) -> None:
        """Handle configuration changes.

        Args:
            config: The new configuration
            changed_fields: Set of changed field names

        """
        if isinstance(config, list):
            self.remote_bots.clear()
            for bot_config in config:  # pyright: ignore[reportUnknownVariableType]
                if isinstance(bot_config, RemoteBotConfig):
                    instance = RemoteBotInstance(
                        name=bot_config.name,
                        base_url=bot_config.base_url,
                        api_key=bot_config.api_key,
                    )
                    self.remote_bots[instance.name] = instance
            LOGGER.info(f"Loaded {len(self.remote_bots)} remote bot configurations")

    def get_remote_bot(self, name: str) -> RemoteBotInstance:
        """Get a remote bot instance by name.

        Args:
            name: Name of the remote bot

        Returns:
            RemoteBotInstance: The remote bot instance

        Raises:
            KeyError: If bot not found

        """
        if name not in self.remote_bots:
            msg = f"Remote bot '{name}' not found"
            raise KeyError(msg)
        return self.remote_bots[name]

    async def _handle_remote_call(
        self,
        request: web.Request,
    ) -> web.Response:
        """Handle incoming remote call requests.

        Args:
            request: The aiohttp request

        Returns:
            web.Response: JSON response

        """
        try:
            # Check authentication
            provided_key = request.headers.get("X-API-Key")
            if self._api_key and provided_key != self._api_key:
                return web.json_response(
                    {"status": "error", "error": "Authentication failed"}, status=401
                )

            encoder = json.Encoder()

            # Parse request body
            body = cast(
                "RemoteCallRequestBody",
                await request.json(
                    loads=lambda x: json.decode(x, type=RemoteCallRequestBody)
                ),
            )
            cog_name = body.cog_name
            method_name = body.method_name
            kwargs = body.kwargs

            if not cog_name or not method_name:
                return web.json_response(
                    data=ErrorResponse(error="cog_name and method_name are required"),
                    status=400,
                    dumps=dumps,
                )

            # Look up method in registry
            if cog_name not in self.method_registry:
                return web.json_response(
                    data=ErrorResponse(error=f"Cog '{cog_name}' not found"),
                    status=404,
                    dumps=dumps,
                )

            if method_name not in self.method_registry[cog_name]:
                error_msg = f"Method '{method_name}' not found in '{cog_name}'"
                return web.json_response(
                    data=ErrorResponse(error=error_msg),
                    status=404,
                    dumps=dumps,
                )

            bound_method, __, cog_instance, struct = self.method_registry[cog_name][
                method_name
            ]

            # Get encoder/decoder hooks from cog instance if available
            enc_hook = getattr(cog_instance, "_remote_enc_hook", None)
            dec_hook = getattr(cog_instance, "_remote_dec_hook", None)

            encoder = json.Encoder(enc_hook=enc_hook)
            kwargs_struct = convert(kwargs, struct, dec_hook=dec_hook)

            # Call the method
            call_result = bound_method(**structs.asdict(kwargs_struct))
            if inspect.isawaitable(call_result):
                result = await call_result
            else:
                result = call_result

            # Encode result to JSON
            response = SuccessResponse(data=result)

            return web.json_response(
                data=response,
                status=200,
                dumps=lambda x: encoder.encode(x).decode(encoding="utf-8"),  # pyright: ignore[reportAny]
            )

        except Exception as e:
            LOGGER.exception("Error handling remote call")
            return web.json_response(
                data=ErrorResponse(error=str(e)),
                status=500,
                dumps=dumps,
            )

    async def call_remote[T: Struct](
        self,
        remote_bot: RemoteBotInstance,
        cog_name: str,
        method_name: str,
        return_type: type[T],
        kwargs: dict[str, Any] | None = None,  # pyright: ignore[reportExplicitAny]
        enc_hook: Callable[[Any], Any] | None = None,  # pyright: ignore[reportExplicitAny]
        dec_hook: Callable[[type, Any], Any] | None = None,  # pyright: ignore[reportExplicitAny]
    ) -> T:
        """Call a remote method on another bot.

        Args:
            remote_bot: The remote bot instance
            cog_name: Name of the Cog
            method_name: Name of the method
            return_type: Expected return type
            args: Positional arguments
            kwargs: Keyword arguments
            enc_hook: Optional encoder hook
            dec_hook: Optional decoder hook

        Returns:
            The deserialized result

        Raises:
            ConnectionError: If the remote bot is unavailable
            RuntimeError: If the call fails

        """
        if kwargs is None:
            kwargs = {}

        if not self._session:
            msg = "HTTP client session not initialized"
            raise RuntimeError(msg)

        url = f"{remote_bot.base_url}/api/remote-call"
        headers: dict[str, str] = {}
        if remote_bot.api_key:
            headers["X-API-Key"] = remote_bot.api_key

        encoder = json.Encoder(enc_hook=enc_hook)

        request_body = RemoteCallRequestBody(
            cog_name=cog_name,
            method_name=method_name,
            kwargs=kwargs,
        )
        try:
            async with self._session.post(
                url,
                data=encoder.encode(request_body),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                http_ok = 200

                decoder = json.Decoder(
                    type=SuccessResponse[return_type] | ErrorResponse, dec_hook=dec_hook
                )

                response_data = cast(
                    "SuccessResponse[Struct] | ErrorResponse",
                    await response.json(loads=decoder.decode),
                )

                if isinstance(response_data, ErrorResponse):
                    msg = f"Remote call failed: {response_data.error}"
                    raise RuntimeError(msg)  # noqa: TRY004

                if response.status != http_ok:
                    msg = f"Remote call failed with status {response.status}"
                    raise RuntimeError(msg)

                return cast("T", response_data.data)

        except aiohttp.ClientError as e:
            msg = f"Failed to connect to remote bot: {e}"
            raise ConnectionError(msg) from e
        except TimeoutError as e:
            msg = "Remote call timed out"
            raise ConnectionError(msg) from e
