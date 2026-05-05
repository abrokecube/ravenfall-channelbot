from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any, override

from twitchAPI.object.api import SendMessageResponse

from bot.integrations.chat_messages import BaseMessageService, MessageSendResult
from utils.rate_limiter import AsyncRateLimiter
from utils.strutils import split_by_utf16_bytes

from .consts import EVENT_SOURCE_TWITCH
from .enums import MessageDeliveryMode, MessageRateMode

if TYPE_CHECKING:
    from twitchAPI.twitch import Twitch

    from bot.integrations.twitch.models import ConnectedChat

    from .event_sources import TwitchEventSource

LOGGER = logging.getLogger(__name__)


def _format_text(text: str, *, multiline: bool = False, max_length: int = 500):
    if not multiline:
        text = " - ".join(text.splitlines())
    split_text = [text]
    if max_length > 0:
        split_text = split_by_utf16_bytes(text, max_length)
    return split_text


class TwitchService(BaseMessageService):
    """Service for twitch related stuff."""

    def __init__(self, twitch_event_source: TwitchEventSource) -> None:
        super().__init__(EVENT_SOURCE_TWITCH)
        self.event_source: TwitchEventSource = twitch_event_source
        self.twitches: dict[str, Twitch] = {}
        self._channel_bucket: AsyncRateLimiter = AsyncRateLimiter(1, 1.05)
        self._standard_user_bucket: AsyncRateLimiter = AsyncRateLimiter(20, 30.05)
        self._upgraded_user_bucket: AsyncRateLimiter = AsyncRateLimiter(100, 30.05)
        self._last_messages: defaultdict[str, str] = defaultdict(str)
        self._channel_bucket_lock: defaultdict[str, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )

    def get_twitch(self, channel_id: str):
        """Get an authenticated Twitch instance."""
        return self.twitches.get(channel_id)

    async def _chat_rate_limit(self, channel_id: str, settings: ConnectedChat):
        async with self._channel_bucket_lock[channel_id]:
            if settings.message_rate == MessageRateMode.STANDARD:
                await self._channel_bucket.acquire(channel_id)
                await self._standard_user_bucket.acquire()
            elif settings.message_rate == MessageRateMode.UPGRADED:
                self._channel_bucket.add(channel_id)
                self._standard_user_bucket.add()
                await self._upgraded_user_bucket.acquire()

    async def _send_irc(self, channel_id: str, text: str, *, reply_id: str | None = None):
        settings = self.event_source.connected_chats[channel_id]
        if not self.event_source.twitch_chat:
            msg = "IRC Twitch chat has not been initialized"
            raise RuntimeError(msg)
        await self._chat_rate_limit(channel_id, settings)
        if (
            settings.message_rate == MessageRateMode.STANDARD
            and self._last_messages[channel_id] == text
        ):
            text += " ͏"
        await self.event_source.twitch_chat.send_message(
            settings.channel_name, text, reply_id
        )
        self._last_messages[channel_id] = text
        return MessageSendResult(is_sent=True, reply_id=None)

    async def _send_http(
        self, channel_id: str, text: str, *, reply_id: str | None = None
    ) -> MessageSendResult:
        twitch = self.get_twitch(channel_id)
        # twitch = self.event_source.bot_twitch
        if not twitch:
            msg = f"Channel id {channel_id} has not been authorized"
            raise ValueError(msg)
        settings = self.event_source.connected_chats[channel_id]
        await self._chat_rate_limit(channel_id, settings)
        if (
            settings.message_rate == MessageRateMode.STANDARD
            and self._last_messages[channel_id] == text
        ):
            text += " ͏"
        max_retries = 2
        result = SendMessageResponse()  # satisfy type checker
        while max_retries > 0:
            try:
                result = await twitch.send_chat_message(
                    channel_id, self.event_source.bot_user_id, text, reply_id
                )
            except KeyError:  # funny twitchapi bug when rate limited
                LOGGER.debug("possibly rate limited, retrying")
                max_retries -= 1
                if max_retries == 0:
                    raise
            else:
                break
        if result.is_sent:
            self._last_messages[channel_id] = text
        elif (
            not result.is_sent
            and result.drop_reason
            and result.drop_reason.code == "msg_duplicate"
        ):
            self._last_messages[channel_id] = text
            return await self._send_http(channel_id, text, reply_id=reply_id)
        return MessageSendResult(is_sent=result.is_sent, reply_id=result.message_id)

    @override
    async def send_message(
        self,
        text: str,
        channel_id: str,
        *,
        me: bool = False,
        reply_id: str | None = None,
        reply_username: str = "",
        use_http: bool | None = None,
        **kwargs: Any,  # pyright: ignore[reportExplicitAny, reportAny]
    ):
        """Send a chat message."""
        max_len = 500
        settings = self.event_source.connected_chats[channel_id]

        if settings.message_rate == MessageRateMode.STANDARD:
            # make room for extra chars
            max_len -= 2

        if me:
            max_len -= 4

        if reply_id is not None:
            max_len -= len(reply_username) + 2

        if channel_id not in self.event_source.connected_chats:
            msg = f"Not connected to chat room of {channel_id}"
            raise ValueError(msg)
        if use_http is None:
            use_http = settings.message_delivery_mode == MessageDeliveryMode.HELIX

        methods = []
        if use_http:
            # methods = [self._send_http, self._send_irc]
            methods = [self._send_http]
        else:
            methods = [self._send_irc, self._send_http]

        results: list[MessageSendResult] = []
        for sub_text in _format_text(text, max_length=max_len):
            for m in methods:
                try:
                    result = await m(channel_id, sub_text, reply_id=reply_id)
                except Exception:  # noqa: BLE001
                    LOGGER.warning("Failed to send message", exc_info=True)
                    continue
                else:
                    results.append(result)
                    break

        return MessageSendResult(is_sent=all(x.is_sent for x in results))

    @override
    async def teardown(self):
        for t in self.twitches.values():
            await t.close()
        return await super().teardown()
