from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from bot.core.components import BaseService

if TYPE_CHECKING:
    from collections.abc import Callable

    from bot.core.components import BaseEvent

import logging

LOGGER = logging.getLogger(__name__)


@dataclass
class EventWaiterRequest[T: BaseEvent]:
    """Represents a request to wait for a specific event."""

    predicate: Callable[[BaseEvent], bool]
    future: asyncio.Future[T]
    timeout: float | None = None
    event_type: type[T] | None = None


class EventWaiterService(BaseService):
    """Service that allows awaiting for matching events.

    This service provides a way to wait for events that match specific criteria,
    similar to discord.py's `wait_for` functionality. It integrates with the
    existing event system to intercept and match events against registered waiters.
    """

    def __init__(self, max_history_size: int = 500) -> None:
        super().__init__()
        self._waiters: list[EventWaiterRequest[BaseEvent]] = []
        self._lock: asyncio.Lock = asyncio.Lock()
        self._event_history: list[tuple[float, BaseEvent]] = []
        self._max_history_size: int = max_history_size
        self._history_lock: asyncio.Lock = asyncio.Lock()

    async def wait_for[T: BaseEvent](
        self,
        event_type: type[T] | None = None,
        *,
        predicate: Callable[[BaseEvent], bool] | None = None,
        timeout: float | None = None,
        seconds_before: float | None = None,
    ) -> T:
        """Wait for an event matching the specified criteria.

        Args:
            event_type: The type of event to wait for. If None, matches any event type.
            predicate: A callable that takes an event and returns True if it matches.
            timeout: Maximum time in seconds to wait. Raises TimeoutError if exceeded.
            seconds_before: If specified, check past events within this time window
                before waiting for new events. Returns immediately if a matching
                past event is found.

        Returns:
            The first event that matches the criteria.

        Raises:
            TimeoutError: If the timeout is exceeded.
            asyncio.CancelledError: If the wait is cancelled.
        """
        # Check past events first if seconds_before is specified
        if seconds_before is not None:
            past_event = await self._check_past_events(
                seconds_before, event_type, predicate
            )
            if past_event is not None:
                return past_event

        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def effective_predicate(event: BaseEvent) -> bool:
            if event_type is not None and not isinstance(event, event_type):
                return False
            if predicate is not None:
                try:
                    return predicate(event)
                except Exception:
                    LOGGER.exception("Error in event waiter predicate: %s", predicate)
                    return False
            return True

        waiter = EventWaiterRequest(
            predicate=effective_predicate,
            future=future,
            timeout=timeout,
            event_type=event_type,
        )

        async with self._lock:
            self._waiters.append(waiter)

        try:
            if timeout is not None:
                return await asyncio.wait_for(future, timeout=timeout)  # pyright: ignore[reportAny]
            return await future  # pyright: ignore[reportAny]
        except TimeoutError as e:
            msg = (
                f"Timed out waiting for event "
                f"{event_type.__name__ if event_type else 'matching criteria'}"
            )
            raise TimeoutError(msg) from e
        finally:
            async with self._lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
            if not future.done():
                __ = future.cancel()

    async def process_event(self, event: BaseEvent) -> BaseEvent:
        """Process an event and dispatch to matching waiters.

        This method should be called by the event processing pipeline
        to check if any waiters are waiting for this event.

        Args:
            event: The event to process.

        Returns:
            The unmodified event (for use in middleware chains).
        """
        # Store event in history buffer
        async with self._history_lock:
            self._event_history.append((event.timestamp, event))
            # Cleanup old events if buffer exceeds max size
            if len(self._event_history) > self._max_history_size:
                self._event_history = self._event_history[-self._max_history_size :]

        async with self._lock:
            matched_waiters: list[EventWaiterRequest[BaseEvent]] = []
            for waiter in self._waiters:
                try:
                    if waiter.predicate(event):
                        matched_waiters.append(waiter)
                except Exception:
                    LOGGER.exception(
                        "Error evaluating predicate for event waiter: %s", waiter
                    )

        for waiter in matched_waiters:
            if not waiter.future.done():
                waiter.future.set_result(event)

        return event

    def get_active_waiters_count(self) -> int:
        """Return the number of active event waiters."""
        return len(self._waiters)

    async def _check_past_events[T: BaseEvent](
        self,
        seconds_before: float,
        event_type: type[T] | None = None,
        predicate: Callable[[BaseEvent], bool] | None = None,
    ) -> T | None:
        """Check history buffer for events within the specified time window.

        Args:
            seconds_before: Number of seconds before current time to search.
            event_type: The type of event to search for. If None, matches any event type.
            predicate: A callable that takes an event and returns True if it matches.

        Returns:
            The most recent matching event, or None if no match found.
        """
        cutoff_time = time.time() - seconds_before

        def effective_predicate(event: BaseEvent) -> bool:
            if event_type is not None and not isinstance(event, event_type):
                return False
            if predicate is not None:
                try:
                    return predicate(event)
                except Exception:
                    LOGGER.exception("Error in event waiter predicate: %s", predicate)
                    return False
            return True

        async with self._history_lock:
            # Search from newest to oldest
            for event_timestamp, event in reversed(self._event_history):
                if event_timestamp < cutoff_time:
                    break
                try:
                    if effective_predicate(event):
                        return event  # pyright: ignore[reportReturnType]
                except Exception:
                    LOGGER.exception("Error checking past event: %s", event)

        return None

    async def get_past_events[T: BaseEvent](
        self,
        seconds_before: float,
        event_type: type[T] | None = None,
        predicate: Callable[[BaseEvent], bool] | None = None,
    ) -> list[T]:
        """Get all matching events within the specified time window.

        Args:
            seconds_before: Number of seconds before current time to search.
            event_type: The type of event to search for. If None, matches any event type.
            predicate: A callable that takes an event and returns True if it matches.

        Returns:
            A list of matching events ordered from newest to oldest.
        """
        cutoff_time = time.time() - seconds_before

        def effective_predicate(event: BaseEvent) -> bool:
            if event_type is not None and not isinstance(event, event_type):
                return False
            if predicate is not None:
                try:
                    return predicate(event)
                except Exception:
                    LOGGER.exception("Error in event waiter predicate: %s", predicate)
                    return False
            return True

        matching_events: list[T] = []
        async with self._history_lock:
            # Search from newest to oldest
            for event_timestamp, event in reversed(self._event_history):
                if event_timestamp < cutoff_time:
                    break
                try:
                    if effective_predicate(event):
                        matching_events.append(event)  # pyright: ignore[reportArgumentType]
                except Exception:
                    LOGGER.exception("Error checking past event: %s", event)

        return matching_events

    @override
    async def teardown(self) -> None:
        """Clean up all active waiters when service is being torn down."""
        async with self._lock:
            for waiter in self._waiters:
                if not waiter.future.done():
                    __ = waiter.future.cancel()
            self._waiters.clear()
        async with self._history_lock:
            self._event_history.clear()
