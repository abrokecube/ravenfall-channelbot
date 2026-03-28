from __future__ import annotations
from bot.core.components import BaseService
from .event_sources import TwitchEventSource

from twitchAPI.twitch import Twitch


class TwitchService(BaseService):
    """Service for twitch related stuff."""

    def __init__(self, twitch_event_source: TwitchEventSource) -> None:
        super().__init__()
        self.event_source: TwitchEventSource = twitch_event_source
        self.twitches: dict[str, Twitch] = {}

    def get_twitch(self, channel_id: str):
        """Get an authenticated Twitch instance."""
        return self.twitches.get(channel_id)
    
    async def send_message(self, )
