from __future__ import annotations

from typing import TYPE_CHECKING

from bot.core.components import BaseService

if TYPE_CHECKING:
    from bot.integrations.ravenfall.event_sources import RavenfallEventSource


class RavenfallService(BaseService):
    """Service for Ravenfall."""

    def __init__(self, ravenfall: RavenfallEventSource) -> None:
        super().__init__()
        self.event_source: RavenfallEventSource = ravenfall

    def get_ravenfall_instance(
        self, *, channel_name: str | None = None, channel_id: str | None = None
    ):
        """Get a Ravenfall instance by channel name or channel id."""
        if channel_name is not None:
            return self.event_source.channel_name_to_instance.get(channel_name)
        if channel_id is not None:
            return self.event_source.channel_id_to_instance.get(channel_id)
        return None

    def get_all_ravenfall_instances(self):
        """Get all Ravenfall instances."""
        return self.event_source.ravenfall_instances

    def get_ravennest(self):
        """Get an authenticated RavenNest instance."""
        return self.event_source.ravennest_api
