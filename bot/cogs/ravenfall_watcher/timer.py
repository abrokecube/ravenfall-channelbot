"""Timer module for countdown timer with async callbacks."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


LOGGER = logging.getLogger(__name__)


class _TimeTracker:
    """Helper class to track elapsed time with pause/resume support."""

    def __init__(self) -> None:
        self.is_running: bool = False
        self.is_paused: bool = False
        self._start_time: float = 0.0
        self._elapsed_paused: float = 0.0

    def start(self) -> None:
        """Start the time tracker."""
        self.is_running = True
        self.is_paused = False
        self._start_time = monotonic()
        self._elapsed_paused = 0.0

    def stop(self) -> None:
        """Stop the time tracker."""
        self.is_running = False
        self.is_paused = False
        self._start_time = 0.0
        self._elapsed_paused = 0.0

    def pause(self) -> None:
        """Pause the time tracker."""
        if not self.is_running or self.is_paused:
            return
        self.is_paused = True
        self._elapsed_paused = self.get_elapsed()

    def resume(self) -> None:
        """Resume the time tracker."""
        if not self.is_running or not self.is_paused:
            return
        self.is_paused = False
        self._start_time = monotonic()

    def get_elapsed(self) -> float:
        """Get total elapsed time."""
        if not self.is_running:
            return 0.0
        if self.is_paused:
            return self._elapsed_paused
        current_time = monotonic()
        return current_time - self._start_time + self._elapsed_paused

    def set_elapsed(self, elapsed: float) -> None:
        """Manually override the current elapsed time."""
        self._elapsed_paused = elapsed
        if not self.is_paused:
            self._start_time = monotonic()


@dataclass
class _CallbackInfo:
    """Internal dataclass for storing callback information."""

    callback: Callable[[], Awaitable[None]]
    from_end: bool
    time_point: float


class Timer:
    """An asyncio-safe countdown timer with callback support.

    The timer efficiently uses asyncio.timeout to minimize resource usage,
    only waking when callbacks need to fire or the timer ends, and correctly
    interrupts when callbacks are added or state changes.
    """

    def __init__(self) -> None:
        """Initialize the timer with a duration in seconds."""
        self._duration: float = 60.0
        self._tracker: _TimeTracker = _TimeTracker()
        self._wakeup_event: asyncio.Event = asyncio.Event()
        self._absolute_callbacks: dict[float, list[_CallbackInfo]] = defaultdict(list)
        self._relative_callbacks: dict[float, list[_CallbackInfo]] = defaultdict(list)
        self._task: asyncio.Task[None] | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    async def start(self, duration: float) -> None:
        """Start the countdown timer.

        Raises:
            RuntimeError: If the timer is already running.
        """
        async with self._lock:
            if self.get_is_running():
                msg = "Timer is already running"
                raise RuntimeError(msg)

            self._duration = duration
            self._tracker.start()

            # Recalculate absolute times for relative callbacks
            self._absolute_callbacks.clear()
            for time_point, callback_infos in self._relative_callbacks.items():
                absolute_time = self._duration - time_point
                if absolute_time >= 0:
                    self._absolute_callbacks[absolute_time].extend(callback_infos)

        LOGGER.debug(f"Timer started with duration {self._duration}")
        if self._task:
            __ = self._task.cancel()
        self._wakeup_event.clear()
        self._task = asyncio.create_task(self._timer_loop())

    async def stop(self) -> None:
        """Stop the timer and reset to initial state."""
        async with self._lock:
            if self._task:
                __ = self._task.cancel()
                self._task = None

            self._tracker.stop()

        LOGGER.debug("Timer stopped")

    async def pause(self) -> None:
        """Pause the timer, preserving current elapsed time."""
        async with self._lock:
            if not self._tracker.is_running or self._tracker.is_paused:
                return

            self._tracker.pause()
            self._wakeup_event.set()

        LOGGER.debug(f"Timer paused at elapsed time {self._tracker._elapsed_paused}")

    async def resume(self) -> None:
        """Resume the timer from paused state."""
        async with self._lock:
            if not self._tracker.is_running or not self._tracker.is_paused:
                return

            self._tracker.resume()
            self._wakeup_event.set()

        LOGGER.debug("Timer resumed")

    async def get_elapsed_time(self) -> float:
        """Calculate and return the current elapsed time."""
        async with self._lock:
            return min(self._tracker.get_elapsed(), self._duration)

    async def get_time_remaining(self) -> float:
        """Calculate and return the current time remaining."""
        async with self._lock:
            if not self._tracker.is_running:
                return self._duration
            elapsed = self._tracker.get_elapsed()
            return max(self._duration - elapsed, 0.0)

    def get_is_running(self) -> bool:
        """Check if the timer is running."""
        return self._tracker.is_running and self._tracker.get_elapsed() < self._duration

    async def register_callback(
        self,
        time_point: float,
        callback: Callable[[], Awaitable[None]],
        *,
        from_end: bool = False,
    ) -> None:
        """Register an async callback at a specific elapsed time point."""
        async with self._lock:
            callback_info = _CallbackInfo(
                callback=callback, from_end=from_end, time_point=time_point
            )
            if from_end:
                self._relative_callbacks[time_point].append(callback_info)
                # If running, dynamically update absolute_callbacks to maintain state
                if self._tracker.is_running:
                    absolute_time = self._duration - time_point
                    if absolute_time >= 0:
                        self._absolute_callbacks[absolute_time].append(callback_info)
                        self._wakeup_event.set()
            else:
                self._absolute_callbacks[time_point].append(callback_info)
                if self._tracker.is_running:
                    self._wakeup_event.set()

    async def remove_callback(
        self,
        time_point: float,
        callback: Callable[[], Awaitable[None]],
        *,
        from_end: bool = False,
    ) -> bool:
        """Remove a specific callback from a time point."""
        async with self._lock:
            callback_dict = (
                self._relative_callbacks if from_end else self._absolute_callbacks
            )
            if time_point not in callback_dict:
                return False

            removed = False
            for i, callback_info in enumerate(callback_dict[time_point]):
                if callback_info.callback == callback:
                    del callback_dict[time_point][i]
                    if not callback_dict[time_point]:
                        del callback_dict[time_point]
                    removed = True
                    break

            if removed and self._tracker.is_running:
                # If we removed a relative callback, also try to remove it from absolute
                if from_end:
                    absolute_time = self._duration - time_point
                    if absolute_time in self._absolute_callbacks:
                        abs_list = self._absolute_callbacks[absolute_time]
                        for i, cb_info in enumerate(abs_list):
                            if cb_info.callback == callback:
                                del abs_list[i]
                                if not abs_list:
                                    del self._absolute_callbacks[absolute_time]
                                break
                self._wakeup_event.set()
            return removed

    async def clear_callbacks(
        self,
        time_point: float | None = None,
        *,
        from_end: bool = False,
    ) -> None:
        """Clear callbacks at a specific time point, or all callbacks."""
        async with self._lock:
            if time_point is None:
                self._absolute_callbacks.clear()
                self._relative_callbacks.clear()
            else:
                callback_dict = (
                    self._relative_callbacks if from_end else self._absolute_callbacks
                )
                if time_point in callback_dict:
                    del callback_dict[time_point]

                    if from_end and self._tracker.is_running:
                        absolute_time = self._duration - time_point
                        if absolute_time in self._absolute_callbacks:
                            del self._absolute_callbacks[absolute_time]

            if self._tracker.is_running:
                self._wakeup_event.set()

    async def _timer_loop(self) -> None:
        """Main timer loop that sleeps until next callback or timer end."""
        while True:
            if self._tracker.is_paused:
                __ = await self._wakeup_event.wait()
                self._wakeup_event.clear()
                continue

            async with self._lock:
                elapsed = self._tracker.get_elapsed()
                remaining = self._duration - elapsed

            if remaining <= 0:
                break

            # Get sorted callback time points
            async with self._lock:
                callback_times = sorted(
                    t
                    for t in self._absolute_callbacks
                    if t > elapsed and t <= self._duration
                )

            sleep_time = remaining
            next_callback = None

            if callback_times:
                next_callback = callback_times[0]
                sleep_time = min(next_callback - elapsed, remaining)

            if sleep_time > 0:
                try:
                    async with asyncio.timeout(sleep_time):
                        __ = await self._wakeup_event.wait()
                        self._wakeup_event.clear()
                        continue  # event triggered, loop will recalculate
                except TimeoutError:
                    pass  # normal sleep completion

            # Check if we reached a callback time
            async with self._lock:
                current_elapsed = self._tracker.get_elapsed()
                callbacks_to_fire: list[_CallbackInfo] = []

                if next_callback is not None and current_elapsed >= next_callback:
                    callbacks_to_fire = self._absolute_callbacks.get(
                        next_callback,
                        [],
                    ).copy()

            if callbacks_to_fire:
                for callback_info in callbacks_to_fire:
                    LOGGER.debug(f"Firing callback at {next_callback}")
                    await callback_info.callback()


class EventHandle:
    """Handle for identifying and removing timeline events."""

    def __init__(self, event_id: str) -> None:
        self._event_id: str = event_id

    @property
    def event_id(self) -> str:
        """Return the event ID."""
        return self._event_id


@dataclass(frozen=True)
class EventContext:
    """Context passed to event callbacks containing timeline information."""

    current_time: float
    event_start: float
    event_end: float
    event_progress: float


class SeekMode(Enum):
    """Mode for seek behavior."""

    RANGE = 1
    POINT = 2


@dataclass
class _TimelineEvent:
    """Internal dataclass for storing timeline event information."""

    event_id: str
    start_time: float
    end_time: float
    start_callback: Callable[[EventContext], Awaitable[None]] | None
    end_callback: Callable[[EventContext], Awaitable[None]] | None


class Timeline:
    """An asyncio-safe media timeline with event callbacks."""

    _TIME_EPSILON: float = 0.001

    def __init__(self, *, seek_mode: SeekMode = SeekMode.RANGE) -> None:
        """Initialize the timeline."""
        self._duration: float = 0.0
        self._start_position: float = 0.0
        self._end_position: float = 0.0
        self._events: list[_TimelineEvent] = []
        self._seek_mode: SeekMode = seek_mode
        self._task: asyncio.Task[None] | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

        self._tracker: _TimeTracker = _TimeTracker()
        self._wakeup_event: asyncio.Event = asyncio.Event()

    async def start(self, start_time: float, end_time: float) -> None:
        """Start the timeline playback."""
        if start_time >= end_time:
            msg = f"start_time ({start_time}) must be less than end_time ({end_time})"
            raise ValueError(msg)

        async with self._lock:
            if self.get_is_playing():
                msg = "Timeline is already playing"
                raise RuntimeError(msg)

            self._start_position = start_time
            self._end_position = end_time
            self._duration = end_time - start_time

            self._tracker.start()
            self._tracker.set_elapsed(start_time - self._start_position)

        LOGGER.debug(f"Timeline started from {start_time} to {end_time}")
        if self._task:
            __ = self._task.cancel()
        self._wakeup_event.clear()
        self._task = asyncio.create_task(self._playback_loop())

    async def stop(self) -> None:
        """Stop the timeline and reset to initial state."""
        async with self._lock:
            if self._task:
                __ = self._task.cancel()
                self._task = None

            self._tracker.stop()
            self._start_position = 0.0
            self._end_position = 0.0
            self._duration = 0.0

        LOGGER.debug("Timeline stopped")

    async def pause(self) -> None:
        """Pause the timeline, preserving current position."""
        async with self._lock:
            if not self._tracker.is_running or self._tracker.is_paused:
                return

            self._tracker.pause()
            self._wakeup_event.set()

        LOGGER.debug("Timeline paused")

    async def resume(self) -> None:
        """Resume the timeline from paused state."""
        async with self._lock:
            if not self._tracker.is_running or not self._tracker.is_paused:
                return

            self._tracker.resume()
            self._wakeup_event.set()

        LOGGER.debug("Timeline resumed")

    async def seek(self, time: float) -> None:
        """Seek to a specific time position."""
        async with self._lock:
            old_time = await self._get_current_time_sync()

            self._tracker.set_elapsed(time - self._start_position)
            self._wakeup_event.set()

        LOGGER.debug(f"Timeline seeked from {old_time} to {time}")
        await self._process_seek(old_time, time)

    async def add_event(
        self,
        start_time: float,
        length: float,
        start_callback: Callable[[EventContext], Awaitable[None]] | None,
        end_callback: Callable[[EventContext], Awaitable[None]] | None,
    ) -> EventHandle:
        """Add an event to the timeline."""
        if length < 0:
            msg = f"Event length must be positive, got {length}"
            raise ValueError(msg)

        event_id = str(uuid.uuid4())
        end_time = start_time + length

        async with self._lock:
            event = _TimelineEvent(
                event_id=event_id,
                start_time=start_time,
                end_time=end_time,
                start_callback=start_callback,
                end_callback=end_callback,
            )
            self._events.append(event)
            self._events.sort(key=lambda e: e.start_time)
            if self._tracker.is_running:
                self._wakeup_event.set()

        return EventHandle(event_id)

    async def remove_event(self, handle: EventHandle) -> bool:
        """Remove an event from the timeline."""
        async with self._lock:
            for i, event in enumerate(self._events):
                if event.event_id == handle.event_id:
                    del self._events[i]
                    if self._tracker.is_running:
                        self._wakeup_event.set()
                    return True
            return False

    async def clear_events(self) -> None:
        """Remove all events from the timeline."""
        async with self._lock:
            self._events.clear()
            if self._tracker.is_running:
                self._wakeup_event.set()

    def set_seek_mode(self, mode: SeekMode) -> None:
        """Set the seek behavior mode."""
        self._seek_mode = mode

    async def get_current_time(self) -> float:
        """Get the current timeline position."""
        async with self._lock:
            return await self._get_current_time_sync()

    async def _get_current_time_sync(self) -> float:
        """Internal sync get current time."""
        if not self._tracker.is_running:
            return self._start_position

        elapsed = self._tracker.get_elapsed()
        return min(elapsed + self._start_position, self._end_position)

    def get_is_playing(self) -> bool:
        """Check if the timeline is playing."""
        return self._tracker.is_running and self._tracker.get_elapsed() < self._duration

    async def _process_seek(self, old_time: float, new_time: float) -> None:
        """Process seek operation by firing appropriate callbacks."""
        async with self._lock:
            events = self._events.copy()
            mode = self._seek_mode

        if mode == SeekMode.RANGE:
            await self._process_seek_range(old_time, new_time, events)
        else:
            await self._process_seek_point(new_time, events)

    async def _process_seek_range(
        self,
        old_time: float,
        new_time: float,
        events: list[_TimelineEvent],
    ) -> None:
        """Process seek in RANGE mode."""
        forward = new_time >= old_time
        start, end = (old_time, new_time) if forward else (new_time, old_time)

        events_to_fire: list[
            tuple[Callable[[EventContext], Awaitable[None]], EventContext]
        ] = []

        for event in events:
            if start <= event.start_time <= end and event.start_callback:
                context = EventContext(
                    current_time=event.start_time,
                    event_start=event.start_time,
                    event_end=event.end_time,
                    event_progress=0.0,
                )
                events_to_fire.append((event.start_callback, context))

            if start <= event.end_time <= end and event.end_callback:
                context = EventContext(
                    current_time=event.end_time,
                    event_start=event.start_time,
                    event_end=event.end_time,
                    event_progress=event.end_time - event.start_time,
                )
                events_to_fire.append((event.end_callback, context))

        if not forward:
            events_to_fire.reverse()

        for callback, context in events_to_fire:
            await callback(context)

    async def _process_seek_point(
        self,
        time: float,
        events: list[_TimelineEvent],
    ) -> None:
        """Process seek in POINT mode."""
        for event in events:
            if event.start_time <= time < event.end_time:
                context = EventContext(
                    current_time=time,
                    event_start=event.start_time,
                    event_end=event.end_time,
                    event_progress=time - event.start_time,
                )
                if event.start_callback:
                    await event.start_callback(context)
                if event.end_callback:
                    await event.end_callback(context)

    async def _playback_loop(self) -> None:
        """Main playback loop that sleeps until next event boundary."""
        while True:
            if self._tracker.is_paused:
                __ = await self._wakeup_event.wait()
                self._wakeup_event.clear()
                continue

            async with self._lock:
                current_time = await self._get_current_time_sync()
                remaining = self._end_position - current_time

            if remaining <= 0:
                break

            async with self._lock:
                callbacks_prerun: list[
                    tuple[Callable[[EventContext], Awaitable[None]], EventContext]
                ] = []
                for event in self._events:
                    if (
                        abs(event.start_time - current_time) < self._TIME_EPSILON
                        and event.start_callback
                    ):
                        context = EventContext(
                            current_time=current_time,
                            event_start=event.start_time,
                            event_end=event.end_time,
                            event_progress=0.0,
                        )
                        callbacks_prerun.append((event.start_callback, context))
                    elif (
                        event.start_time < current_time < event.end_time
                        and event.start_callback
                    ):
                        context = EventContext(
                            current_time=current_time,
                            event_start=event.start_time,
                            event_end=event.end_time,
                            event_progress=current_time - event.start_time,
                        )
                        callbacks_prerun.append((event.start_callback, context))

            for cb, ctx in callbacks_prerun:
                await cb(ctx)

            async with self._lock:
                next_boundaries: list[float] = []
                for event in self._events:
                    if (
                        event.start_time > current_time
                        and event.start_time <= self._end_position
                    ):
                        next_boundaries.append(event.start_time)
                    if (
                        event.end_time > current_time
                        and event.end_time <= self._end_position
                    ):
                        next_boundaries.append(event.end_time)

                next_boundaries.sort()

            sleep_time = remaining
            if next_boundaries:
                sleep_time = min(next_boundaries[0] - current_time, remaining)

            if sleep_time > 0:
                try:
                    async with asyncio.timeout(sleep_time):
                        __ = await self._wakeup_event.wait()
                        self._wakeup_event.clear()
                        continue  # event triggered, loop will recalculate
                except TimeoutError:
                    pass

            async with self._lock:
                current_time = await self._get_current_time_sync()
                callbacks_to_fire: list[
                    tuple[Callable[[EventContext], Awaitable[None]], EventContext]
                ] = []
                for event in self._events:
                    if (
                        abs(event.start_time - current_time) < self._TIME_EPSILON
                        and event.start_callback
                    ):
                        context = EventContext(
                            current_time=current_time,
                            event_start=event.start_time,
                            event_end=event.end_time,
                            event_progress=0.0,
                        )
                        callbacks_to_fire.append((event.start_callback, context))
                    if (
                        abs(event.end_time - current_time) < self._TIME_EPSILON
                        and event.end_callback
                    ):
                        context = EventContext(
                            current_time=current_time,
                            event_start=event.start_time,
                            event_end=event.end_time,
                            event_progress=event.end_time - event.start_time,
                        )
                        callbacks_to_fire.append((event.end_callback, context))

            for cb, ctx in callbacks_to_fire:
                await cb(ctx)
