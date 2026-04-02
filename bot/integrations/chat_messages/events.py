from bot.core import EVENT_CATEGORY_GENERIC, EVENT_SOURCE_ANY
from bot.core.components import BaseEvent
from dataclasses import dataclass
from collections.abc import Collection

from bot.integrations.chat_messages import EVENT_CATEGORY_MESSAGE
from .enums import UserRole
from .models import ChatRoomCapabilities
from typing import Any


@dataclass(kw_only=True)
class MessageEvent(BaseEvent):
    categories: Collection[str] = (
        EVENT_CATEGORY_MESSAGE,
        EVENT_CATEGORY_GENERIC,
    )
    platform: str = EVENT_SOURCE_ANY
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
