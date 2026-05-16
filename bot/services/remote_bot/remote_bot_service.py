"""Remote bot service for inter-bot communication via REST API."""

from __future__ import annotations

import inspect
import logging
import types  # noqa: TC003
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, NamedTuple, cast, override

import aiohttp
from fastapi import HTTPException
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from msgspec import Struct, convert, defstruct, json, structs
from pydantic import BaseModel, Field

from bot.core.components import BaseService
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.mixins.fastapi_routes import FastAPIRoutesMixin, api_route
from bot.services.config_service import ConfigService
from bot.services.web_service import APIServer

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import Request

    from bot.core.components import Cog

LOGGER = logging.getLogger(__name__)

type RemoteMethodAsync[T] = Callable[..., T | Awaitable[T]]

DEFAULT_ENCODER = json.Encoder()


def dumps(x: Any) -> str:  # pyright: ignore[reportExplicitAny, reportAny]
    """Default JSON encoder using msgspec."""
    return DEFAULT_ENCODER.encode(x).decode(encoding="utf-8")


class RegisteredMethod(NamedTuple):
    """Data structure for a registered remote method."""

    method: RemoteMethodAsync[object]
    return_type: type[object] | types.UnionType
    cog_instance: Cog
    struct: type[Struct]


class ErrorResponse(Struct, tag_field="status", tag="success"):
    """Error response for remote bot calls."""

    error: str


class SuccessResponse[T](Struct, tag_field="status", tag="success"):
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


class RemoteBotService(BaseService, ConfigSubscriberMixin, FastAPIRoutesMixin):
    """Service for managing remote bot communication.

    Provides HTTP server for incoming remote requests and HTTP client
    for making requests to remote bots. Handles authentication, method
    registry, and automatic msgspec.Struct conversion.

    Attributes:
        remote_bots: Dictionary of configured remote bot instances
        method_registry: Registry of remotely callable methods

    """

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize the remote bot service.

        Args:
            api_key: Optional API key for authentication

        """
        super().__init__()
        self.remote_bots: dict[str, RemoteBotInstance] = {}
        self.method_registry: dict[str, dict[str, RegisteredMethod]] = {}
        self._session: aiohttp.ClientSession | None = None
        self._api_key: str | None = api_key

    @override
    async def setup(self) -> None:
        """Set up the service and load configuration."""
        # Load configuration
        config_service = await self.global_context.wait_for_service(ConfigService)
        self.inject_config_service(config_service)

        try:
            __ = self.subscribe_config(list[RemoteBotConfig], "services.remote_bots")
        except KeyError:
            LOGGER.debug("No remote_bots configuration found")

        # Register FastAPI routes
        await self.register_fastapi_routes()

        # Create HTTP client session
        self._session = aiohttp.ClientSession()
        LOGGER.info("Remote bot service started")

    @override
    async def teardown(self) -> None:
        """Tear down the HTTP client."""
        if self._session:
            await self._session.close()
        LOGGER.info("Remote bot service stopped")

    def register_method[T: Struct](
        self,
        cog_name: str,
        method_name: str,
        bound_method: RemoteMethodAsync[T],
        return_type: type[T] | types.UnionType,
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
    def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ) -> None:
        """Handle configuration changes.

        Args:
            table: The config table name
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

    @api_route.post(APIServer.PRIVATE, "/api/remote-call")
    async def _handle_remote_call(
        self,
        request: Request,
    ) -> JSONResponse:
        """Handle incoming remote call requests.

        Args:
            request: The FastAPI request

        Returns:
            JSONResponse: JSON response

        """
        try:
            # Check authentication
            provided_key = request.headers.get("X-API-Key")
            if self._api_key and provided_key != self._api_key:
                raise HTTPException(
                    status_code=http_status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication failed",
                )

            encoder = json.Encoder()

            # Parse request body
            body_bytes = await request.body()
            body = json.decode(body_bytes, type=RemoteCallRequestBody)
            cog_name = body.cog_name
            method_name = body.method_name
            kwargs = body.kwargs

            if not cog_name or not method_name:
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail="cog_name and method_name are required",
                )

            # Look up method in registry
            if cog_name not in self.method_registry:
                raise HTTPException(
                    status_code=http_status.HTTP_404_NOT_FOUND,
                    detail=f"Cog '{cog_name}' not found",
                )

            if method_name not in self.method_registry[cog_name]:
                error_msg = f"Method '{method_name}' not found in '{cog_name}'"
                raise HTTPException(
                    status_code=http_status.HTTP_404_NOT_FOUND,
                    detail=error_msg,
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

            return JSONResponse(
                content=encoder.encode(response).decode(encoding="utf-8"),
                status_code=http_status.HTTP_200_OK,
            )

        except HTTPException:
            raise
        except Exception as e:
            LOGGER.exception("Error handling remote call")
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from None

    async def call_remote[T](
        self,
        remote_bot: RemoteBotInstance,
        cog_name: str,
        method_name: str,
        return_type: type[T] | types.UnionType,
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
