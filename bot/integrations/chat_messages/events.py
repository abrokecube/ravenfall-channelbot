from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bot.core import EVENT_CATEGORY_GENERIC, EVENT_SOURCE_ANY
from bot.core.components import BaseEvent

if TYPE_CHECKING:
    from collections.abc import Collection

    from .enums import UserRole
    from .models import ChatRoomCapabilities

EVENT_CATEGORY_MESSAGE = "message"


@dataclass(kw_only=True)
class MessageEvent(BaseEvent):
    """Event representing a chat message."""

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

    async def send(self, text: str, **kwargs: Any):  # pyright: ignore[reportUnusedParameter, reportAny, reportExplicitAny]
        """Send a message in the same context as this event."""

    async def reply(self, text: str, **kwargs: Any):  # pyright: ignore[reportUnusedParameter, reportAny, reportExplicitAny]
        """Reply to this message."""
