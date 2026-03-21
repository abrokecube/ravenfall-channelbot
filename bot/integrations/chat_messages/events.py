from bot.core.components import BaseEvent
from bot.core.enums import EventCategory, EventSource
from dataclasses import dataclass
from collections.abc import Collection
from .enums import UserRole
from .models import ChatRoomCapabilities
from typing import Any


@dataclass(kw_only=True)
class MessageEvent(BaseEvent):
    categories: Collection[EventCategory] = (
        EventCategory.Message,
        EventCategory.Generic,
    )
    platform: EventSource = EventSource.Any
    text: str
    id: str
    author_login: str
    author_name: str
    author_id: str
    author_roles: set[UserRole]
    room_name: str
    room_id: str
    room_capabilities: ChatRoomCapabilities
    bot_user_login: str
    bot_user_name: str
    bot_user_id: str

    async def send(self, text: str, **kwargs: Any):
        pass

    async def reply(self, text: str, **kwargs: Any):
        pass
