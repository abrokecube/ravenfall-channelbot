"""Test Cog for Prometheus metrics functionality."""

from __future__ import annotations

import asyncio
import contextlib
import random
from typing import TYPE_CHECKING

from bot.core.components import Cog
from bot.mixins.prometheus_collector import (
    PrometheusCollectorMixin,
    counter_collector,
    gauge_collector,
    histogram_collector,
    info_collector,
    summary_collector,
)
from bot.services.prometheus_service import PrometheusService

if TYPE_CHECKING:
    from bot.clients.prometheus import Counter, Gauge, Histogram, Info, Summary


class PrometheusTestCog(Cog, PrometheusCollectorMixin):
    """Test Cog for demonstrating Prometheus metrics functionality."""

    def __init__(self, event_manager) -> None:
        super().__init__(event_manager)
        self._counter: int = 0
        self._gauge_value: float = 0.0
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None

    async def setup(self) -> None:
        """Set up the cog."""
        await super().setup()

        # Register collectors with PrometheusService
        await self.register_prometheus_collectors()

        # Start background task to update metrics
        self._running = True
        self._task = asyncio.create_task(self._update_metrics_loop())
        await self.example_direct_metric_usage()

    async def teardown(self) -> None:
        """Tear down the cog."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await super().teardown()

    async def _update_metrics_loop(self) -> None:
        """Background loop to update metrics periodically."""
        while self._running:
            await asyncio.sleep(5)
            self._counter += 1
            self._gauge_value = random.random() * 100

    # Collector functions
    @counter_collector(labelnames=("test_label",))
    async def test_counter(self, metric: Counter) -> None:
        """Test counter metric."""
        __ = await metric.labels(test_label="value1").inc(1)
        __ = await metric.labels(test_label="value2").inc(2)

    @gauge_collector(labelnames=("status",))
    async def test_gauge(self, metric: Gauge) -> None:
        """Test gauge metric."""
        __ = await metric.labels(status="active").set(self._gauge_value)
        __ = await metric.labels(status="inactive").set(0)

    @histogram_collector(
        labelnames=("operation",), buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0)
    )
    async def test_histogram(self, metric: Histogram) -> None:
        """Test histogram metric."""
        print("aga")
        __ = await metric.labels(operation="read").observe(random.random() * 10)
        __ = await metric.labels(operation="write").observe(random.random() * 5)

    @summary_collector(labelnames=("service",), quantiles=(0.5, 0.9, 0.95, 0.99))
    async def test_summary(self, metric: Summary) -> None:
        """Test summary metric."""
        __ = await metric.labels(service="api").observe(random.random() * 100)
        __ = await metric.labels(service="database").observe(random.random() * 50)

    @info_collector(labelnames=("version", "environment"))
    async def test_info(self, metric: Info) -> None:
        """Test info metric."""
        __ = await metric.labels(version="1.0.0", environment="production").info()

    # Direct metric usage example
    async def example_direct_metric_usage(self) -> None:
        """Example of using PrometheusService factory methods directly."""
        prometheus_service = await self.global_context.wait_for_service(PrometheusService)

        # Create metrics directly
        custom_counter = await prometheus_service.create_counter(
            "custom_counter", "A custom counter metric"
        )
        __ = await custom_counter.inc()

        custom_gauge = await prometheus_service.create_gauge(
            "custom_gauge", "A custom gauge metric"
        )
        __ = await custom_gauge.set(42.0)

        custom_histogram = await prometheus_service.create_histogram(
            "custom_histogram", "A custom histogram metric"
        )
        __ = await custom_histogram.observe(1.5)

        custom_summary = await prometheus_service.create_summary(
            "custom_summary", "A custom summary metric"
        )
        __ = await custom_summary.observe(10.0)

        custom_info = await prometheus_service.create_info(
            "custom_info", "A custom info metric"
        )
        __ = await custom_info.labels(build="123").info()
