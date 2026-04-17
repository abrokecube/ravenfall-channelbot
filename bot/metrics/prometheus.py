"""Custom Prometheus metrics library with thread-safe updates."""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot.metrics.prometheus import (
        _CounterChild,
        _GaugeChild,
        _HistogramChild,
        _InfoChild,
        _SummaryChild,
    )


class MetricType(Enum):
    """Prometheus metric types."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"
    INFO = "info"


@dataclass(frozen=True)
class MetricDefinition:
    """Definition of a Prometheus metric."""

    name: str
    description: str
    metric_type: MetricType
    labelnames: tuple[str, ...] = ()
    buckets: tuple[float, ...] | None = None
    quantiles: tuple[float, ...] | None = None


@dataclass(frozen=True)
class MetricEntry:
    """A single metric entry with labels."""

    name: str
    labels: str


# Label escaping translation table
_LABEL_ESCAPE_TABLE = str.maketrans(
    {
        '"': '\\"',
        "\\": "\\\\",
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }
)


def to_label(obj: object) -> str:
    """Convert a value to a Prometheus label string.

    Args:
        obj: The value to convert.

    Returns:
        The escaped label string.

    """
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, str):
        return obj.translate(_LABEL_ESCAPE_TABLE)
    return str(obj)


@dataclass
class Metrics:
    """Thread-safe Prometheus metrics storage."""

    definitions: dict[str, MetricDefinition] = field(default_factory=dict)
    metrics: dict[MetricEntry, float] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def add_value(self, metric_name: str, value: float, **labels: str) -> None:
        """Add a value to a metric with labels.

        Args:
            metric_name: The name of the metric.
            value: The value to add.
            **labels: The label key-value pairs.

        """
        label_str = ",".join([f'{k}="{to_label(v)}"' for k, v in labels.items()])
        entry = MetricEntry(metric_name, label_str)
        if isinstance(value, bool):
            value = 1.0 if value else 0.0
        async with self._lock:
            self.metrics[entry] = float(value)

    async def add_definition(
        self,
        metric_name: str,
        description: str,
        metric_type: MetricType,
        labelnames: tuple[str, ...] = (),
        buckets: tuple[float, ...] | None = None,
        quantiles: tuple[float, ...] | None = None,
    ) -> None:
        """Add a metric definition.

        Args:
            metric_name: The name of the metric.
            description: The description of the metric.
            metric_type: The type of the metric.
            labelnames: The label names for the metric.
            buckets: The bucket values for histograms.
            quantiles: The quantile values for summaries.

        """
        definition = MetricDefinition(
            name=metric_name,
            description=description,
            metric_type=metric_type,
            labelnames=labelnames,
            buckets=buckets,
            quantiles=quantiles,
        )
        async with self._lock:
            self.definitions[metric_name] = definition

    async def get_text(self) -> str:
        """Generate Prometheus text format output.

        Returns:
            The Prometheus text format string.

        """
        async with self._lock:
            metrics_list = list(self.metrics.keys())
            defs = set(self.definitions.keys())
            out_text: list[str] = []
            emitted_defs: set[str] = set()

            # Group metrics by name to output HELP/TYPE before each group
            metrics_by_name: dict[str, list[MetricEntry]] = {}
            for metric_entry in metrics_list:
                if metric_entry.name not in metrics_by_name:
                    metrics_by_name[metric_entry.name] = []
                metrics_by_name[metric_entry.name].append(metric_entry)

            # Output metrics with their HELP/TYPE comments
            for metric_name, entries in metrics_by_name.items():
                if metric_name in defs and metric_name not in emitted_defs:
                    metric_def = self.definitions[metric_name]
                    out_text.extend(
                        [
                            f"# HELP {metric_name} {metric_def.description}",
                            f"# TYPE {metric_name} {metric_def.metric_type.value}",
                        ]
                    )
                    emitted_defs.add(metric_name)

                for metric_entry in entries:
                    value = self.metrics[metric_entry]
                    labels = metric_entry.labels
                    if labels:
                        labels = f"{{{labels}}}"
                    out_text.append(f"{metric_entry.name}{labels} {value}")

            return "\n".join(out_text)

    async def remove_labelset(self, metric_name: str, *label_values: str) -> None:
        """Remove a specific labelset from a metric.

        Args:
            metric_name: The name of the metric.
            *label_values: The label values in the order of labelnames.

        """
        definition = self.definitions.get(metric_name)
        if not definition:
            return

        label_str = ",".join(
            [
                f'{k}="{to_label(v)}"'
                for k, v in zip(definition.labelnames, label_values, strict=True)
            ]
        )
        entry = MetricEntry(metric_name, label_str)

        async with self._lock:
            self.metrics.pop(entry, None)

    async def remove_labelsets_by_labels(self, metric_name: str, **labels: str) -> None:
        """Remove all labelsets that partially match the given labels.

        Args:
            metric_name: The name of the metric.
            **labels: The label key-value pairs to match.

        """
        async with self._lock:
            entries_to_remove: list[MetricEntry] = []
            for entry in list(self.metrics.keys()):
                if entry.name != metric_name:
                    continue
                entry_labels = entry.labels
                if all(f'{k}="{to_label(v)}"' in entry_labels for k, v in labels.items()):
                    entries_to_remove.append(entry)
            for entry in entries_to_remove:
                self.metrics.pop(entry, None)

    async def clear_metric(self, metric_name: str) -> None:
        """Remove all labelsets from a metric.

        Args:
            metric_name: The name of the metric.

        """
        async with self._lock:
            entries_to_remove = [e for e in self.metrics if e.name == metric_name]
            for entry in entries_to_remove:
                self.metrics.pop(entry, None)


class _MetricChild:
    """Child metric object with specific label values."""

    def __init__(
        self,
        metrics: Metrics,
        metric_name: str,
        label_values: tuple[str, ...] | None = None,
        label_kwargs: dict[str, str] | None = None,
    ) -> None:
        """Initialize a metric child.

        Args:
            metrics: The Metrics instance.
            metric_name: The name of the metric.
            label_values: Positional label values.
            label_kwargs: Keyword label values.

        """
        self._metrics = metrics
        self._metric_name = metric_name
        self._label_values = label_values or ()
        self._label_kwargs = label_kwargs or {}

    def _get_labels(self, definition: MetricDefinition) -> dict[str, str]:
        """Get the combined labels from positional and keyword arguments.

        Args:
            definition: The metric definition.

        Returns:
            The combined label dictionary.

        """
        labels: dict[str, str] = {}
        if self._label_values:
            labels = dict(zip(
                    definition.labelnames, self._label_values, strict=True
                ))
        labels.update(self._label_kwargs)
        return labels


class Counter:
    """A Prometheus counter metric."""

    def __init__(
        self,
        metrics: Metrics,
        name: str,
        description: str,
        labelnames: tuple[str, ...] = (),
    ) -> None:
        """Initialize a counter.

        Args:
            metrics: The Metrics instance.
            name: The metric name.
            description: The metric description.
            labelnames: The label names.

        """
        self._metrics = metrics
        self._name = name
        self._labelnames = labelnames
        self._description = description

    async def _ensure_definition(self) -> None:
        """Ensure the metric definition exists."""
        if self._name not in self._metrics.definitions:
            await self._metrics.add_definition(
                self._name,
                self._description,
                MetricType.COUNTER,
                self._labelnames,
            )

    def labels(self, *values: str, **kwargs: str) -> "_CounterChild":
        """Get a child metric with specific label values.

        Args:
            *values: Positional label values.
            **kwargs: Keyword label values.

        Returns:
            A child counter metric.

        """
        return _CounterChild(self._metrics, self._name, self._labelnames, values, kwargs)

    async def inc(self, amount: float = 1, **labels: str) -> None:
        """Increment the counter.

        Args:
            amount: The amount to increment by.
            **labels: The label key-value pairs.

        """
        await self._ensure_definition()
        await self._metrics.add_value(self._name, amount, **labels)

    async def remove(self, *label_values: str) -> None:
        """Remove a specific labelset.

        Args:
            *label_values: The label values in the order of labelnames.

        """
        await self._metrics.remove_labelset(self._name, *label_values)

    async def remove_by_labels(self, labels: dict[str, str]) -> None:
        """Remove labelsets that match the given labels.

        Args:
            labels: The label key-value pairs to match.

        """
        await self._metrics.remove_labelsets_by_labels(self._name, **labels)

    async def clear(self) -> None:
        """Remove all labelsets from the counter."""
        await self._metrics.clear_metric(self._name)


class _CounterChild(_MetricChild):
    """Child counter metric with specific label values."""

    def __init__(
        self,
        metrics: Metrics,
        metric_name: str,
        labelnames: tuple[str, ...],
        label_values: tuple[str, ...],
        label_kwargs: dict[str, str],
    ) -> None:
        """Initialize a counter child.

        Args:
            metrics: The Metrics instance.
            metric_name: The metric name.
            labelnames: The label names.
            label_values: Positional label values.
            label_kwargs: Keyword label values.

        """
        super().__init__(metrics, metric_name, label_values, label_kwargs)
        self._labelnames = labelnames

    async def inc(self, amount: float = 1) -> None:
        """Increment the counter.

        Args:
            amount: The amount to increment by.

        """
        definition = self._metrics.definitions.get(self._metric_name)
        if definition:
            labels = self._get_labels(definition)
        else:
            labels = self._label_kwargs.copy()
            if self._label_values:
                for name, value in zip(self._labelnames, self._label_values, strict=True):
                    labels[name] = value
        await self._metrics.add_value(self._metric_name, amount, **labels)


class Gauge:
    """A Prometheus gauge metric."""

    def __init__(
        self,
        metrics: Metrics,
        name: str,
        description: str,
        labelnames: tuple[str, ...] = (),
    ) -> None:
        """Initialize a gauge.

        Args:
            metrics: The Metrics instance.
            name: The metric name.
            description: The metric description.
            labelnames: The label names.

        """
        self._metrics = metrics
        self._name = name
        self._labelnames = labelnames
        self._description = description

    async def _ensure_definition(self) -> None:
        """Ensure the metric definition exists."""
        if self._name not in self._metrics.definitions:
            await self._metrics.add_definition(
                self._name,
                self._description,
                MetricType.GAUGE,
                self._labelnames,
            )

    def labels(self, *values: str, **kwargs: str) -> "_GaugeChild":
        """Get a child metric with specific label values.

        Args:
            *values: Positional label values.
            **kwargs: Keyword label values.

        Returns:
            A child gauge metric.

        """
        return _GaugeChild(self._metrics, self._name, self._labelnames, values, kwargs)

    async def set(self, value: float, **labels: str) -> None:
        """Set the gauge to a value.

        Args:
            value: The value to set.
            **labels: The label key-value pairs.

        """
        await self._ensure_definition()
        await self._metrics.add_value(self._name, value, **labels)

    async def inc(self, amount: float = 1, **labels: str) -> None:
        """Increment the gauge.

        Args:
            amount: The amount to increment by.
            **labels: The label key-value pairs.

        """
        await self._ensure_definition()
        await self._metrics.add_value(self._name, amount, **labels)

    async def dec(self, amount: float = 1, **labels: str) -> None:
        """Decrement the gauge.

        Args:
            amount: The amount to decrement by.
            **labels: The label key-value pairs.

        """
        await self._ensure_definition()
        await self._metrics.add_value(self._name, -amount, **labels)

    async def remove(self, *label_values: str) -> None:
        """Remove a specific labelset.

        Args:
            *label_values: The label values in the order of labelnames.

        """
        await self._metrics.remove_labelset(self._name, *label_values)

    async def remove_by_labels(self, labels: dict[str, str]) -> None:
        """Remove labelsets that match the given labels.

        Args:
            labels: The label key-value pairs to match.

        """
        await self._metrics.remove_labelsets_by_labels(self._name, **labels)

    async def clear(self) -> None:
        """Remove all labelsets from the gauge."""
        await self._metrics.clear_metric(self._name)


class _GaugeChild(_MetricChild):
    """Child gauge metric with specific label values."""

    def __init__(
        self,
        metrics: Metrics,
        metric_name: str,
        labelnames: tuple[str, ...],
        label_values: tuple[str, ...],
        label_kwargs: dict[str, str],
    ) -> None:
        """Initialize a gauge child.

        Args:
            metrics: The Metrics instance.
            metric_name: The metric name.
            labelnames: The label names.
            label_values: Positional label values.
            label_kwargs: Keyword label values.

        """
        super().__init__(metrics, metric_name, label_values, label_kwargs)
        self._labelnames = labelnames

    async def set(self, value: float) -> None:
        """Set the gauge to a value.

        Args:
            value: The value to set.

        """
        definition = self._metrics.definitions.get(self._metric_name)
        if definition:
            labels = self._get_labels(definition)
        else:
            labels = self._label_kwargs.copy()
            if self._label_values:
                for name, val in zip(self._labelnames, self._label_values, strict=True):
                    labels[name] = val
        await self._metrics.add_value(self._metric_name, value, **labels)

    async def inc(self, amount: float = 1) -> None:
        """Increment the gauge.

        Args:
            amount: The amount to increment by.

        """
        definition = self._metrics.definitions.get(self._metric_name)
        if definition:
            labels = self._get_labels(definition)
        else:
            labels = self._label_kwargs.copy()
            if self._label_values:
                for name, value in zip(self._labelnames, self._label_values, strict=True):
                    labels[name] = value
        await self._metrics.add_value(self._metric_name, amount, **labels)

    async def dec(self, amount: float = 1) -> None:
        """Decrement the gauge.

        Args:
            amount: The amount to decrement by.

        """
        definition = self._metrics.definitions.get(self._metric_name)
        if definition:
            labels = self._get_labels(definition)
        else:
            labels = self._label_kwargs.copy()
            if self._label_values:
                for name, val in zip(self._labelnames, self._label_values, strict=True):
                    labels[name] = val
        await self._metrics.add_value(self._metric_name, -amount, **labels)


class Histogram:
    """A Prometheus histogram metric."""

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

    def __init__(
        self,
        metrics: Metrics,
        name: str,
        description: str,
        labelnames: tuple[str, ...] = (),
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        """Initialize a histogram.

        Args:
            metrics: The Metrics instance.
            name: The metric name.
            description: The metric description.
            labelnames: The label names.
            buckets: The bucket boundaries.

        """
        self._metrics = metrics
        self._name = name
        self._labelnames = labelnames
        self._buckets = buckets or self.DEFAULT_BUCKETS
        self._description = description

    async def _ensure_definition(self) -> None:
        """Ensure the metric definition exists."""
        if self._name not in self._metrics.definitions:
            await self._metrics.add_definition(
                self._name,
                self._description,
                MetricType.HISTOGRAM,
                self._labelnames,
                buckets=self._buckets,
            )
            # Add child metric definitions
            await self._metrics.add_definition(
                f"{self._name}_sum",
                f"{self._description} (sum)",
                MetricType.GAUGE,
                self._labelnames,
            )
            await self._metrics.add_definition(
                f"{self._name}_count",
                f"{self._description} (count)",
                MetricType.COUNTER,
                self._labelnames,
            )

    def labels(self, *values: str, **kwargs: str) -> "_HistogramChild":
        """Get a child metric with specific label values.

        Args:
            *values: Positional label values.
            **kwargs: Keyword label values.

        Returns:
            A child histogram metric.

        """
        return _HistogramChild(
            self._metrics, self._name, self._labelnames, values, kwargs
        )

    async def observe(self, amount: float, **labels: str) -> None:
        """Observe a value.

        Args:
            amount: The value to observe.
            **labels: The label key-value pairs.

        """
        await self._ensure_definition()
        # For simplicity, we just store the observed value
        # A full implementation would track bucket counts
        await self._metrics.add_value(f"{self._name}_sum", amount, **labels)
        await self._metrics.add_value(f"{self._name}_count", 1, **labels)

    async def remove(self, *label_values: str) -> None:
        """Remove a specific labelset.

        Args:
            *label_values: The label values in the order of labelnames.

        """
        await self._metrics.remove_labelset(self._name, *label_values)
        await self._metrics.remove_labelset(f"{self._name}_sum", *label_values)
        await self._metrics.remove_labelset(f"{self._name}_count", *label_values)

    async def remove_by_labels(self, labels: dict[str, str]) -> None:
        """Remove labelsets that match the given labels.

        Args:
            labels: The label key-value pairs to match.

        """
        await self._metrics.remove_labelsets_by_labels(self._name, **labels)
        await self._metrics.remove_labelsets_by_labels(f"{self._name}_sum", **labels)
        await self._metrics.remove_labelsets_by_labels(f"{self._name}_count", **labels)

    async def clear(self) -> None:
        """Remove all labelsets from the histogram."""
        await self._metrics.clear_metric(self._name)
        await self._metrics.clear_metric(f"{self._name}_sum")
        await self._metrics.clear_metric(f"{self._name}_count")


class _HistogramChild(_MetricChild):
    """Child histogram metric with specific label values."""

    def __init__(
        self,
        metrics: Metrics,
        metric_name: str,
        labelnames: tuple[str, ...],
        label_values: tuple[str, ...],
        label_kwargs: dict[str, str],
    ) -> None:
        """Initialize a histogram child.

        Args:
            metrics: The Metrics instance.
            metric_name: The metric name.
            labelnames: The label names.
            label_values: Positional label values.
            label_kwargs: Keyword label values.

        """
        super().__init__(metrics, metric_name, label_values, label_kwargs)
        self._labelnames = labelnames

    async def observe(self, amount: float) -> None:
        """Observe a value.

        Args:
            amount: The value to observe.

        """
        definition = self._metrics.definitions.get(self._metric_name)
        if definition:
            labels = self._get_labels(definition)
        else:
            labels = self._label_kwargs.copy()
            if self._label_values:
                for name, value in zip(self._labelnames, self._label_values, strict=True):
                    labels[name] = value
        await self._metrics.add_value(f"{self._metric_name}_sum", amount, **labels)
        await self._metrics.add_value(f"{self._metric_name}_count", 1, **labels)


class Summary:
    """A Prometheus summary metric."""

    DEFAULT_QUANTILES = (0.5, 0.9, 0.95, 0.99)

    def __init__(
        self,
        metrics: Metrics,
        name: str,
        description: str,
        labelnames: tuple[str, ...] = (),
        quantiles: tuple[float, ...] | None = None,
    ) -> None:
        """Initialize a summary.

        Args:
            metrics: The Metrics instance.
            name: The metric name.
            description: The metric description.
            labelnames: The label names.
            quantiles: The quantile values.

        """
        self._metrics = metrics
        self._name = name
        self._labelnames = labelnames
        self._quantiles = quantiles or self.DEFAULT_QUANTILES
        self._description = description

    async def _ensure_definition(self) -> None:
        """Ensure the metric definition exists."""
        if self._name not in self._metrics.definitions:
            await self._metrics.add_definition(
                self._name,
                self._description,
                MetricType.SUMMARY,
                self._labelnames,
                quantiles=self._quantiles,
            )
            # Add child metric definitions
            await self._metrics.add_definition(
                f"{self._name}_sum",
                f"{self._description} (sum)",
                MetricType.GAUGE,
                self._labelnames,
            )
            await self._metrics.add_definition(
                f"{self._name}_count",
                f"{self._description} (count)",
                MetricType.COUNTER,
                self._labelnames,
            )

    def labels(self, *values: str, **kwargs: str) -> "_SummaryChild":
        """Get a child metric with specific label values.

        Args:
            *values: Positional label values.
            **kwargs: Keyword label values.

        Returns:
            A child summary metric.

        """
        return _SummaryChild(self._metrics, self._name, self._labelnames, values, kwargs)

    async def observe(self, amount: float, **labels: str) -> None:
        """Observe a value.

        Args:
            amount: The value to observe.
            **labels: The label key-value pairs.

        """
        await self._ensure_definition()
        # For simplicity, we just store the observed value
        # A full implementation would track quantiles
        await self._metrics.add_value(f"{self._name}_sum", amount, **labels)
        await self._metrics.add_value(f"{self._name}_count", 1, **labels)

    async def remove(self, *label_values: str) -> None:
        """Remove a specific labelset.

        Args:
            *label_values: The label values in the order of labelnames.

        """
        await self._metrics.remove_labelset(self._name, *label_values)
        await self._metrics.remove_labelset(f"{self._name}_sum", *label_values)
        await self._metrics.remove_labelset(f"{self._name}_count", *label_values)

    async def remove_by_labels(self, labels: dict[str, str]) -> None:
        """Remove labelsets that match the given labels.

        Args:
            labels: The label key-value pairs to match.

        """
        await self._metrics.remove_labelsets_by_labels(self._name, **labels)
        await self._metrics.remove_labelsets_by_labels(f"{self._name}_sum", **labels)
        await self._metrics.remove_labelsets_by_labels(f"{self._name}_count", **labels)

    async def clear(self) -> None:
        """Remove all labelsets from the summary."""
        await self._metrics.clear_metric(self._name)
        await self._metrics.clear_metric(f"{self._name}_sum")
        await self._metrics.clear_metric(f"{self._name}_count")


class _SummaryChild(_MetricChild):
    """Child summary metric with specific label values."""

    def __init__(
        self,
        metrics: Metrics,
        metric_name: str,
        labelnames: tuple[str, ...],
        label_values: tuple[str, ...],
        label_kwargs: dict[str, str],
    ) -> None:
        """Initialize a summary child.

        Args:
            metrics: The Metrics instance.
            metric_name: The metric name.
            labelnames: The label names.
            label_values: Positional label values.
            label_kwargs: Keyword label values.

        """
        super().__init__(metrics, metric_name, label_values, label_kwargs)
        self._labelnames = labelnames

    async def observe(self, amount: float) -> None:
        """Observe a value.

        Args:
            amount: The value to observe.

        """
        definition = self._metrics.definitions.get(self._metric_name)
        if definition:
            labels = self._get_labels(definition)
        else:
            labels = self._label_kwargs.copy()
            if self._label_values:
                for name, value in zip(self._labelnames, self._label_values, strict=True):
                    labels[name] = value
        await self._metrics.add_value(f"{self._metric_name}_sum", amount, **labels)
        await self._metrics.add_value(f"{self._metric_name}_count", 1, **labels)


class Info:
    """A Prometheus info metric."""

    def __init__(
        self,
        metrics: Metrics,
        name: str,
        description: str,
        labelnames: tuple[str, ...] = (),
    ) -> None:
        """Initialize an info metric.

        Args:
            metrics: The Metrics instance.
            name: The metric name.
            description: The metric description.
            labelnames: The label names.

        """
        self._metrics = metrics
        self._name = name
        self._labelnames = labelnames
        self._description = description

    async def _ensure_definition(self) -> None:
        """Ensure the metric definition exists."""
        if self._name not in self._metrics.definitions:
            await self._metrics.add_definition(
                self._name,
                self._description,
                MetricType.INFO,
                self._labelnames,
            )

    def labels(self, *values: str, **kwargs: str) -> "_InfoChild":
        """Get a child metric with specific label values.

        Args:
            *values: Positional label values.
            **kwargs: Keyword label values.

        Returns:
            A child info metric.

        """
        return _InfoChild(self._metrics, self._name, self._labelnames, values, kwargs)

    async def info(self, **labels: str) -> None:
        """Set the info metric with labels.

        Args:
            **labels: The label key-value pairs.

        """
        await self._ensure_definition()
        await self._metrics.add_value(self._name, 1, **labels)

    async def remove(self, *label_values: str) -> None:
        """Remove a specific labelset.

        Args:
            *label_values: The label values in the order of labelnames.

        """
        await self._metrics.remove_labelset(self._name, *label_values)

    async def remove_by_labels(self, labels: dict[str, str]) -> None:
        """Remove labelsets that match the given labels.

        Args:
            labels: The label key-value pairs to match.

        """
        await self._metrics.remove_labelsets_by_labels(self._name, **labels)

    async def clear(self) -> None:
        """Remove all labelsets from the info metric."""
        await self._metrics.clear_metric(self._name)


class _InfoChild(_MetricChild):
    """Child info metric with specific label values."""

    def __init__(
        self,
        metrics: Metrics,
        metric_name: str,
        labelnames: tuple[str, ...],
        label_values: tuple[str, ...],
        label_kwargs: dict[str, str],
    ) -> None:
        """Initialize an info child.

        Args:
            metrics: The Metrics instance.
            metric_name: The metric name.
            labelnames: The label names.
            label_values: Positional label values.
            label_kwargs: Keyword label values.

        """
        super().__init__(metrics, metric_name, label_values, label_kwargs)
        self._labelnames = labelnames

    async def info(self) -> None:
        """Set the info metric with labels."""
        definition = self._metrics.definitions.get(self._metric_name)
        if definition:
            labels = self._get_labels(definition)
        else:
            labels = self._label_kwargs.copy()
            if self._label_values:
                for name, val in zip(self._labelnames, self._label_values, strict=True):
                    labels[name] = val
        await self._metrics.add_value(self._metric_name, 1, **labels)
