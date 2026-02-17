from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Awaitable, Dict
if TYPE_CHECKING:
    from .event_manager import EventManager

from .enums import EventSource, UserRole
from .events import BaseEvent, MessageEvent, TwitchMessageEvent, TwitchRedemptionEvent
    
class BaseEventSource:
    def __init__(self):
        # self.event_manager: EventManager = None
        self.event_platform: EventSource = EventSource.Unknown
        self.event_processor_callback: Callable[[BaseEvent], None | Awaitable[None]] | None = None
        
    async def send_event(self, event: BaseEvent):
        if self.event_processor_callback:
            await self.event_processor_callback(event)

if TYPE_CHECKING:
    from twitchAPI.twitch import Twitch, TwitchUser
    from twitchAPI.chat import Chat, ChatMessage, ChatUser
    from twitchAPI.type import ChatEvent
    from twitchAPI.object.eventsub import ChannelPointsCustomRewardRedemptionAddEvent, ChannelPointsCustomRewardRedemptionData

class TwitchAPIEventSource(BaseEventSource):
    def __init__(self, chat: Chat, bot_twitch: Twitch, channel_twitches: Dict[str, Twitch], bot_user: TwitchUser):
        super().__init__()
        self.event_platform = EventSource.Twitch
        self.chat: Chat = chat
        self.bot_twitch: Twitch = bot_twitch
        self.bot_user: TwitchUser = bot_user
        self.channel_twitches: Dict[str, Twitch] = channel_twitches
    
    def register_events(self, chat: Chat):
        chat.register_event(ChatEvent.MESSAGE, self.on_message)
    
    def _get_user_roles(self, user: ChatUser, room_id: str):
        roles = set()
        if user.id == room_id:
            roles.add(UserRole.ADMINISTRATOR)
        if user.lead_mod:
            roles.add(UserRole.ADMINISTRATOR)
        if user.mod:
            roles.add(UserRole.MODERATOR)
        roles.add(UserRole.USER)
    
    async def on_message(self, message: ChatMessage):
        await self.send_event(TwitchMessageEvent(
            data=message,
            text=message.text,
            id=message.id,
            author_login=message.user.name,
            author_name=message.user.display_name,
            author_id=message.user.id,
            author_roles=self._get_user_roles(message.user, message.room.room_id),
            room_name=message.room.name,
            room_id=message.room.room_id,
            bot_user_id=self.bot_user.id,
            bot_user_login=self.bot_user.login,
            bot_user_name=self.bot_user.display_name,
            bot_twitch=self.bot_twitch,
            channel_twitch=self.channel_twitches.get(message.room.room_id),
            twitch_chat=message.chat
        ))

    async def on_channel_point_redemption(self, redemption: ChannelPointsCustomRewardRedemptionData):
        await self.send_event(TwitchRedemptionEvent(
            data=redemption,
            text=redemption.user_input,
            id=redemption.id,
            author_login=redemption.user_login,
            author_name=redemption.user_name,
            author_id=redemption.user_id,
            author_roles=set([UserRole.USER]),
            room_name=redemption.broadcaster_user_login,
            room_id=redemption.broadcaster_user_id,
            bot_user_id=self.bot_user.id,
            bot_user_login=self.bot_user.login,
            bot_user_name=self.bot_user.display_name,
            bot_twitch=self.bot_twitch,
            channel_twitch=self.channel_twitches.get(redemption.broadcaster_user_id),
            twitch_chat=self.chat
        ))
    