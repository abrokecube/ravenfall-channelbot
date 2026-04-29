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


@dataclass
class _CallbackInfo:
    """Internal dataclass for storing callback information."""

    callback: Callable[[], Awaitable[None]]
    from_end: bool
    time_point: float


class Timer:
    """An asyncio-safe countdown timer with callback support.

    The timer efficiently uses asyncio.sleep to minimize resource usage,
    only waking when callbacks need to fire or the timer ends.
    Elapsed time is calculated on-demand.
    """

    def __init__(self) -> None:
        """Initialize the timer with a duration in seconds.

        Args:
            duration: Total countdown duration in seconds.
        """
        self._duration: float = 60
        self._start_time: float = 0.0
        self._elapsed_paused: float = 0.0
        self._is_running: bool = False
        self._is_paused: bool = False
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
            if self._is_running:
                msg = "Timer is already running"
                raise RuntimeError(msg)

            self._duration = duration
            self._is_running = True
            self._is_paused = False
            self._start_time = monotonic()
            self._elapsed_paused = 0.0

            # Recalculate absolute times for relative callbacks
            self._absolute_callbacks.clear()
            for time_point, callback_infos in self._relative_callbacks.items():
                absolute_time = self._duration - time_point
                if absolute_time >= 0:
                    self._absolute_callbacks[absolute_time].extend(callback_infos)

        LOGGER.debug(f"Timer started with duration {self._duration}")
        self._task = asyncio.create_task(self._timer_loop())

    async def stop(self) -> None:
        """Stop the timer and reset to initial state."""
        async with self._lock:
            if self._task:
                __ = self._task.cancel()
                self._task = None

            self._is_running = False
            self._is_paused = False
            self._start_time = 0.0
            self._elapsed_paused = 0.0

        LOGGER.debug("Timer stopped")

    async def pause(self) -> None:
        """Pause the timer, preserving current elapsed time."""
        async with self._lock:
            if not self._is_running or self._is_paused:
                return

            self._is_paused = True
            self._elapsed_paused = await self.get_elapsed_time()

            if self._task:
                __ = self._task.cancel()
                self._task = None

        LOGGER.debug(f"Timer paused at elapsed time {self._elapsed_paused}")

    async def resume(self) -> None:
        """Resume the timer from paused state."""
        async with self._lock:
            if not self._is_running or not self._is_paused:
                return

            self._is_paused = False
            self._start_time = monotonic()

        LOGGER.debug("Timer resumed")
        self._task = asyncio.create_task(self._timer_loop())

    async def get_elapsed_time(self) -> float:
        """Calculate and return the current elapsed time.

        Returns:
            Current elapsed time in seconds.
        """
        async with self._lock:
            if not self._is_running:
                return 0.0

            if self._is_paused:
                return self._elapsed_paused

            current_time = monotonic()
            elapsed = current_time - self._start_time + self._elapsed_paused
            return min(elapsed, self._duration)

    async def get_time_remaining(self) -> float:
        """Calculate and return the current time remaining.

        Returns:
            Current time remaining in seconds.
        """
        async with self._lock:
            if not self._is_running:
                return self._duration

            if self._is_paused:
                return self._elapsed_paused

            current_time = monotonic()
            elapsed = current_time - self._start_time + self._elapsed_paused
            return max(self._duration - elapsed, 0)

    def get_is_running(self):
        """Check if the timer is running."""
        return self._is_running

    async def register_callback(
        self,
        time_point: float,
        callback: Callable[[], Awaitable[None]],
        *,
        from_end: bool = False,
    ) -> None:
        """Register an async callback at a specific elapsed time point.

        The callback will be fired once when the timer reaches the
        specified elapsed time. Callbacks persist until manually removed.

        Args:
            time_point: Time in seconds when the callback should fire.
                If from_end=False, this is elapsed time from start.
                If from_end=True, this is time remaining before end.
            callback: Async callback function to execute.
            from_end: If True, time_point is relative to timer end (time remaining).
                If False, time_point is relative to timer start (elapsed time).
        """
        async with self._lock:
            callback_info = _CallbackInfo(
                callback=callback, from_end=from_end, time_point=time_point
            )
            if from_end:
                self._relative_callbacks[time_point].append(callback_info)
            else:
                self._absolute_callbacks[time_point].append(callback_info)

    async def remove_callback(
        self,
        time_point: float,
        callback: Callable[[], Awaitable[None]],
        *,
        from_end: bool = False,
    ) -> bool:
        """Remove a specific callback from a time point.

        Args:
            time_point: Time point where the callback is registered.
            callback: Callback function to remove.
            from_end: If True, search in relative callbacks. If False, search in absolute.

        Returns:
            True if the callback was removed, False if not found.
        """
        async with self._lock:
            callback_dict = (
                self._relative_callbacks if from_end else self._absolute_callbacks
            )
            if time_point not in callback_dict:
                return False

            for i, callback_info in enumerate(callback_dict[time_point]):
                if callback_info.callback == callback:
                    del callback_dict[time_point][i]
                    if not callback_dict[time_point]:
                        del callback_dict[time_point]
                    return True
            return False

    async def clear_callbacks(
        self,
        time_point: float | None = None,
        *,
        from_end: bool = False,
    ) -> None:
        """Clear callbacks at a specific time point, or all callbacks.

        Args:
            time_point: Specific time point to clear, or None to clear all.
            from_end: If True, clear from relative callbacks. If False, clear from
                absolute. Only used when time_point is specified.
        """
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

    async def _timer_loop(self) -> None:
        """Main timer loop that sleeps until next callback or timer end."""
        try:
            while True:
                elapsed = await self.get_elapsed_time()
                remaining = self._duration - elapsed

                if remaining <= 0:
                    break

                # Get sorted callback time points
                async with self._lock:
                    callback_times = sorted(
                        t
                        for t in self._absolute_callbacks.keys()
                        if t > elapsed and t <= self._duration
                    )

                if not callback_times:
                    # No callbacks, sleep until timer ends
                    await asyncio.sleep(remaining)
                    break

                next_callback = callback_times[0]
                sleep_time = min(next_callback - elapsed, remaining)

                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

                # Check if we reached a callback time
                current_elapsed = await self.get_elapsed_time()
                callbacks_to_fire: list[_CallbackInfo] = []
                async with self._lock:
                    if current_elapsed >= next_callback:
                        callbacks_to_fire = self._absolute_callbacks.get(
                            next_callback,
                            [],
                        ).copy()

                if callbacks_to_fire:
                    for callback_info in callbacks_to_fire:
                        LOGGER.debug(f"Firing callback at {next_callback}")
                        await callback_info.callback()

        except asyncio.CancelledError:
            __ = asyncio.CancelledError("Timer loop cancelled")
            raise


class EventHandle:
    """Handle for identifying and removing timeline events."""

    def __init__(self, event_id: str) -> None:
        """Initialize the event handle.

        Args:
            event_id: Unique identifier for the event.
        """
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
    """An asyncio-safe media timeline with event callbacks.

    The timeline manages events with start/end callbacks and supports
    seekable playback with configurable seek behavior.
    """

    _TIME_EPSILON: float = 0.001

    def __init__(self, *, seek_mode: SeekMode = SeekMode.RANGE) -> None:
        """Initialize the timeline."""
        self._current_time: float = 0.0
        self._is_playing: bool = False
        self._is_paused: bool = False
        self._duration: float = 0.0
        self._start_position: float = 0.0
        self._end_position: float = 0.0
        self._events: list[_TimelineEvent] = []
        self._seek_mode: SeekMode = seek_mode
        self._task: asyncio.Task[None] | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._start_time: float = 0.0
        self._elapsed_paused: float = 0.0

    async def start(self, start_time: float, end_time: float) -> None:
        """Start the timeline playback.

        Args:
            start_time: Starting position in seconds. Can be negative for countdown.
            end_time: Ending position in seconds. Must be greater than start_time.

        Raises:
            RuntimeError: If the timeline is already playing.
            ValueError: If start_time >= end_time.
        """
        if start_time >= end_time:
            msg = f"start_time ({start_time}) must be less than end_time ({end_time})"
            raise ValueError(msg)

        async with self._lock:
            if self._is_playing:
                msg = "Timeline is already playing"
                raise RuntimeError(msg)

            self._start_position = start_time
            self._end_position = end_time
            self._duration = end_time - start_time
            self._is_playing = True
            self._is_paused = False
            self._current_time = start_time
            self._start_time = monotonic()
            self._elapsed_paused = 0.0

        LOGGER.debug(f"Timeline started from {start_time} to {end_time}")
        self._task = asyncio.create_task(self._playback_loop())

    async def stop(self) -> None:
        """Stop the timeline and reset to initial state."""
        async with self._lock:
            if self._task:
                __ = self._task.cancel()
                self._task = None

            self._is_playing = False
            self._is_paused = False
            self._current_time = 0.0
            self._start_position = 0.0
            self._end_position = 0.0
            self._duration = 0.0
            self._start_time = 0.0
            self._elapsed_paused = 0.0

        LOGGER.debug("Timeline stopped")

    async def pause(self) -> None:
        """Pause the timeline, preserving current position."""
        async with self._lock:
            if not self._is_playing or self._is_paused:
                return

            self._is_paused = True
            self._elapsed_paused = self._current_time

            if self._task:
                __ = self._task.cancel()
                self._task = None

        LOGGER.debug(f"Timeline paused at {self._elapsed_paused}")

    async def resume(self) -> None:
        """Resume the timeline from paused state."""
        async with self._lock:
            if not self._is_playing or not self._is_paused:
                return

            self._is_paused = False
            self._start_time = monotonic()

        LOGGER.debug("Timeline resumed")
        self._task = asyncio.create_task(self._playback_loop())

    async def seek(self, time: float) -> None:
        """Seek to a specific time position.

        Args:
            time: Target time position in seconds. Can be negative for countdown.

        Raises:
            ValueError: If time is outside valid range [start_position, end_position].
        """
        async with self._lock:
            if time < self._start_position or time > self._end_position:
                msg = (
                    f"Time must be between {self._start_position} "
                    f"and {self._end_position}"
                )
                raise ValueError(msg)

            old_time = self._current_time
            self._current_time = time

            if self._is_paused:
                self._elapsed_paused = time
            else:
                self._start_time = monotonic()
                self._elapsed_paused = 0.0

        LOGGER.debug(f"Timeline seeked from {old_time} to {time}")
        await self._process_seek(old_time, time)

    async def add_event(
        self,
        start_time: float,
        length: float,
        start_callback: Callable[[EventContext], Awaitable[None]] | None,
        end_callback: Callable[[EventContext], Awaitable[None]] | None,
    ) -> EventHandle:
        """Add an event to the timeline.

        Args:
            start_time: When the event begins in seconds.
            length: Duration of the event in seconds.
            start_callback: Async callback fired when event starts.
            end_callback: Async callback fired when event ends.

        Returns:
            EventHandle object for removing the event later.
        """
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

        return EventHandle(event_id)

    async def remove_event(self, handle: EventHandle) -> bool:
        """Remove an event from the timeline.

        Args:
            handle: EventHandle returned by add_event.

        Returns:
            True if the event was removed, False if not found.
        """
        async with self._lock:
            for i, event in enumerate(self._events):
                if event.event_id == handle.event_id:
                    del self._events[i]
                    return True
            return False

    async def clear_events(self) -> None:
        """Remove all events from the timeline."""
        async with self._lock:
            self._events.clear()

    def set_seek_mode(self, mode: SeekMode) -> None:
        """Set the seek behavior mode.

        Args:
            mode: SeekMode.RANGE or SeekMode.POINT.
        """
        self._seek_mode = mode

    async def get_current_time(self) -> float:
        """Get the current timeline position.

        Returns:
            Current time in seconds. Can be negative for countdown.
        """
        async with self._lock:
            if not self._is_playing:
                return self._start_position

            if self._is_paused:
                return self._elapsed_paused

            current_time = monotonic()
            elapsed = current_time - self._start_time + self._elapsed_paused
            return min(elapsed + self._start_position, self._end_position)

    def get_is_playing(self) -> bool:
        """Check if the timeline is playing.

        Returns:
            True if playing, False otherwise.
        """
        return self._is_playing

    async def _process_seek(self, old_time: float, new_time: float) -> None:
        """Process seek operation by firing appropriate callbacks.

        Args:
            old_time: Previous timeline position.
            new_time: New timeline position.
        """
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
        """Process seek in RANGE mode.

        Fires start callbacks for events starting in [old_time, new_time]
        and end callbacks for events ending in [old_time, new_time].

        Args:
            old_time: Previous timeline position.
            new_time: New timeline position.
            events: Copy of events list.
        """
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
        """Process seek in POINT mode.

        Fires callbacks for events active at the seek point.

        Args:
            time: Seek position.
            events: Copy of events list.
        """
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
        try:
            while True:
                current_time = await self.get_current_time()
                remaining = self._duration - current_time

                if remaining <= 0:
                    break

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

                if not next_boundaries:
                    await asyncio.sleep(self._end_position - current_time)
                    break

                next_boundary = next_boundaries[0]
                sleep_time = min(next_boundary - current_time, remaining)

                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

                current_time = await self.get_current_time()

                async with self._lock:
                    for event in self._events:
                        if (
                            abs(event.start_time - current_time) < self._TIME_EPSILON
                            and event.start_callback
                        ):
                            LOGGER.debug(
                                f"Firing start callback for event at {event.start_time}"
                            )
                            context = EventContext(
                                current_time=current_time,
                                event_start=event.start_time,
                                event_end=event.end_time,
                                event_progress=0.0,
                            )
                            await event.start_callback(context)
                        if (
                            abs(event.end_time - current_time) < self._TIME_EPSILON
                            and event.end_callback
                        ):
                            LOGGER.debug(
                                f"Firing end callback for event at {event.end_time}"
                            )
                            context = EventContext(
                                current_time=current_time,
                                event_start=event.start_time,
                                event_end=event.end_time,
                                event_progress=event.end_time - event.start_time,
                            )
                            await event.end_callback(context)

        except asyncio.CancelledError:
            __ = asyncio.CancelledError("Playback loop cancelled")
            raise
