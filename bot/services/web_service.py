"""WebService for managing multiple FastAPI instances."""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, override

import uvicorn
from fastapi import FastAPI
from uvicorn.server import Server

from bot.core.components import BaseService
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigModel, ConfigService

if TYPE_CHECKING:
    from fastapi import APIRouter


LOGGER = logging.getLogger(__name__)


class APIServer(StrEnum):
    """Enum for identifying which FastAPI server instance to use."""

    PUBLIC = "public"
    PRIVATE = "private"


class ServerConfigModel(ConfigModel):
    """Pydantic model for server configuration."""

    config_table_name: ClassVar[str | None] = "services.web.public"

    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8080


class WebServiceConfig:
    """Configuration for a single FastAPI server instance."""

    def __init__(self, host: str, port: int) -> None:
        """Initialize server configuration.

        Args:
            host: Host address to bind to
            port: Port number to listen on

        """
        self.host: str = host
        self.port: int = port


class WebService(BaseService, ConfigSubscriberMixin):
    """Service for managing multiple FastAPI instances.

    This service creates and manages FastAPI applications for different
    server instances (e.g., PUBLIC and PRIVATE). Cogs can register their
    APIRouter instances with specific servers via the include_router method.

    """

    def __init__(self) -> None:
        """Initialize the WebService.

        Args:
            config_path: Path to the configuration file

        """
        super().__init__()
        self._apps: dict[APIServer, FastAPI] = {}
        self._configs: dict[APIServer, WebServiceConfig] = {}
        self._servers: dict[APIServer, Server] = {}
        self._tasks: list[asyncio.Task[None]] = []

    @override
    async def setup(self) -> None:
        """Set up FastAPI instances from configuration."""
        # Wait for ConfigService
        config_service: ConfigService = await self.global_context.wait_for_service(
            ConfigService
        )
        self.inject_config_service(config_service)

        # Load configuration for each server
        for server in APIServer:
            try:
                table_name = f"services.web.{server.value}"
                config = config_service.get_table(ServerConfigModel, table_name)
                self.subscribe_config(ServerConfigModel, table_name)
                self._configs[server] = WebServiceConfig(
                    host=config.host,
                    port=config.port,
                )
                LOGGER.info(
                    "Loaded configuration for %s server: %s:%d",
                    server,
                    config.host,
                    config.port,
                )
            except KeyError:
                LOGGER.warning(f"No configuration found for {server} server, skipping")

        # Create FastAPI apps for each configured server
        for server, config in self._configs.items():
            app = FastAPI(title=f"{server.value.capitalize()} API")
            self._apps[server] = app
            LOGGER.info(
                "Created FastAPI app for %s server on %s:%d",
                server,
                config.host,
                config.port,
            )

        # Start uvicorn servers
        for server, config in self._configs.items():
            app = self._apps[server]

            # Create uvicorn Server instance
            uvicorn_server = Server(
                config=uvicorn.Config(
                    app=app,
                    host=config.host,
                    port=config.port,
                    log_level="info",
                )
            )
            self._servers[server] = uvicorn_server

            # Start server in background task
            task = asyncio.create_task(uvicorn_server.serve())
            self._tasks.append(task)
            LOGGER.info("Started %s server on %s:%d", server, config.host, config.port)

    @override
    async def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ) -> None:
        LOGGER.warning(
            "WebService configuration changed, a restart is required to apply changes"
        )

    @override
    async def teardown(self) -> None:
        """Shutdown all FastAPI servers."""
        # Signal uvicorn servers to shutdown
        for server in self._servers.values():
            server.should_exit = True

        # Wait for tasks to complete
        for task in self._tasks:
            if not task.done():
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except TimeoutError:
                    __ = task.cancel()

        self._tasks.clear()
        self._servers.clear()
        self._apps.clear()
        self._configs.clear()
        LOGGER.info("WebService teardown complete")

    def get_app(self, server: APIServer) -> FastAPI:
        """Get the FastAPI app for a specific server.

        Args:
            server: The APIServer to get the app for

        Returns:
            The FastAPI application instance

        Raises:
            KeyError: If the server is not configured

        """
        if server not in self._apps:
            msg = f"No FastAPI app configured for server {server}"
            raise KeyError(msg)
        return self._apps[server]

    def include_router(self, router: APIRouter, server: APIServer) -> None:
        """Include an APIRouter in a specific FastAPI app.

        Args:
            router: The FastAPI APIRouter to include
            server: The APIServer to include the router in

        Raises:
            KeyError: If the server is not configured

        """
        if server not in self._apps:
            msg = f"Cannot include router in unconfigured server {server}"
            raise KeyError(msg)
        app = self._apps[server]
        app.include_router(router)
        LOGGER.debug("Included router in %s server", server)
