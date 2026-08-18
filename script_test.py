# ruff: noqa: CPY001, T201
from __future__ import annotations

import asyncio
from typing import override

from bot.core import EVENT_CATEGORY_GENERIC
from bot.core.components import (
    BaseEvent,
    BaseEventSource,
    Cog,
    EventManager,
    GlobalContext,
)
from bot.core.decorators import on_match


class PingEvent(BaseEvent):
    """A simple test event dispatched through the event system."""

    def __init__(self) -> None:
        super().__init__(
            categories=[EVENT_CATEGORY_GENERIC],
            data=None,
            process_concurrently=False,
        )


class TestEventSource(BaseEventSource):
    """Event source that emits a single PingEvent after setup."""

    @override
    async def setup(self, event_manager: EventManager) -> None:
        """Send a PingEvent once the source is registered."""
        await super().setup(event_manager)
        await self.send_event(PingEvent())


class TestCog(Cog):
    """Cog that listens for PingEvent."""

    @on_match(PingEvent)
    async def on_ping(self, event: PingEvent, _match: object) -> None:
        """Print the received event."""
        print(f"Got event: {event!r}")


async def main() -> None:
    """Dispatch a single test event through the event system."""
    global_ctx = GlobalContext()
    event_manager = EventManager(global_ctx)
    await event_manager.add_cog(TestCog)
    await event_manager.add_event_source(TestEventSource())
    await event_manager.teardown()
    await global_ctx.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
