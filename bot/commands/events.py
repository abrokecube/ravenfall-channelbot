from __future__ import annotations
from typing import List, Dict, Any, TYPE_CHECKING, Set
from dataclasses import dataclass, field

from .enums import EventCategory, EventSource, UserRole, TwitchCustomRewardRedemptionStatus
from .modals import ChatRoomCapabilities
from utils.strutils import split_by_utf16_bytes

@dataclass(kw_only=True)
class BaseEvent:
    category: EventCategory
    platform: EventSource = EventSource.Unknown
    data: Dict

@dataclass(kw_only=True)
class MessageEvent(BaseEvent):
    category: EventCategory = EventCategory.Message
    platform: EventSource = EventSource.Unknown
    text: str
    id: str
    author_login: str
    author_name: str
    author_id: str
    author_roles: Set[UserRole]
    room_name: str
    room_id: str
    room_capabilities: ChatRoomCapabilities
    bot_user_login: str
    bot_user_name: str
    bot_user_id: str
    

    async def send(self, text: str, **kwargs):
        pass
    
    async def reply(self, text: str, **kwargs):
        pass

def filter_text(context: MessageEvent, text: str, *, max_length: int | None = None):
    if not context.room_capabilities.multiline:
        text = " - ".join(text.splitlines())
    split_text = [text]
    char_limit = max_length or context.room_capabilities.max_message_length
    if char_limit is not None and char_limit > 0:
        split_text = split_by_utf16_bytes(text, char_limit)
    return split_text

if TYPE_CHECKING:
    from .command_parser import CommandArgs

@dataclass(kw_only=True)
class CommandEvent:
    message: MessageEvent
    prefix: str
    invoked_with: str
    parsed_args: CommandArgs
    parameters_text: str

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

    async def send(self, text: str, *, me: bool = False, use_http: bool = True):
        char_limit = self.room_capabilities.max_message_length
        if me:
            char_limit -= 4
        for text_ in filter_text(self, text, max_length=char_limit):
            if me:
                text_ = f"/me {text_}"
            if not use_http:
                await self.twitch_chat.send_message(self.room_name, text_)
            else:
                await self.channel_twitch.send_chat_message(
                    self.room_id, self.bot_user_id, text_
                )

    async def reply(self, text: str, *, use_http: bool = True):
        char_limit = self.room_capabilities.max_message_length - len(self.author_login) - 2
        for text_ in filter_text(self, text, max_length=char_limit):
            if not use_http:
                await self.twitch_chat.send_message(self.room_name, text_, self.id)
            else:
                await self.channel_twitch.send_chat_message(
                    self.room_id, self.bot_user_id, text_, self.id
                )

@dataclass(kw_only=True)
class TwitchRedemptionEvent(TwitchMessageEvent):
    category: EventCategory = EventCategory.Generic
    data: ChannelPointsCustomRewardRedemptionData

    async def update_status(self, status: TwitchCustomRewardRedemptionStatus):
        if self.data.status == "unfulfilled":
            await self.channel_twitch.update_redemption_status(
                self.data.broadcaster_user_id,
                self.data.reward.id,
                self.data.id,
                status
            )
        else:
            # logger.warning(f"Redemption is not in the UNFULFILLED state (current: {self.redemption.status})")
            pass
    
    async def fulfill(self):
        await self.update_status(TwitchCustomRewardRedemptionStatus.FULFILLED)
    
    async def cancel(self):
        await self.update_status(TwitchCustomRewardRedemptionStatus.CANCELED)
        
    async def reply(self, text, *, use_http = True):
        await self.send(f"@{self.author_login} ")
