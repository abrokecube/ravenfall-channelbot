from __future__ import annotations
from typing import List, Dict, Any, TYPE_CHECKING, Set
from dataclasses import dataclass, field

from .enums import EventCategory, EventSource, UserRole, TwitchCustomRewardRedemptionStatus

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

    async def send(self, text: str, **kwargs):
        pass
    
    async def reply(self, text: str, **kwargs):
        pass

@dataclass(kw_only=True)
class CommandEvent:
    message: MessageEvent
    prefix: str
    invoked_with: str
    parameters_text: str

if TYPE_CHECKING:
    from twitchAPI.object.eventsub import ChannelPointsCustomRewardRedemptionData
    from twitchAPI.twitch import Twitch
    
@dataclass(kw_only=True)
class TwitchMessageEvent(MessageEvent):
    platform: EventSource = EventSource.Unknown
    bot_twitch: Twitch
    
@dataclass(kw_only=True)
class TwitchRedemptionEvent(MessageEvent):
    category: EventCategory = EventCategory.Generic
    platform: EventSource = EventSource.Twitch
    data: ChannelPointsCustomRewardRedemptionData
    channel_twitch: Twitch
    
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
