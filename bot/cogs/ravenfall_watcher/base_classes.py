from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection

logger = logging.getLogger(__name__)


class Alert:
    """Alert class that tracks failing conditions and triggers optional callbacks."""

    def __init__(
        self,
        fail_duration_seconds: float = 60,
        failure_callback: Callable[[], Awaitable[None]] | None = None,
        recovery_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._is_in_alerting_condition: bool = False
        self._alert_reason: str | None = None
        self._alert_start_time: float | None = None
        self._fail_duration: float = fail_duration_seconds
        self._alerting_callback: Callable[[], Awaitable[None]] | None = failure_callback
        self._alerting_callback_task: asyncio.Task[None] | None = None
        self._recovery_callback: Callable[[], Awaitable[None]] | None = recovery_callback
        self._recovery_callback_task: asyncio.Task[None] | None = None
        self._has_sent_alert: bool = False

    def set_alerting_callback(self, callback: Callable[[], Awaitable[None]]):
        """Overwrites the current alert callback."""
        self._alerting_callback = callback

    def set_recovery_callback(self, callback: Callable[[], Awaitable[None]]):
        """Overwrites the current recovery callback."""
        self._recovery_callback = callback

    def set_failing(self, reason: str | None = None):
        """Mark the alert as in a failing condition.

        Args:
            reason: Optional reason for the failure.
        """
        if not self._is_in_alerting_condition:
            self._is_in_alerting_condition = True
            self._alert_start_time = time.monotonic()
            if self._alerting_callback is not None:
                self._alerting_callback_task = asyncio.create_task(
                    self._trigger_alerting_callback()
                )
        self._alert_reason = reason

    def set_normal(self):
        """Clear the failing condition and return to normal."""
        if self._is_in_alerting_condition:
            self._is_in_alerting_condition = False
            self._alert_start_time = None
            self._alert_reason = None
            if self._alerting_callback_task is not None:
                __ = self._alerting_callback_task.cancel()
                self._alerting_callback_task = None
            if self._has_sent_alert and self._recovery_callback:
                self._recovery_callback_task = asyncio.create_task(
                    self._trigger_recovery_callback()
                )

    def get_is_alerting(self):
        """Check if the alert is currently alerting.

        Returns:
            True if the alert condition has persisted longer than the fail duration.
        """
        if not self._is_in_alerting_condition:
            return False
        return bool(
            self._is_in_alerting_condition
            and self._alert_start_time
            and time.monotonic() - self._alert_start_time > self._fail_duration
        )

    def get_alert_reason(self):
        """Get the reason for the current alert condition.

        Returns:
            The alert reason string, or None if not alerting.
        """
        return self._alert_reason

    async def _trigger_recovery_callback(self) -> None:
        try:
            self._has_sent_alert = False
            if self._recovery_callback:
                await self._recovery_callback()
        except Exception:
            logger.exception("Error in Alert recovery callback")

    async def _trigger_alerting_callback(self) -> None:
        """Wait for fail duration, then trigger callback if still failing."""
        try:
            await asyncio.sleep(self._fail_duration)
            if self._is_in_alerting_condition:
                self._has_sent_alert = True
                if self._alerting_callback is not None:
                    await self._alerting_callback()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error in Alert failure callback")


class BaseCollector[T]:
    """Base class for collecting data from a single instance with alert capabilities.

    This class manages a periodic processing loop that can be started and stopped,
    with built-in alerting when the process fails for a specified duration.
    """

    def __init__(
        self,
        instance: T,
        *,
        loop_interval: float = 1,
        fail_duration: float = 60,
        is_urgent_failure: bool = False,
    ) -> None:
        self.instance: T = instance
        self.interval: float = loop_interval
        self._alert: Alert = Alert(fail_duration, None)
        self._loop_task: asyncio.Task[None] | None = None
        self._last_execution: float = time.monotonic() - self.interval
        self._registered_alert_callback: Callable[[], Awaitable[None]] | None = None
        self._registered_recovery_callback: Callable[[], Awaitable[None]] | None = None
        self._alert.set_alerting_callback(self._alert_callback)
        self._alert.set_recovery_callback(self._recovery_callback)
        self._exception_count: int = 0
        self.is_urgent_failure: bool = is_urgent_failure

    def start(self):
        """Start the processing loop."""
        self._loop_task = asyncio.create_task(self._loop())

    def get_is_started(self):
        """Check if the collector is running."""
        return self._loop_task is not None

    def stop(self):
        """Stop the loop."""
        if self._loop_task is not None:
            __ = self._loop_task.cancel()
            self._loop_task = None

    def reset(self):
        """Stops the loop and stops alerting."""
        self.stop()
        self.set_status(failing=False)

    def set_alert_callback(self, callback: Callable[[], Awaitable[None]] | None):
        """Set the callback to be invoked when an alert is triggered.

        Args:
            callback: Async callable or None.
        """
        self._registered_alert_callback = callback

    def set_recovery_callback(self, callback: Callable[[], Awaitable[None]] | None):
        """Set the callback to be invoked when the alert recovers.

        Args:
            callback: Async callable or None.
        """
        self._registered_recovery_callback = callback

    async def _loop(self):
        while True:
            try:
                t = time.monotonic()
                await asyncio.sleep(max(0, self.interval - (t - self._last_execution)))
                _ = await self.run_process_now()
            except Exception:
                logger.exception(f"Error in collector loop {self.__class__.__name__}")
                self._exception_count += 1
            _max_exceptions = 3
            if self._exception_count >= _max_exceptions:
                self._exception_count = 0
                await asyncio.sleep(10)

    async def run_process_now(self):
        """Execute the process method and update last execution time."""
        try:
            t = time.monotonic()
            await self.process()
            self._last_execution = t
        except Exception:
            logger.exception(f"Error in collector {self.__class__.__name__}")
            self._exception_count += 1

    async def _alert_callback(self):
        try:
            await self.on_alert()
        except Exception:
            logger.exception("Exception in alert handler")
        if self._registered_alert_callback:
            await self._registered_alert_callback()

    async def _recovery_callback(self):
        try:
            await self.on_recovery()
        except Exception:
            logger.exception("Exception in recovery handler")
        if self._registered_recovery_callback:
            await self._registered_recovery_callback()

    def get_is_alerting(self) -> bool:
        """Check if the collector is currently alerting.

        Returns:
            True if the alert condition has persisted longer than the fail duration.
        """
        return self._alert.get_is_alerting()

    def get_alert_reason(self) -> str | None:
        """Get the reason for the current alert condition.

        Returns:
            The alert reason string, or None if not alerting.
        """
        return self._alert.get_alert_reason()

    def clear_alert(self):
        """Clear the alert condition and return to normal."""
        self._alert.set_normal()

    def set_status(self, *, failing: bool, reason: str = ""):
        """Set the alert status for the collector.

        Args:
            failing: Whether the collector is failing.
            reason: The reason for the status change.
        """
        if failing:
            self._alert.set_failing(reason)
        else:
            self._alert.set_normal()

    async def on_alert(self):
        """Fires when an alert goes off."""

    async def on_recovery(self):
        """Fires when an alert goes off."""

    async def process(self) -> None:
        """Code that fetches data.

        Use the set_data and get_data functions to store and retrieve data.
        """
        raise NotImplementedError


class BaseGroupCollector[T]:
    """Base class for collecting data from multiple instances
    with per-instance alert capabilities.

    This class manages a periodic processing loop for a collection of instances,
    with independent alert tracking and callbacks for each instance.
    """

    def __init__(
        self,
        instances: Collection[T],
        *,
        loop_interval: float = 1,
        fail_duration: float = 60,
        is_urgent_failure: bool = False,
    ) -> None:
        self.instances: list[T] = list(instances)
        self.interval: float = loop_interval
        self._alerts: dict[T, Alert] = {
            x: Alert(
                failure_callback=functools.partial(self._alert_callback, x),
                recovery_callback=functools.partial(self._recovery_callback, x),
            )
            for x in self.instances
        }
        self._loop_task: asyncio.Task[None] | None = None
        self._last_execution: float = time.monotonic() - self.interval
        self._registered_alert_callbacks: dict[
            T, Callable[[], Awaitable[None]] | None
        ] = dict.fromkeys(self.instances)
        self._registered_recovery_callbacks: dict[
            T, Callable[[], Awaitable[None]] | None
        ] = dict.fromkeys(self.instances)
        self.is_urgent_failure: bool = is_urgent_failure
        self._exception_count: int = 0

    def start(self):
        """Start the processing loop."""
        self._loop_task = asyncio.create_task(self._loop())

    def get_is_started(self):
        """Check if the collector is running."""
        return self._loop_task is not None

    def stop(self):
        """Stop the loop."""
        if self._loop_task is not None:
            __ = self._loop_task.cancel()
            self._loop_task = None

    def set_alert_callback(
        self, instance: T, callback: Callable[[], Awaitable[None]] | None
    ):
        """Set the alert callback for a specific instance.

        Args:
            instance: The instance to set the callback for.
            callback: Async callable or None.

        Raises:
            ValueError: If the instance is not registered with this collector.
        """
        if instance not in self.instances:
            msg = f"This GroupCollector does not have this instance {instance} registered"
            raise ValueError(msg)
        self._registered_alert_callbacks[instance] = callback

    def set_recovery_callback(
        self, instance: T, callback: Callable[[], Awaitable[None]] | None
    ):
        """Set the recovery callback for a specific instance.

        Args:
            instance: The instance to set the callback for.
            callback: Async callable or None.

        Raises:
            ValueError: If the instance is not registered with this collector.
        """
        if instance not in self.instances:
            msg = f"This GroupCollector does not have this instance {instance} registered"
            raise ValueError(msg)
        self._registered_recovery_callbacks[instance] = callback

    async def _loop(self):
        while True:
            try:
                t = time.monotonic()
                await asyncio.sleep(max(0, self.interval - (t - self._last_execution)))
                _ = await self.run_process_now()
            except Exception:
                logger.exception(f"Error in collector loop {self.__class__.__name__}")
                self._exception_count += 1
            _max_exceptions = 3
            if self._exception_count >= _max_exceptions:
                self._exception_count = 0
                await asyncio.sleep(10)

    async def run_process_now(self):
        """Execute the process method and update last execution time."""
        try:
            t = time.monotonic()
            await self.process()
            self._last_execution = t
        except Exception:
            logger.exception(f"Error in collector {self.__class__.__name__}")
            self._exception_count += 1

    async def _alert_callback(self, instance: T):
        if instance not in self.instances:
            msg = f"This GroupCollector does not have this instance {instance} registered"
            raise ValueError(msg)
        try:
            await self.on_alert(instance)
        except Exception:
            logger.exception("Exception in alert handler")
        callback = self._registered_alert_callbacks[instance]
        if callback is not None:
            await callback()

    async def _recovery_callback(self, instance: T):
        if instance not in self.instances:
            msg = f"This GroupCollector does not have this instance {instance} registered"
            raise ValueError(msg)
        try:
            await self.on_recovery(instance)
        except Exception:
            logger.exception("Exception in recovery handler")
        callback = self._registered_recovery_callbacks[instance]
        if callback is not None:
            await callback()

    def get_status(self, instance: T) -> bool:
        """Get the alert status for a specific instance.

        Args:
            instance: The instance to check.

        Returns:
            True if the instance is currently alerting.

        Raises:
            ValueError: If the instance is not registered with this collector.
        """
        if instance not in self.instances:
            msg = f"This GroupCollector does not have this instance {instance} registered"
            raise ValueError(msg)
        return self._alerts[instance].get_is_alerting()

    def get_alert_reason(self, instance: T) -> str | None:
        """Get the alert reason for a specific instance.

        Args:
            instance: The instance to check.

        Returns:
            The alert reason string, or None if not alerting.

        Raises:
            ValueError: If the instance is not registered with this collector.
        """
        if instance not in self.instances:
            msg = f"This GroupCollector does not have this instance {instance} registered"
            raise ValueError(msg)
        return self._alerts[instance].get_alert_reason()

    def clear_alert(self, instance: T):
        """Clear the alert condition for a specific instance and return to normal.

        Args:
            instance: The instance to clear the alert for.
        """
        if instance not in self.instances:
            msg = f"This GroupCollector does not have this instance {instance} registered"
            raise ValueError(msg)
        self._alerts[instance].set_normal()

    def set_status(self, instance: T, *, failing: bool, reason: str = ""):
        """Set the alert status for a specific instance.

        Args:
            instance: The instance to update.
            failing: Whether the instance is failing.
            reason: The reason for the status change.

        Raises:
            ValueError: If the instance is not registered with this collector.
        """
        if instance not in self.instances:
            msg = f"This GroupCollector does not have this instance {instance} registered"
            raise ValueError(msg)
        if failing:
            self._alerts[instance].set_failing(reason)
        else:
            self._alerts[instance].set_normal()

    async def on_alert(self, instance: T):  # pyright: ignore[reportUnusedParameter]
        """Fires when an alert goes off."""

    async def on_recovery(self, instance: T):  # pyright: ignore[reportUnusedParameter]
        """Fires when an alert goes off."""

    async def process(self) -> None:
        """Code that fetches data.

        Use the set_data and get_data functions to store and retrieve data.
        """
        raise NotImplementedError
