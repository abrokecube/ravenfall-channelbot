"""Prometheus service for managing metrics and collectors."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable
from typing import TYPE_CHECKING, override

import aiohttp
import msgspec
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from bot.core.components import BaseService
from bot.metrics.prometheus import (
    Counter,
    Gauge,
    Histogram,
    Info,
    Metrics,
    Summary,
)
from bot.services.web_service import APIServer, WebService

if TYPE_CHECKING:
    from collections.abc import Callable

    from bot.metrics.prometheus import (
        MetricType,
    )

LOGGER = logging.getLogger(__name__)

type CollectorFunc = Callable[
    [Counter | Gauge | Histogram | Summary | Info], Awaitable[None]
]


# Prometheus query response structs
class InstantQueryResult(msgspec.Struct):
    """Result from an instant Prometheus query."""

    metric: dict[str, str]
    value: list[float]


class RangeQueryResult(msgspec.Struct):
    """Result from a range Prometheus query."""

    metric: dict[str, str]
    values: list[list[float]]


class InstantQueryResponse(msgspec.Struct):
    """Wrapper for instant query API responses."""

    status: str
    data: dict[str, list[InstantQueryResult]]


class RangeQueryResponse(msgspec.Struct):
    """Wrapper for range query API responses."""

    status: str
    data: dict[str, list[RangeQueryResult]]


class PrometheusServiceConfig(BaseModel):
    """Configuration for PrometheusService."""

    enabled: bool = True
    collector_timeout_s: int = 10
    metrics_server: str = "private"


class PrometheusQueryConfig(BaseModel):
    """Configuration for Prometheus query servers."""

    servers: dict[str, str] = {"main": "http://localhost:9090"}
    default: str = "main"


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


class PrometheusService(BaseService):
    """Service for managing Prometheus metrics and collectors."""

    def __init__(self) -> None:
        super().__init__()
        self._metrics: Metrics = Metrics()
        self._collectors: dict[str, CollectorDefinition] = {}
        self._config: PrometheusServiceConfig = PrometheusServiceConfig()
        self._query_config: PrometheusQueryConfig = PrometheusQueryConfig()
        self._http_client: aiohttp.ClientSession | None = None

    @override
    async def setup(self) -> None:
        """Set up the Prometheus service."""
        self._http_client = aiohttp.ClientSession()

        # Integrate with WebService

        web_service = await self.global_context.wait_for_service(WebService)

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
        self, query: str, server_id: str | None = None, server_url: str | None = None
    ) -> InstantQueryResponse:
        """Query an external Prometheus server.

        Args:
            query: The Prometheus query.
            server_id: The server ID from config.
            server_url: Override URL for the server.

        Returns:
            The query result as a typed struct.

        """
        if server_url:
            url = f"{server_url}/api/v1/query"
        elif server_id:
            if server_id not in self._query_config.servers:
                LOGGER.warning(
                    "Invalid server_id '%s' specified, falling back to default",
                    server_id,
                )

            server_url = self._query_config.servers.get(
                server_id, self._query_config.servers[self._query_config.default]
            )
            url = f"{server_url}/api/v1/query"
        else:
            server_url = self._query_config.servers[self._query_config.default]
            url = f"{server_url}/api/v1/query"

        if not self._http_client:
            msg = "HTTP client not initialized"
            raise RuntimeError(msg)

        response = await self._http_client.get(url, params={"query": query})
        response.raise_for_status()
        json_data = await response.json()  # pyright: ignore[reportAny]
        return msgspec.convert(json_data, InstantQueryResponse)

    async def query_range(
        self,
        query: str,
        duration_s: int,
        step_s: int = 20,
        server_id: str | None = None,
        server_url: str | None = None,
    ) -> RangeQueryResponse:
        """Query an external Prometheus server with a range.

        Args:
            query: The Prometheus query.
            duration_s: The duration in seconds.
            step_s: The step size in seconds.
            server_id: The server ID from config.
            server_url: Override URL for the server.

        Returns:
            The query result as a typed struct.

        """
        if server_url:
            url = f"{server_url}/api/v1/query_range"
        elif server_id:
            if server_id not in self._query_config.servers:
                LOGGER.warning(
                    "Invalid server_id '%s' specified, falling back to default",
                    server_id,
                )
            server_url = self._query_config.servers.get(
                server_id, self._query_config.servers[self._query_config.default]
            )
            url = f"{server_url}/api/v1/query_range"
        else:
            server_url = self._query_config.servers[self._query_config.default]
            url = f"{server_url}/api/v1/query_range"

        if not self._http_client:
            msg = "HTTP client not initialized"
            raise RuntimeError(msg)

        params = {
            "query": query,
            "start": time.time() - duration_s,
            "end": time.time(),
            "step": step_s,
        }

        response = await self._http_client.get(url, params=params)
        response.raise_for_status()
        json_data = await response.json()  # pyright: ignore[reportAny]
        return msgspec.convert(json_data, RangeQueryResponse)
