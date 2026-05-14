"""Prometheus service for managing metrics and collectors."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Generic, Literal, NamedTuple, TypeVar, override

import aiohttp
import msgspec
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import Field, model_validator

from bot.clients.prometheus import (
    Counter,
    Gauge,
    Histogram,
    Info,
    Metrics,
    Summary,
)
from bot.core.components import BaseService
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigModel, ConfigService
from bot.services.web_service import APIServer, WebService

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import ClassVar

    from bot.clients.prometheus import (
        MetricType,
    )

LOGGER = logging.getLogger(__name__)

type CollectorFunc = Callable[
    [Counter | Gauge | Histogram | Summary | Info], Awaitable[None]
]


class FloatValue(NamedTuple):
    """Timestamped value from Prometheus."""

    timestamp: float
    value: float


class Vector(msgspec.Struct):
    """Vector value."""

    metric: dict[str, str]
    value: FloatValue


class VectorResult(msgspec.Struct, tag_field="resultType", tag="vector"):
    """Vector result."""

    result: list[Vector]


class Matrix(msgspec.Struct):
    """Matrix value."""

    metric: dict[str, str]
    values: list[FloatValue]


class MatrixResult(msgspec.Struct, tag_field="resultType", tag="matrix"):
    """Matrix result."""

    result: list[Matrix]


T = TypeVar("T")


class PrometheusResponse(msgspec.Struct, Generic[T]):  # noqa: UP046
    """Response from Prometheus."""

    status: Literal["success"]
    data: T


class PrometheusServiceConfig(ConfigModel):
    """Configuration for PrometheusService."""

    config_table_name: ClassVar[str | None] = "services.prometheus"

    enabled: bool = True
    collector_timeout_s: int = 10
    metrics_server: str = "private"
    default_query_server: str = "main"
    servers: dict[str, str] = Field(
        default_factory=lambda: {"main": "http://localhost:9090"}
    )

    @model_validator(mode="after")
    def validate_config(self):
        """Validate that the default_query_server is defined in servers."""
        if self.default_query_server not in self.servers:
            msg = "default_query_server must be a valid server ID defined in servers"
            raise ValueError(msg)
        return self


class CollectorDefinition:
    """Stores information about a registered collector."""

    def __init__(
        self,
        cog_name: str,
        metric_name: str,
        collector_func: CollectorFunc,
        metric: Counter | Gauge | Histogram | Summary | Info,
        metric_type: MetricType,
        description: str,
    ) -> None:
        self.cog_name: str = cog_name
        self.metric_name: str = metric_name
        self.collector_func: CollectorFunc = collector_func
        self.metric: Counter | Gauge | Histogram | Summary | Info = metric
        self.metric_type: MetricType = metric_type
        self.description: str = description


class PrometheusService(BaseService, ConfigSubscriberMixin):
    """Service for managing Prometheus metrics and collectors."""

    def __init__(self) -> None:
        super().__init__()
        self._metrics: Metrics = Metrics()
        self._collectors: dict[str, CollectorDefinition] = {}
        self._config: PrometheusServiceConfig = PrometheusServiceConfig()
        self._http_client: aiohttp.ClientSession | None = None

    @override
    async def setup(self) -> None:
        """Set up the Prometheus service."""
        self._http_client = aiohttp.ClientSession()

        web_service = await self.global_context.wait_for_service(WebService)
        config_service = await self.global_context.wait_for_service(ConfigService)
        self.inject_config_service(config_service)

        self._config = self.subscribe_config(PrometheusServiceConfig)

        router = APIRouter()

        @router.get("/metrics")
        async def metrics_endpoint() -> PlainTextResponse:  # pyright: ignore[reportUnusedFunction]
            """Prometheus metrics endpoint."""
            await self.run_collectors()
            text = await self.get_metrics_text()
            return PlainTextResponse(text, media_type="text/plain")

        # Determine which server to use
        server = (
            APIServer.PRIVATE
            if self._config.metrics_server == "private"
            else APIServer.PUBLIC
        )
        web_service.include_router(router, server)
        LOGGER.info("Registered /metrics endpoint on %s server", server)

    @override
    async def teardown(self) -> None:
        """Tear down the Prometheus service."""
        if self._http_client:
            await self._http_client.close()

    @override
    def on_config_changed(self, table: str, config: object, changed_fields: set[str]):
        """Handle configuration changes."""
        if table == "services.prometheus" and isinstance(config, PrometheusServiceConfig):
            self._config = config
            LOGGER.info("PrometheusService configuration updated: %s", config)

    # Factory methods for metric objects
    async def create_counter(
        self, name: str, description: str, labelnames: tuple[str, ...] = ()
    ) -> Counter:
        """Create a Counter metric."""
        counter = Counter(self._metrics, name, description, labelnames)
        await counter._ensure_definition()
        return counter

    async def create_gauge(
        self, name: str, description: str, labelnames: tuple[str, ...] = ()
    ) -> Gauge:
        """Create a Gauge metric."""
        gauge = Gauge(self._metrics, name, description, labelnames)
        await gauge._ensure_definition()
        return gauge

    async def create_histogram(
        self,
        name: str,
        description: str,
        labelnames: tuple[str, ...] = (),
        buckets: tuple[float, ...] | None = None,
    ) -> Histogram:
        """Create a Histogram metric."""
        histogram = Histogram(self._metrics, name, description, labelnames, buckets)
        await histogram._ensure_definition()
        return histogram

    async def create_summary(
        self,
        name: str,
        description: str,
        labelnames: tuple[str, ...] = (),
        quantiles: tuple[float, ...] | None = None,
    ) -> Summary:
        """Create a Summary metric."""
        summary = Summary(self._metrics, name, description, labelnames, quantiles)
        await summary._ensure_definition()
        return summary

    async def create_info(
        self, name: str, description: str, labelnames: tuple[str, ...] = ()
    ) -> Info:
        """Create an Info metric."""
        info = Info(self._metrics, name, description, labelnames)
        await info._ensure_definition()
        return info

    # Collector registration
    def register_collector(
        self,
        cog_name: str,
        metric_name: str,
        collector_func: CollectorFunc,
        metric: Counter | Gauge | Histogram | Summary | Info,
        metric_type: MetricType,
        description: str,
    ) -> None:
        """Register a collector."""
        key = f"{cog_name}.{metric_name}"
        self._collectors[key] = CollectorDefinition(
            cog_name, metric_name, collector_func, metric, metric_type, description
        )

    async def run_collectors(self) -> None:
        """Run all registered collectors in parallel."""
        if not self._collectors:
            return

        tasks: list[asyncio.Task[None]] = []
        for definition in self._collectors.values():
            task = asyncio.create_task(
                self._run_single_collector(definition),
                name=f"collector_{definition.cog_name}_{definition.metric_name}",
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                LOGGER.exception("Collector failed: %s", result)

    async def _run_single_collector(self, definition: CollectorDefinition) -> None:
        """Run a single collector with timeout."""
        try:
            await asyncio.wait_for(
                definition.collector_func(definition.metric),
                timeout=self._config.collector_timeout_s,
            )
        except TimeoutError:
            LOGGER.warning(
                "Collector %s.%s timed out after %s seconds",
                definition.cog_name,
                definition.metric_name,
                self._config.collector_timeout_s,
            )
        except Exception:
            LOGGER.exception(
                "Collector %s.%s failed",
                definition.cog_name,
                definition.metric_name,
            )

    async def get_metrics_text(self) -> str:
        """Generate Prometheus text format."""
        return await self._metrics.get_text()

    # Query functions for external Prometheus servers
    async def query(
        self,
        query: str,
        server_id: str | None = None,
        server_url: str | None = None,
        time: str | float | None = None,
        timeout: str | None = None,
        limit: int | None = None,
        lookback_delta: str | float | None = None,
        stats: str | None = None,
    ):
        """Query an external Prometheus server.

        Args:
            query: The Prometheus query.
            server_id: The server ID from config.
            server_url: Override URL for the server.
            time: Evaluation timestamp (rfc3339 or unix_timestamp). Optional.
            timeout: Evaluation timeout (duration). Optional.
            limit: Maximum number of returned series. Optional. 0 means disabled.
            lookback_delta: Override lookback period (duration or float seconds). Optional.
            stats: Include query statistics in response. Optional.

        Returns:
            The query result as a typed struct.

        """
        if server_url:
            url = f"{server_url}/api/v1/query"
        elif server_id:
            if server_id not in self._config.servers:
                LOGGER.warning(
                    "Invalid server_id '%s' specified, falling back to default",
                    server_id,
                )

            server_url = self._config.servers.get(
                server_id, self._config.servers[self._config.default_query_server]
            )
            url = f"{server_url}/api/v1/query"
        else:
            server_url = self._config.servers[self._config.default_query_server]
            url = f"{server_url}/api/v1/query"

        if not self._http_client:
            msg = "HTTP client not initialized"
            raise RuntimeError(msg)

        params: dict[str, str | int | float] = {"query": query}
        if time is not None:
            params["time"] = time
        if timeout is not None:
            params["timeout"] = timeout
        if limit is not None:
            params["limit"] = limit
        if lookback_delta is not None:
            params["lookback_delta"] = lookback_delta
        if stats is not None:
            params["stats"] = stats

        response = await self._http_client.get(url, params=params)
        text_data = await response.text()
        response.raise_for_status()
        response_data = msgspec.json.decode(
            text_data, type=PrometheusResponse[VectorResult], strict=False
        )
        return response_data.data.result

    async def query_range(
        self,
        query: str,
        duration_s: int,
        step_s: int = 20,
        server_id: str | None = None,
        server_url: str | None = None,
        timeout: str | None = None,
        limit: int | None = None,
        lookback_delta: str | float | None = None,
        stats: str | None = None,
    ):
        """Query an external Prometheus server with a range.

        Args:
            query: The Prometheus query.
            duration_s: The duration in seconds.
            step_s: The step size in seconds.
            server_id: The server ID from config.
            server_url: Override URL for the server.
            timeout: Evaluation timeout (duration). Optional.
            limit: Maximum number of returned series. Optional. 0 means disabled.
            lookback_delta: Override lookback period (duration or float seconds). Optional.
            stats: Include query statistics in response. Optional.

        Returns:
            The query result as a typed struct.

        """
        if server_url:
            url = f"{server_url}/api/v1/query_range"
        elif server_id:
            if server_id not in self._config.servers:
                LOGGER.warning(
                    "Invalid server_id '%s' specified, falling back to default",
                    server_id,
                )
            server_url = self._config.servers.get(
                server_id, self._config.servers[self._config.default_query_server]
            )
            url = f"{server_url}/api/v1/query_range"
        else:
            server_url = self._config.servers[self._config.default_query_server]
            url = f"{server_url}/api/v1/query_range"

        if not self._http_client:
            msg = "HTTP client not initialized"
            raise RuntimeError(msg)

        params: dict[str, str | int | float] = {
            "query": query,
            "start": time.time() - duration_s,
            "end": time.time(),
            "step": step_s,
        }
        if timeout is not None:
            params["timeout"] = timeout
        if limit is not None:
            params["limit"] = limit
        if lookback_delta is not None:
            params["lookback_delta"] = lookback_delta
        if stats is not None:
            params["stats"] = stats

        response = await self._http_client.get(url, params=params)
        text_data = await response.text()
        response.raise_for_status()
        response_data = msgspec.json.decode(
            text_data, type=PrometheusResponse[MatrixResult], strict=False
        )
        return response_data.data.result
