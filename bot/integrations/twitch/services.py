from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

from twitchAPI.twitch import Twitch

from bot.core.components import BaseService
from bot.integrations.twitch.enums import MessageDeliveryMode

if TYPE_CHECKING:
    from .event_sources import TwitchEventSource

LOGGER = logging.getLogger(__name__)


class TwitchService(BaseService):
    """Service for twitch related stuff."""

    def __init__(self, twitch_event_source: TwitchEventSource) -> None:
        super().__init__()
        self.event_source: TwitchEventSource = twitch_event_source
        self.twitches: dict[str, Twitch] = {}

    def get_twitch(self, channel_id: str):
        """Get an authenticated Twitch instance."""
        return self.twitches.get(channel_id)

    async def _send_irc(self, channel_id: str, text: str, *, reply_id: str | None = None):
        settings = self.event_source.connected_chats[channel_id]
        if not self.event_source.twitch_chat:
            msg = "IRC Twitch chat has not been initialized"
            raise RuntimeError(msg)
        await self.event_source.twitch_chat.send_message(
            settings.channel_name, text, reply_id
        )

    async def _send_http(
        self, channel_id: str, text: str, *, reply_id: str | None = None
    ):
        twitch = self.get_twitch(channel_id)
        # twitch = self.event_source.bot_twitch
        if not twitch:
            msg = f"Channel id {channel_id} has not been authorized"
            raise ValueError(msg)
        __ = await twitch.send_chat_message(
            channel_id, self.event_source.bot_user_id, text, reply_id
        )

    async def send_chat_message(
        self,
        channel_id: str,
        text: str,
        *,
        me: bool = False,
        reply_id: str | None = None,
        use_http: bool | None = None,
    ):
        """Send a chat message."""
        max_len = 500
        if me:
            max_len -= 4
        if len(text.encode("utf-16-le")) > max_len:
            msg = f"Text must be below {max_len} characters."
            raise ValueError(msg)
        if not channel_id in self.event_source.connected_chats:
            msg = f"Not connected to chat room of {channel_id}"
            raise ValueError(msg)
        settings = self.event_source.connected_chats[channel_id]
        if use_http is None:
            use_http = settings.message_delivery_mode == MessageDeliveryMode.HELIX

        methods = []
        if use_http:
            methods = [self._send_http, self._send_irc]
        else:
            methods = [self._send_irc, self._send_http]
        for m in methods:
            try:
                _ = await m(channel_id, text, reply_id=reply_id)
                break
            except Exception:
                LOGGER.warning("Failed to send message", exc_info=True)
                continue

    @override
    async def teardown(self):
        for t in self.twitches.values():
            await t.close()
        return await super().teardown()
