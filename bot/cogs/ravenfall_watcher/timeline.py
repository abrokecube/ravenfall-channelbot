"""timeline.py — Async-native Timeline with event callbacks and seek modes.

Usage:
    timeline = Timeline()
    event = timeline.add_event(0.0, 5.0, on_enter, on_exit)
    await timeline.start(-10.0, 20.0)
    await timeline.seek(3.0)
    await timeline.pause()
    await timeline.resume()
    await timeline.stop()
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import enum
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import NamedTuple

from bot.core.components import fire_and_forget

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SeekMode(enum.Enum):
    """Controls which event callbacks fire during a seek operation.

    RANGE — every event whose boundary is crossed between the old and new
            cursor positions fires its enter/exit callback.
    POINT — only the events that overlap the old cursor position (exit) and
            the new cursor position (enter) fire their callbacks.
    """

    RANGE = "range"
    POINT = "point"


@dataclasses.dataclass(slots=True)
class EventInfo:
    """Payload passed to every event callback."""

    current_time: float
    event_start: float
    event_end: float
    event_progress: float  # seconds elapsed since event_start


# A callback may be a plain function or a coroutine function.
Callback = Callable[[EventInfo], None] | Callable[[EventInfo], Awaitable[None]]


@dataclasses.dataclass(slots=True)
class TimelineEvent:
    """A registered event on the timeline."""

    id: str
    start_time: float
    end_time: float
    start_callback: Callback | None
    end_callback: Callback | None

    def contains(self, t: float) -> bool:
        """Return True if *t* is within [start_time, end_time]."""
        return self.start_time <= t <= self.end_time


class _PlaybackState(enum.Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _TimeRange(NamedTuple):
    lo: float
    hi: float

    @classmethod
    def ordered(cls, a: float, b: float) -> _TimeRange:
        return cls(min(a, b), max(a, b))

    def contains(self, t: float) -> bool:
        return self.lo <= t <= self.hi

    def boundary_crossed(self, t: float) -> bool:
        """True if *t* is strictly inside (not at an endpoint)."""
        return self.lo < t < self.hi


async def _invoke(cb: Callback, info: EventInfo) -> None:
    result = cb(info)
    if asyncio.iscoroutine(result):
        # await result
        fire_and_forget(result)


type Crossing = tuple[float, str, TimelineEvent]  # (t, kind, event)

# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


class Timeline:
    """Async-native timeline with floating-point time, event callbacks, and
    configurable seek behaviour.

    Time values may be negative or positive.  The internal tick loop uses
    long ``asyncio.sleep`` calls so it wakes up only when the next event
    boundary is about to be crossed, keeping CPU usage minimal.
    """

    # Maximum sleep between loop ticks even when no events are registered.
    _MAX_SLEEP: float = 1.0

    def __init__(self) -> None:
        self._events: dict[str, TimelineEvent] = {}
        self._state: _PlaybackState = _PlaybackState.STOPPED
        self._seek_mode: SeekMode = SeekMode.RANGE

        # Timeline window
        self._start_time: float = 0.0
        self._end_time: float = 0.0

        # Cursor tracking
        self._current_time: float = 0.0

        # Wall-clock anchor: the real time at which _current_time was last set.
        self._wall_anchor: float = 0.0

        # Set of event IDs whose "inside" state is currently active.
        self._active_events: set[str] = set()

        # Background task handle
        self._task: asyncio.Task[None] | None = None

        # Pause / resume coordination
        self._pause_event: asyncio.Event = asyncio.Event()
        self._pause_event.set()  # not paused initially

    # ------------------------------------------------------------------
    # Public API — control
    # ------------------------------------------------------------------

    async def start(self, start_time: float, end_time: float) -> None:
        """Begin playback from *start_time* towards *end_time*.

        Calling ``start`` while already playing stops the current playback
        first and restarts cleanly.
        """
        if self._state != _PlaybackState.STOPPED:
            await self.stop()

        self._start_time = start_time
        self._end_time = end_time
        self._current_time = start_time
        self._wall_anchor = time.monotonic()
        self._active_events.clear()
        self._pause_event.set()
        self._state = _PlaybackState.PLAYING

        logger.debug("Starting timeline: %f -> %f", start_time, end_time)
        self._task = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        """Halt playback and reset the cursor to the start position."""
        if self._state == _PlaybackState.STOPPED:
            return

        logger.debug("Stopping timeline")
        self._state = _PlaybackState.STOPPED
        self._pause_event.set()  # unblock loop so it can exit

        if self._task is not None:
            __ = self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        # Fire exit callbacks for any events still active.
        await self._deactivate_all(self._current_time)
        self._active_events.clear()

    async def pause(self) -> None:
        """Freeze the cursor without resetting it."""
        if self._state != _PlaybackState.PLAYING:
            return
        self._state = _PlaybackState.PAUSED
        logger.debug("Pausing timeline at %f", self._current_time)
        self._snapshot_time()  # capture cursor position at wall-clock now
        self._pause_event.clear()  # block the tick loop

    async def resume(self) -> None:
        """Continue playback from the current cursor position."""
        if self._state != _PlaybackState.PAUSED:
            return
        logger.debug("Resuming timeline from %f", self._current_time)
        self._wall_anchor = time.monotonic()
        self._state = _PlaybackState.PLAYING
        self._pause_event.set()

    async def seek(self, target_time: float) -> None:
        """Move the cursor to *target_time* and fire the appropriate callbacks."""
        was_playing = self._state == _PlaybackState.PLAYING
        if was_playing:
            # Freeze the wall-clock snapshot before we change _current_time.
            self._snapshot_time()

        old_time = self._current_time
        logger.debug(
            "Seeking from %f to %f (mode=%s)", old_time, target_time, self._seek_mode.name
        )
        new_time = max(self._start_time, min(self._end_time, target_time))

        await self._fire_seek_callbacks(old_time, new_time)

        self._current_time = new_time
        if was_playing or self._state == _PlaybackState.PAUSED:
            self._wall_anchor = time.monotonic()

        # Wake the tick loop so it recalculates its sleep duration.
        if was_playing:
            self._pause_event.set()

    # ------------------------------------------------------------------
    # Public API — events
    # ------------------------------------------------------------------

    def add_event(
        self,
        start_time: float,
        end_time: float,
        start_callback: Callback | None = None,
        end_callback: Callback | None = None,
    ) -> TimelineEvent:
        """Register an event and return the ``TimelineEvent`` handle."""
        if start_time > end_time:
            msg = f"start_time ({start_time}) must be <= end_time ({end_time})"
            raise ValueError(msg)
        event = TimelineEvent(
            id=str(uuid.uuid4()),
            start_time=start_time,
            end_time=end_time,
            start_callback=start_callback,
            end_callback=end_callback,
        )
        self._events[event.id] = event
        logger.debug("Added event %s: [%f, %f]", event.id, start_time, end_time)
        return event

    def remove_event(self, event: TimelineEvent) -> None:
        """Unregister *event*.  No callbacks are fired."""
        __ = self._events.pop(event.id, None)
        self._active_events.discard(event.id)
        logger.debug("Removed event %s", event.id)

    def clear_events(self) -> None:
        """Remove all registered events.  No callbacks are fired."""
        self._events.clear()
        self._active_events.clear()

    # ------------------------------------------------------------------
    # Public API — configuration / observation
    # ------------------------------------------------------------------

    def set_seek_mode(self, mode: SeekMode) -> None:
        """Change the seek mode (``SeekMode.RANGE`` or ``SeekMode.POINT``)."""
        self._seek_mode = mode

    def get_current_time(self) -> float:
        """Return the current cursor position in timeline seconds."""
        if self._state == _PlaybackState.PLAYING:
            self._snapshot_time()
        return self._current_time

    def get_is_playing(self) -> bool:
        """Return ``True`` while the timeline is advancing (not paused/stopped)."""
        return self._state == _PlaybackState.PLAYING

    # ------------------------------------------------------------------
    # Internal — tick loop
    # ------------------------------------------------------------------

    async def _tick_loop(self) -> None:
        """Main async loop.

        Sleeps until the next event boundary, then fires
        the relevant callbacks and recalculates the next sleep duration.
        """
        forward = self._end_time >= self._start_time

        # Fire callbacks for any events active at the very start position
        # before the first sleep.  This handles events whose start_time equals
        # the timeline start_time (e.g. start(-120, 0) with an event at -120).
        await self._sync_active_events(self._current_time)

        while self._state != _PlaybackState.STOPPED:
            # Block here while paused.
            __ = await self._pause_event.wait()

            # Advance the cursor to "now".
            self._snapshot_time()
            now = self._current_time

            # Check if we've reached the end of the timeline.
            if (forward and now >= self._end_time) or (
                not forward and now <= self._end_time
            ):
                self._current_time = self._end_time
                # Sync first: fires enter for anything whose boundary lands
                # exactly on end_time (e.g. a zero-duration event at t=0).
                await self._sync_active_events(self._end_time)
                # Deactivate everything still active.  A second sync would
                # not help here: events whose end_time == self._end_time still
                # satisfy contains(), so they never transition to "outside"
                # through the normal diff and their exit callback would be lost.
                await self._deactivate_all(self._end_time)
                self._active_events.clear()
                logger.debug("Timeline reached end: %f", self._end_time)
                self._state = _PlaybackState.STOPPED
                break

            # Synchronise active-event state (fires callbacks as needed).
            await self._sync_active_events(now)

            # Calculate how long to sleep until the next event boundary.
            sleep_duration = self._sleep_until_next_boundary(now, forward=forward)
            logger.debug(
                "Tick: time=%f, active=%d, sleeping=%fs",
                now,
                len(self._active_events),
                sleep_duration,
            )
            try:
                await asyncio.sleep(sleep_duration)
            except asyncio.CancelledError:
                break

    def _sleep_until_next_boundary(self, now: float, *, forward: bool = True) -> float:
        """Return the number of *wall-clock* seconds to sleep before the next
        interesting moment (an event boundary or the end of the timeline).

        Timeline-time advances at 1 s/s, so the gap in timeline units equals
        the gap in wall-clock seconds.  We add a small epsilon so the loop
        wakes just *after* the boundary rather than exactly on it, and cap at
        _MAX_SLEEP so a very distant boundary doesn't cause a minutes-long sleep.
        """
        _epsilon = 0.001  # wake 1 ms after the boundary
        candidates: list[float] = [self._end_time]

        for ev in self._events.values():
            if forward:
                # >= so a boundary exactly at now is still a candidate
                # (happens right after a seek lands on a boundary).
                if ev.start_time >= now:
                    candidates.append(ev.start_time)
                if ev.end_time >= now:
                    candidates.append(ev.end_time)
            else:
                if ev.start_time <= now:
                    candidates.append(ev.start_time)
                if ev.end_time <= now:
                    candidates.append(ev.end_time)

        if forward:
            next_boundary = min(candidates)
            gap = next_boundary - now
        else:
            next_boundary = max(candidates)
            gap = now - next_boundary

        # gap is in timeline-seconds == wall-clock seconds (speed = 1 s/s).
        return max(0.0, min(gap + _epsilon, self._MAX_SLEEP))

    # ------------------------------------------------------------------
    # Internal — cursor helpers
    # ------------------------------------------------------------------

    def _snapshot_time(self) -> None:
        """Update ``_current_time`` from the wall clock (playing only)."""
        if self._state != _PlaybackState.PLAYING:
            return
        elapsed = time.monotonic() - self._wall_anchor
        direction = 1.0 if self._end_time >= self._start_time else -1.0
        raw = self._current_time + direction * elapsed
        self._current_time = max(
            min(self._start_time, self._end_time),
            min(raw, max(self._start_time, self._end_time)),
        )
        self._wall_anchor = time.monotonic()

    # ------------------------------------------------------------------
    # Internal — active-event synchronisation
    # ------------------------------------------------------------------

    async def _sync_active_events(self, now: float) -> None:
        """Bring ``_active_events`` in line with the current cursor position,
        firing enter/exit callbacks for any transitions.
        """
        for ev in self._events.values():
            inside = ev.contains(now)
            was_inside = ev.id in self._active_events

            if inside and not was_inside:
                self._active_events.add(ev.id)
                await self._fire_enter(ev, now)
            elif not inside and was_inside:
                self._active_events.discard(ev.id)
                await self._fire_exit(ev, now)

    async def _deactivate_all(self, now: float) -> None:
        for eid in list(self._active_events):
            ev = self._events.get(eid)
            if ev is not None:
                await self._fire_exit(ev, now)

    # ------------------------------------------------------------------
    # Internal — seek callback logic
    # ------------------------------------------------------------------

    async def _fire_seek_callbacks(self, old_time: float, new_time: float) -> None:
        if self._seek_mode == SeekMode.RANGE:
            await self._seek_range(old_time, new_time)
        else:
            await self._seek_point(old_time, new_time)

    async def _seek_range(self, old_time: float, new_time: float) -> None:
        """Fire callbacks for every event boundary crossed between old and new."""
        __ = _TimeRange.ordered(old_time, new_time)
        moving_forward = new_time >= old_time

        # Collect all crossed boundaries with their type ('enter'/'exit').
        crossings: list[Crossing] = []

        for ev in self._events.values():
            was_inside = ev.contains(old_time)
            will_be_inside = ev.contains(new_time)

            # Boundary crossed going forward: start → enter, end → exit.
            if moving_forward:
                if ev.start_time > old_time and ev.start_time <= new_time:
                    crossings.append((ev.start_time, "enter", ev))
                if ev.end_time > old_time and ev.end_time <= new_time:
                    crossings.append((ev.end_time, "exit", ev))
            else:
                # Going backward: crossing an end boundary → enter, a start → exit.
                if ev.end_time < old_time and ev.end_time >= new_time:
                    crossings.append((ev.end_time, "enter", ev))
                if ev.start_time < old_time and ev.start_time >= new_time:
                    crossings.append((ev.start_time, "exit", ev))

            # Handle events that straddle the entire range.
            if (
                was_inside
                and not will_be_inside
                and ev.id not in {c[2].id for c in crossings if c[1] == "exit"}
            ):
                crossings.append((new_time, "exit", ev))
            if (
                not was_inside
                and will_be_inside
                and ev.id not in {c[2].id for c in crossings if c[1] == "enter"}
            ):
                crossings.append((new_time, "enter", ev))

        # Sort by time, in the direction of travel.
        crossings.sort(key=lambda c: c[0], reverse=not moving_forward)

        for t, kind, ev in crossings:
            if kind == "enter":
                self._active_events.add(ev.id)
                await self._fire_enter(ev, t)
            else:
                self._active_events.discard(ev.id)
                await self._fire_exit(ev, t)

        # Final sync at the new position.
        await self._sync_active_events(new_time)

    async def _seek_point(self, old_time: float, new_time: float) -> None:
        """Fire exit callbacks for events active at old_time, enter callbacks
        for events active at new_time.
        """
        # Exit events at old position that won't be active at new position.
        for eid in list(self._active_events):
            ev = self._events.get(eid)
            if ev is not None and not ev.contains(new_time):
                self._active_events.discard(eid)
                await self._fire_exit(ev, old_time)

        # Enter events at new position that weren't active at old position.
        for ev in self._events.values():
            if ev.contains(new_time) and ev.id not in self._active_events:
                self._active_events.add(ev.id)
                await self._fire_enter(ev, new_time)

    # ------------------------------------------------------------------
    # Internal — callback dispatch
    # ------------------------------------------------------------------

    async def _fire_enter(self, ev: TimelineEvent, now: float) -> None:
        if ev.start_callback is None:
            return
        info = EventInfo(
            current_time=now,
            event_start=ev.start_time,
            event_end=ev.end_time,
            event_progress=max(0.0, now - ev.start_time),
        )
        logger.debug("Firing ENTER for event %s at %f", ev.id, now)
        await _invoke(ev.start_callback, info)

    async def _fire_exit(self, ev: TimelineEvent, now: float) -> None:
        if ev.end_callback is None:
            return
        info = EventInfo(
            current_time=now,
            event_start=ev.start_time,
            event_end=ev.end_time,
            event_progress=max(0.0, now - ev.start_time),
        )
        logger.debug("Firing EXIT for event %s at %f", ev.id, now)
        await _invoke(ev.end_callback, info)


# ---------------------------------------------------------------------------
# Quick smoke-test (run with: python timeline.py)
# ---------------------------------------------------------------------------


# async def _demo() -> None:

#     def on_enter(info: EventInfo) -> None:
#         print(
#             f"  ▶ ENTER  t={info.current_time:+.3f}  "
#             f"event=[{info.event_start}, {info.event_end}]  "
#             f"progress={info.event_progress:.3f}s"
#         )

#     def on_exit(info: EventInfo) -> None:
#         print(
#             f"  ◀ EXIT   t={info.current_time:+.3f}  "
#             f"event=[{info.event_start}, {info.event_end}]  "
#             f"progress={info.event_progress:.3f}s"
#         )

#     tl = Timeline()
#     _e1 = tl.add_event(-5.0, -2.0, on_enter, on_exit)  # negative range
#     _e2 = tl.add_event(1.0, 3.0, on_enter, on_exit)
#     _e3 = tl.add_event(2.5, 6.0, on_enter, on_exit)

#     print("=== Starting playback from -6 to 8 ===")
#     await tl.start(-6.0, 8.0)

#     await asyncio.sleep(1.5)
#     print(f"\n--- pause at t={tl.get_current_time():+.3f} ---")
#     await tl.pause()

#     await asyncio.sleep(0.5)
#     print("\n--- seek (RANGE) to t=2.0 ---")
#     await tl.seek(2.0)

#     await asyncio.sleep(0.2)
#     print("\n--- seek (POINT) to t=5.0 ---")
#     tl.set_seek_mode(SeekMode.POINT)
#     await tl.seek(5.0)
#     tl.set_seek_mode(SeekMode.RANGE)

#     print("\n--- resume ---")
#     await tl.resume()

#     await asyncio.sleep(3.5)
#     print(f"\n--- stop (is_playing={tl.get_is_playing()}) ---")
#     await tl.stop()
#     print(f"Final cursor: {tl.get_current_time():+.3f}")


# if __name__ == "__main__":
#     asyncio.run(_demo())
