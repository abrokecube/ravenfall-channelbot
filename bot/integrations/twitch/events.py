from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from twitchAPI.type import TwitchResourceNotFound

from bot.core import EVENT_CATEGORY_GENERIC
from bot.integrations.chat_messages.events import MessageEvent
from bot.integrations.chat_messages.models import ChatRoomCapabilities
from bot.integrations.twitch import EVENT_SOURCE_TWITCH
from utils.strutils import split_by_utf16_bytes

from .enums import TwitchCustomRewardRedemptionStatus

if TYPE_CHECKING:
    from collections.abc import Collection

    from twitchAPI.chat import ChatMessage as TwitchChatMessage
    from twitchAPI.object.eventsub import (
        ChannelChatMessageData,
        ChannelPointsCustomRewardRedemptionData,
    )
    from twitchAPI.twitch import Twitch

    from bot.integrations.twitch.services import TwitchService

LOGGER = logging.getLogger(__name__)


def _filter_text(context: MessageEvent, text: str, *, max_length: int | None = None):
    if not context.room_capabilities.multiline:
        text = " - ".join(text.splitlines())
    split_text = [text]
    char_limit = max_length or context.room_capabilities.max_message_length
    if char_limit > 0:
        split_text = split_by_utf16_bytes(text, char_limit)
    return split_text


@dataclass(kw_only=True)
class TwitchIRCMessageEvent(MessageEvent):
    """Twitch chat message event."""

    platform: str = EVENT_SOURCE_TWITCH
    bot_twitch: Twitch
    channel_twitch: Twitch
    twitch_service: TwitchService
    data: TwitchChatMessage
    room_capabilities: ChatRoomCapabilities = ChatRoomCapabilities(  # noqa: RUF009
        multiline=False, max_message_length=500
    )

    @override
    async def send(
        self,
        text: str,
        *,
        me: bool = False,
        use_http: bool = True,
        reply_id: str | None = None,
        **kwargs: object,
    ):
        char_limit = self.room_capabilities.max_message_length
        if me:
            char_limit -= 4
        for text_ in _filter_text(self, text, max_length=char_limit):
            final_text = text_
            if me:
                final_text = f"/me {text_}"
            await self.twitch_service.send_chat_message(
                self.room_id, final_text, use_http=use_http, reply_id=reply_id
            )

    @override
    async def reply(self, text: str, *, use_http: bool = True, **kwargs: object):
        char_limit = (
            self.room_capabilities.max_message_length - len(self.author_login) - 2
        )
        for text_ in _filter_text(self, text, max_length=char_limit):
            await self.twitch_service.send_chat_message(
                self.room_id, text_, use_http=use_http, reply_id=self.id
            )


@dataclass(kw_only=True)
class TwitchEventSubMessageEvent(MessageEvent):
    """Twitch EventSub chat message event."""

    bot_twitch: Twitch
    channel_twitch: Twitch
    twitch_service: TwitchService
    data: ChannelChatMessageData
    room_capabilities: ChatRoomCapabilities = ChatRoomCapabilities(  # noqa: RUF009
        multiline=False, max_message_length=500
    )

    @override
    async def send(
        self,
        text: str,
        *,
        me: bool = False,
        use_http: bool = True,
        reply_id: str | None = None,
        **kwargs: object,
    ):
        char_limit = self.room_capabilities.max_message_length
        if me:
            char_limit -= 4
        for text_ in _filter_text(self, text, max_length=char_limit):
            final_text = text_
            if me:
                final_text = f"/me {text_}"
            await self.twitch_service.send_chat_message(
                self.room_id, final_text, use_http=use_http, reply_id=reply_id
            )

    @override
    async def reply(self, text: str, *, use_http: bool = True, **kwargs: object):
        char_limit = (
            self.room_capabilities.max_message_length - len(self.author_login) - 2
        )
        for text_ in _filter_text(self, text, max_length=char_limit):
            await self.twitch_service.send_chat_message(
                self.room_id, text_, use_http=use_http, reply_id=self.id
            )


@dataclass(kw_only=True)
class TwitchRedemptionEvent(TwitchIRCMessageEvent):
    """Twitch redemption event."""

    categories: Collection[str] = (EVENT_CATEGORY_GENERIC,)
    data: ChannelPointsCustomRewardRedemptionData  # pyright: ignore[reportIncompatibleVariableOverride]
    redeem_name: str
    redeem_id: str
    redeem_cost: int

    async def update_status(self, status: TwitchCustomRewardRedemptionStatus):
        """Update the status of the redemption.

        Only updates if the redemption is currently unfulfilled
        to avoid conflicts with other processes handling the same redemption.
        """
        if self.data.status == "unfulfilled":
            try:
                _ = await self.channel_twitch.update_redemption_status(
                    self.data.broadcaster_user_id,
                    self.data.reward.id,
                    self.data.id,
                    status,
                )
            except TwitchResourceNotFound:
                LOGGER.warning(
                    f"Redemption resource was already used "
                    f"({self.redeem_name}: {self.redeem_id})"
                )
        else:
            LOGGER.info(
                f"Redemption is not in the UNFULFILLED state "
                f"(current: {self.data.status})"
            )

    async def fulfill(self):
        """Fulfill the redemption."""
        await self.update_status(TwitchCustomRewardRedemptionStatus.FULFILLED)

    async def cancel(self):
        """Cancel the redemption."""
        await self.update_status(TwitchCustomRewardRedemptionStatus.CANCELED)

    @override
    async def send(
        self,
        text: str,
        *,
        me: bool = False,
        use_http: bool = True,
        reply_id: str | None = None,
        **kwargs: object,
    ):
        return await super().send(
            text, me=me, use_http=use_http, reply_id=reply_id, **kwargs
        )

    @override
    async def reply(self, text: str, *, use_http: bool = True, **kwargs: object):
        return await super().send(
            f"@{self.author_login} {text}",
            me=False,
            use_http=use_http,
            reply_id=None,
            **kwargs,
        )
