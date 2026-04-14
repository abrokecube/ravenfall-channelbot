"""FastAPI routes mixin for Cogs to define HTTP endpoints."""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any, override

if TYPE_CHECKING:
    from collections.abc import Callable

    from bot.core.components import GlobalContext
    from bot.services.web_service import APIServer, WebService

from fastapi import APIRouter

from bot.services.web_service import WebService

LOGGER = logging.getLogger(__name__)


class EndpointDefinition:
    """Stores information about a FastAPI endpoint definition."""

    def __init__(
        self,
        method: str,
        server: APIServer,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Initialize an endpoint definition.

        Args:
            method: HTTP method (GET, POST, etc.)
            server: Which FastAPI server instance to use
            *args: Additional positional arguments (path is first)
            **kwargs: Additional keyword arguments for the route

        """
        self.method: str = method
        self.server: APIServer = server
        self.path: str = args[0] if len(args) > 0 else kwargs.get("path", "")
        self.args: tuple[Any, ...] = args[1:]
        self.kwargs: dict[str, Any] = kwargs

    def include_in_router(
        self,
        endpoint: Callable[..., Any],
        router: APIRouter,
        **defaults_route_args: Any,
    ) -> None:
        """Include this endpoint in a FastAPI router.

        Args:
            endpoint: The endpoint function
            router: The FastAPI APIRouter instance
            **defaults_route_args: Default route arguments

        """
        kwargs = {**defaults_route_args, **self.kwargs}
        router.add_api_route(
            self.path, endpoint, *self.args, methods=[self.method], **kwargs
        )


class APIControllerDecorator:
    """Emulates FastAPI endpoint decorators.

    Intercepts calls to them and records these in an endpoint method custom
    property to replay it later when included in a router.
    """

    @override
    def __getattribute__[T: Callable[..., Any]](
        self, _name: str
    ) -> Callable[..., Callable[[T], T]]:
        """Return a decorator factory for FastAPI route methods.

        Args:
            _name: The decorator name (e.g., 'get', 'post')

        Returns:
            A decorator factory function

        """
        allowed_methods = [
            "api_route",
            "delete",
            "get",
            "head",
            "options",
            "patch",
            "post",
            "put",
            "trace",
        ]
        if _name not in allowed_methods:
            msg = f"Method {_name} not allowed in APIControllerDecorator"
            raise ValueError(msg)
        return APIControllerDecorator._intercept_method(_name)

    @staticmethod
    def _intercept_method[T: Callable[..., Any]](
        method: str,
    ) -> Callable[..., Callable[[T], T]]:
        """Create a decorator factory for a specific HTTP method.

        Args:
            method: The HTTP method name

        Returns:
            A decorator factory function

        """

        def decorator_factory(
            server: APIServer, /, *args: Any, **kwargs: Any
        ) -> Callable[[T], T]:
            """Decorator factory that takes server and route parameters.

            Args:
                server: The APIServer to use
                *args: Route path and additional positional args
                **kwargs: Additional route kwargs

            Returns:
                The actual decorator function

            """

            def decorate(endpoint: T) -> T:
                """Decorate an endpoint function.

                Args:
                    endpoint: The endpoint function to decorate

                Returns:
                    The decorated endpoint function

                """
                if not hasattr(endpoint, "__endpoint_definitions__"):
                    setattr(endpoint, "__endpoint_definitions__", [])
                __ = getattr(endpoint, "__endpoint_definitions__", []).append(
                    EndpointDefinition(method, server, *args, **kwargs)
                )
                return endpoint

            return decorate

        return decorator_factory


# Global decorator instance
api_route: APIControllerDecorator = APIControllerDecorator()


class FastAPIRoutesMixin:
    """Mixin for Cogs to enable FastAPI route definitions.

    Cogs inheriting from this mixin can define HTTP endpoints using the
    @api_route decorator. Routes are registered by calling register_fastapi_routes()
    in the cog's setup() method.

    """

    async def register_fastapi_routes(
        self, web_service: WebService | None = None
    ) -> None:
        """Register all FastAPI routes defined on this cog.

        This method should be called in the cog's setup() method.
        It inspects the cog for methods decorated with @api_route,
        groups them by APIServer, builds APIRouter instances, and
        includes them in the appropriate WebService FastAPI app.

        """
        if web_service is None:
            global_context: GlobalContext | None = getattr(self, "global_context", None)
            if global_context is None:
                msg = (
                    "This mixin is meant to be used in a Cog "
                    "with access to the global context."
                )
                raise RuntimeError(msg)

            try:
                web_service = await global_context.wait_for_service(
                    WebService  # type: ignore[arg-type]
                )
            except (TimeoutError, RuntimeError) as e:
                LOGGER.warning(
                    "WebService not available, skipping route registration: %s", e
                )
                return

        # Find all methods with endpoint definitions
        members = inspect.getmembers(
            self,
            lambda x: hasattr(x, "__endpoint_definitions__"),
        )

        if not members:
            LOGGER.debug("No FastAPI routes found in cog %s", self.__class__.__name__)
            return

        # Group endpoint definitions by server
        routes_by_server: dict[
            APIServer,
            list[tuple[str, Callable[..., Any], EndpointDefinition]],
        ] = {}

        for _, endpoint in members:
            definitions = getattr(endpoint, "__endpoint_definitions__")
            for definition in definitions:
                if definition.server not in routes_by_server:
                    routes_by_server[definition.server] = []
                routes_by_server[definition.server].append(("", endpoint, definition))

        # Create routers and include them for each server
        for server, routes in routes_by_server.items():
            router = APIRouter()
            for _, endpoint, definition in routes:
                LOGGER.debug(
                    "Configuring route '%s' on server %s: %s%s",
                    definition.path,
                    server,
                    endpoint.__name__,
                    inspect.signature(endpoint),
                )
                definition.include_in_router(endpoint, router)

            web_service.include_router(router, server)
            LOGGER.info(
                "Registered %d routes on server %s for cog %s",
                len(routes),
                server,
                self.__class__.__name__,
            )
