"""Prometheus collector mixin for Cogs to define async metric collectors."""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any, cast

from bot.clients.prometheus import Counter, Gauge, Histogram, Info, Summary
from bot.clients.prometheus import MetricType as PrometheusMetricType
from bot.services.prometheus_service import PrometheusService

if TYPE_CHECKING:
    from collections.abc import Callable
    from inspect import _GetMembersReturn

    from bot.core.components import GlobalContext
    from bot.services.prometheus_service import CollectorFunc

LOGGER = logging.getLogger(__name__)


class CollectorDefinition:
    """Stores information about a Prometheus collector definition."""

    def __init__(
        self,
        metric_type: type,
        labelnames: tuple[str, ...] | None,
        name: str | None,
        description: str | None,
        buckets: tuple[float, ...] | None,
        quantiles: tuple[float, ...] | None,
    ) -> None:
        """Initialize a collector definition.

        Args:
            metric_type: The metric type (Counter, Gauge, Histogram, Summary, Info).
            labelnames: The label names for the metric.
            name: Optional override for the metric name.
            description: Optional override for the metric description.
            buckets: Bucket values for histograms.
            quantiles: Quantile values for summaries.

        """
        self.metric_type: type = metric_type
        self.labelnames: tuple[str, ...] | None = labelnames
        self.name: str | None = name
        self.description: str | None = description
        self.buckets: tuple[float, ...] | None = buckets
        self.quantiles: tuple[float, ...] | None = quantiles


def _create_collector_decorator[T: Callable[..., Any]](
    metric_type: type,
) -> Callable[..., Callable[..., Callable[[T], T]]]:
    """Create a decorator for a specific metric type.

    Args:
        metric_type: The metric class (Counter, Gauge, Histogram, Summary, Info).

    Returns:
        The decorator function.

    """

    def decorator(
        labelnames: tuple[str, ...] | None = None,
        name: str | None = None,
        description: str | None = None,
        buckets: tuple[float, ...] | None = None,
        quantiles: tuple[float, ...] | None = None,
    ) -> Callable[..., Callable[[T], T]]:
        """Decorator to mark a method as a metric collector.

        Args:
            labelnames: The label names for the metric.
            name: Optional override for the metric name.
            description: Optional override for the metric description.
            buckets: Bucket values for histograms.
            quantiles: Quantile values for summaries.

        Returns:
            The decorator function.

        """

        def decorate(func: T) -> T:
            """Decorate the collector function.

            Args:
                func: The collector function.

            Returns:
                The decorated function.

            """
            if not hasattr(func, "__collector_definition__"):
                setattr(func, "__collector_definition__", None)
            setattr(
                func,
                "__collector_definition__",
                CollectorDefinition(
                    metric_type,
                    labelnames,
                    name,
                    description,
                    buckets,
                    quantiles,
                ),
            )
            return func

        return decorate

    return decorator


# Type-specific decorators
counter_collector = _create_collector_decorator(Counter)
gauge_collector = _create_collector_decorator(Gauge)
histogram_collector = _create_collector_decorator(Histogram)
summary_collector = _create_collector_decorator(Summary)
info_collector = _create_collector_decorator(Info)


class PrometheusCollectorMixin:
    """Mixin for Cogs to enable async metric collectors.

    Cogs inheriting from this mixin can define metric collectors using the
    @counter_collector, @gauge_collector, @histogram_collector,
    @summary_collector, and @info_collector decorators. Collectors are
    registered by calling register_prometheus_collectors() in the cog's
    setup() method.

    """

    async def register_prometheus_collectors(
        self, prometheus_service: PrometheusService | None = None
    ) -> None:
        """Register all Prometheus collectors defined on this cog.

        This method should be called in the cog's setup() method.
        It inspects the cog for methods decorated with collector decorators,
        creates the corresponding metric objects via PrometheusService factory
        methods, and registers the collector functions.

        Args:
            prometheus_service: The PrometheusService instance. If None,
                it will be retrieved from the global context.

        """
        if prometheus_service is None:
            global_context: GlobalContext | None = getattr(self, "global_context", None)
            if global_context is None:
                msg = (
                    "This mixin is meant to be used in a Cog "
                    "with access to the global context."
                )
                raise RuntimeError(msg)

            try:
                prometheus_service = await global_context.wait_for_service(
                    PrometheusService  # type: ignore[arg-type]
                )
            except (TimeoutError, RuntimeError) as e:
                LOGGER.warning(
                    "PrometheusService not available, "
                    "skipping collector registration: %s",
                    e,
                )
                return

        # Find all methods with collector definitions
        members: _GetMembersReturn[CollectorFunc] = inspect.getmembers(
            self,
            lambda x: hasattr(x, "__collector_definition__"),  # pyright: ignore[reportAny]
        )

        if not members:
            LOGGER.debug(
                "No Prometheus collectors found in cog %s", self.__class__.__name__
            )
            return

        for method_name, collector_func in members:
            definition = cast(
                "CollectorDefinition | None",
                getattr(collector_func, "__collector_definition__"),
            )
            if definition is None:
                continue

            # Extract metric name and description
            metric_name = definition.name or method_name
            metric_description = definition.description
            if metric_description is None:
                # Extract from docstring
                docstring = inspect.getdoc(collector_func)
                if docstring:
                    metric_description = docstring.split("\n")[0].strip()
                else:
                    metric_description = metric_name

            # Create metric object via PrometheusService factory
            metric: Counter | Gauge | Histogram | Summary | Info
            labelnames = definition.labelnames or ()

            if definition.metric_type == Counter:
                metric = await prometheus_service.create_counter(
                    metric_name, metric_description, labelnames
                )
            elif definition.metric_type == Gauge:
                metric = await prometheus_service.create_gauge(
                    metric_name, metric_description, labelnames
                )
            elif definition.metric_type == Histogram:
                metric = await prometheus_service.create_histogram(
                    metric_name,
                    metric_description,
                    labelnames,
                    definition.buckets,
                )
            elif definition.metric_type == Summary:
                metric = await prometheus_service.create_summary(
                    metric_name,
                    metric_description,
                    labelnames,
                    definition.quantiles,
                )
            elif definition.metric_type == Info:
                metric = await prometheus_service.create_info(
                    metric_name, metric_description, labelnames
                )
            else:
                LOGGER.warning(
                    "Unknown metric type %s for collector %s",
                    definition.metric_type,
                    method_name,
                )
                continue

            # Register the collector

            metric_type_map = {
                Counter: PrometheusMetricType.COUNTER,
                Gauge: PrometheusMetricType.GAUGE,
                Histogram: PrometheusMetricType.HISTOGRAM,
                Summary: PrometheusMetricType.SUMMARY,
                Info: PrometheusMetricType.INFO,
            }

            prometheus_service.register_collector(
                cog_name=self.__class__.__name__,
                metric_name=metric_name,
                collector_func=collector_func,
                metric=metric,
                metric_type=metric_type_map.get(
                    definition.metric_type, PrometheusMetricType.GAUGE
                ),
                description=metric_description,
            )

            LOGGER.info(
                "Registered %s collector '%s' in cog %s",
                definition.metric_type,
                metric_name,
                self.__class__.__name__,
            )
