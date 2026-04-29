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
        if not self._is_in_alerting_condition:
            self._is_in_alerting_condition = True
            self._alert_start_time = time.monotonic()
            if self._alerting_callback is not None:
                self._alerting_callback_task = asyncio.create_task(
                    self._trigger_alerting_callback()
                )
        self._alert_reason = reason

    def set_normal(self):
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
        if not self._is_in_alerting_condition:
            return False
        return bool(
            self._is_in_alerting_condition
            and self._alert_start_time
            and time.monotonic() - self._alert_start_time > self._fail_duration
        )

    def get_alert_reason(self):
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
        self._registered_alert_callback = callback

    def set_recovery_callback(self, callback: Callable[[], Awaitable[None]] | None):
        self._registered_recovery_callback = callback

    async def _loop(self):
        while True:
            try:
                t = time.monotonic()
                await asyncio.sleep(max(0, self.interval - (t - self._last_execution)))
                _ = await self.run_process_now()
            except Exception:
                logger.exception(f"Error in collector loop {self.__class__.__name__}")

    async def run_process_now(self):
        try:
            t = time.monotonic()
            await self.process()
            self._last_execution = t
        except Exception:
            logger.exception(f"Error in collector {self.__class__.__name__}")

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
        return self._alert.get_is_alerting()

    def get_alert_reason(self) -> str | None:
        return self._alert.get_alert_reason()

    def set_status(self, *, failing: bool, reason: str = ""):
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
    def __init__(
        self,
        instances: Collection[T],
        *,
        loop_interval: float = 1,
        fail_duration: float = 60,
        urgent_failure: bool = False,
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
        self.is_urgent_failure: bool = urgent_failure

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
        if instance not in self.instances:
            msg = f"This GroupCollector does not have this instance {instance} registered"
            raise ValueError(msg)
        self._registered_alert_callbacks[instance] = callback

    def set_recovery_callback(
        self, instance: T, callback: Callable[[], Awaitable[None]] | None
    ):
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

    async def run_process_now(self):
        try:
            t = time.monotonic()
            await self.process()
            self._last_execution = t
        except Exception:
            logger.exception(f"Error in collector {self.__class__.__name__}")

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
        if instance not in self.instances:
            msg = f"This GroupCollector does not have this instance {instance} registered"
            raise ValueError(msg)
        return self._alerts[instance].get_is_alerting()

    def get_alert_reason(self, instance: T) -> str | None:
        if instance not in self.instances:
            msg = f"This GroupCollector does not have this instance {instance} registered"
            raise ValueError(msg)
        return self._alerts[instance].get_alert_reason()

    def set_status(self, instance: T, *, failing: bool, reason: str):
        if instance not in self.instances:
            msg = f"This GroupCollector does not have this instance {instance} registered"
            raise ValueError(msg)
        if failing:
            self._alerts[instance].set_failing(reason)
        else:
            self._alerts[instance].set_normal()

    async def on_alert(self, instance: T):
        """Fires when an alert goes off."""

    async def on_recovery(self, instance: T):
        """Fires when an alert goes off."""

    async def process(self) -> None:
        """Code that fetches data.

        Use the set_data and get_data functions to store and retrieve data.
        """
        raise NotImplementedError
