from __future__ import annotations

from typing import TYPE_CHECKING

from bot.core.components import BaseService

if TYPE_CHECKING:
    from .cog import RavenfallWatcherCog
    from .watcher import RavenfallWatcher


class RavenfallWatcherService(BaseService):
    """Service for getting Ravenfall watchers."""

    def __init__(self, cog: RavenfallWatcherCog) -> None:
        super().__init__()
        self.watcher_cog: RavenfallWatcherCog = cog

    def get_watcher(self, channel_name: str) -> RavenfallWatcher:
        """Get the watcher for a given channel name."""
        result = self.watcher_cog.channel_name_to_watcher.get(channel_name.lower())
        if not result:
            msg = f"No watcher found for channel {channel_name}."
            raise ValueError(msg)
        return result
