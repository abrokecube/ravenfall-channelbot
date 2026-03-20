from bot.integrations.chat_messages import MessageEvent, ChatRoomCapabilities
from bot.core.enums import EventSource, EventCategory 
from utils.strutils import split_by_utf16_bytes
from typing import TYPE_CHECKING, Any, override, cast
from collections.abc import Collection
from dataclasses import dataclass
from twitchAPI.type import TwitchResourceNotFound
from twitchAPI.type import CustomRewardRedemptionStatus
from .enums import TwitchCustomRewardRedemptionStatus
import logging

LOGGER = logging.getLogger(__name__)

def filter_text(context: MessageEvent, text: str, *, max_length: int | None = None):
    if not context.room_capabilities.multiline:
        text = " - ".join(text.splitlines())
    split_text = [text]
    char_limit = max_length or context.room_capabilities.max_message_length
    if char_limit > 0:
        split_text = split_by_utf16_bytes(text, char_limit)
    return split_text

if TYPE_CHECKING:
    from twitchAPI.object.eventsub import ChannelPointsCustomRewardRedemptionData
    from twitchAPI.twitch import Twitch
    from twitchAPI.chat import ChatMessage as TwitchChatMessage
    from twitchAPI.chat import Chat as TwitchChat
    
@dataclass(kw_only=True)
class TwitchMessageEvent(MessageEvent):
    platform: EventSource = EventSource.Twitch
    bot_twitch: Twitch
    channel_twitch: Twitch
    twitch_chat: TwitchChat
    data: TwitchChatMessage
    room_capabilities: ChatRoomCapabilities = ChatRoomCapabilities(
        multiline=False,
        max_message_length=500
    )

    async def _send_irc(self, text: str, *, reply_id: str | None = None):
        await self.twitch_chat.send_message(self.room_name, text, reply_id)
        
    async def _send_http(self, text: str, *, reply_id: str | None = None):
        _ = await self.channel_twitch.send_chat_message(
            self.room_id, self.bot_user_id, text, reply_id
        )
    async def _send(self, text: str, *, use_http: bool = True, reply_id: str | None = None):
        methods = []
        if use_http:
            methods = [self._send_http, self._send_irc]
        else:
            methods = [self._send_irc, self._send_http]
        for m in methods:
            try:
                await m(text, reply_id=reply_id)
                break
            except Exception:
                LOGGER.warning("Failed to send message", exc_info=True)
                continue

    @override
    async def send(self, text: str, *, me: bool = False, use_http: bool = True, reply_id: str | None = None, **kwargs: Any):
        char_limit = self.room_capabilities.max_message_length
        if me:
            char_limit -= 4
        for text_ in filter_text(self, text, max_length=char_limit):
            if me:
                text_ = f"/me {text_}"
            await self._send(text_, use_http=use_http, reply_id=reply_id)

    @override
    async def reply(self, text: str, *, use_http: bool = True, **kwargs: Any):
        char_limit = self.room_capabilities.max_message_length - len(self.author_login) - 2
        for text_ in filter_text(self, text, max_length=char_limit):
            await self._send(text_, use_http=use_http, reply_id=self.id)

@dataclass(kw_only=True)
class TwitchRedemptionEvent(TwitchMessageEvent):
    categories: Collection[EventCategory] = (EventCategory.Generic,)
    data: ChannelPointsCustomRewardRedemptionData  # pyright: ignore[reportIncompatibleVariableOverride]
    redeem_name: str
    redeem_id: str
    redeem_cost: str
    
    async def update_status(self, status: CustomRewardRedemptionStatus | TwitchCustomRewardRedemptionStatus):
        if self.data.status == "unfulfilled":
            try:
                _ = await self.channel_twitch.update_redemption_status(
                    self.data.broadcaster_user_id,
                    self.data.reward.id,
                    self.data.id,
                    cast("CustomRewardRedemptionStatus", status)
                )
            except TwitchResourceNotFound:
                LOGGER.warning(f"Redemption resource was already used ({self.redeem_name}: {self.redeem_id})")
        else:
            # logger.warning(f"Redemption is not in the UNFULFILLED state (current: {self.redemption.status})")
            pass
    
    async def fulfill(self):
        await self.update_status(TwitchCustomRewardRedemptionStatus.FULFILLED)
    
    async def cancel(self):
        await self.update_status(TwitchCustomRewardRedemptionStatus.CANCELED)
    
    @override
    async def reply(self, text: str, *, use_http: bool = True, **kwargs):
        return await super().send(f"@{self.author_login} {text}", use_http=use_http, **kwargs)
