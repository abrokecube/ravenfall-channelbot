from __future__ import annotations

from typing import TYPE_CHECKING

from bot.core.components import BaseService

if TYPE_CHECKING:
    from bot.cogs.ravenfall_scroll_queue import RFScrollQueueCog


class RavenfallScrollQueueService(BaseService):
    """Service for managing the scroll queue."""

    def __init__(self, cog: RFScrollQueueCog) -> None:
        super().__init__()
        self.cog: RFScrollQueueCog = cog

    def get_queue(self, channel_name: str):
        """Get channel scroll queue."""
        if channel_name in self.cog.queues:
            return self.cog.queues[channel_name]
        return None
