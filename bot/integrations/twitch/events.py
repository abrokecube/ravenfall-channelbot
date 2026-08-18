from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from sqlalchemy.ext.asyncio import AsyncSession
from twitchAPI.type import TwitchResourceNotFound

from bot.core import EVENT_CATEGORY_GENERIC
from bot.core.components import BaseEvent
from bot.integrations.chat_messages.events import MessageEvent
from bot.integrations.chat_messages.models import ChatRoomCapabilities

from .consts import EVENT_SOURCE_TWITCH
from .enums import TwitchCustomRewardRedemptionStatus

if TYPE_CHECKING:
    from collections.abc import Collection

    from twitchAPI.chat import ChatMessage as TwitchChatMessage
    from twitchAPI.object.eventsub import (
        ChannelChatMessageData,
        ChannelPointsCustomRewardRedemptionData,
    )
    from twitchAPI.twitch import Twitch

    from bot.integrations.twitch import TwitchChannel
    from bot.integrations.twitch.services import TwitchService

LOGGER = logging.getLogger(__name__)


@dataclass(kw_only=True)
class TwitchEvent(BaseEvent):
    """Base class for events from Twitch."""

    platform: str = EVENT_SOURCE_TWITCH
    bot_twitch: Twitch
    channel_twitch: TwitchChannel
    twitch_service: TwitchService
    channel_id: str
    channel_login: str
    channel_display_name: str | None


@dataclass(kw_only=True)
class TwitchIRCMessageEvent(MessageEvent, TwitchEvent):
    """Twitch chat message event."""

    platform: str = EVENT_SOURCE_TWITCH
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
        final_text = text
        if me:
            final_text = f"/me {text}"
        __ = await self.twitch_service.send_message(
            final_text, self.room_id, use_http=use_http, reply_id=reply_id
        )

    @override
    async def reply(self, text: str, *, use_http: bool = True, **kwargs: object):
        __ = await self.twitch_service.send_message(
            text,
            self.room_id,
            use_http=use_http,
            reply_id=self.id,
            reply_username=self.author_login,
        )


@dataclass(kw_only=True)
class TwitchEventSubMessageEvent(MessageEvent, TwitchEvent):
    """Twitch EventSub chat message event."""

    platform: str = EVENT_SOURCE_TWITCH
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
        final_text = text
        if me:
            final_text = f"/me {text}"
        __ = await self.twitch_service.send_message(
            final_text, self.room_id, use_http=use_http, reply_id=reply_id
        )

    @override
    async def reply(self, text: str, *, use_http: bool = True, **kwargs: object):
        __ = await self.twitch_service.send_message(
            text,
            self.room_id,
            use_http=use_http,
            reply_id=self.id,
            reply_username=self.author_login,
        )


@dataclass(kw_only=True)
class TwitchRedemptionEvent(TwitchIRCMessageEvent):
    """Twitch redemption event."""

    categories: Collection[str] = (EVENT_CATEGORY_GENERIC,)
    data: ChannelPointsCustomRewardRedemptionData  # pyright: ignore[reportIncompatibleVariableOverride]
    redeem_name: str
    redeem_id: str
    redeem_cost: int
    internal_keys: tuple[str, ...]

    async def update_status(self, status: TwitchCustomRewardRedemptionStatus):
        """Update the status of the redemption.

        Only updates if the redemption is currently unfulfilled
        to avoid conflicts with other processes handling the same redemption.
        """
        if self.data.status == "unfulfilled":
            try:
                _ = await self.channel_twitch.update_redemption_status(
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

    async def save_internal_key(self, key: str, db_session: AsyncSession):
        """Save an internal key for this reward."""
        await self.channel_twitch.set_custom_reward_key(key, self.id, db_session)
