from __future__ import annotations
from bot.core.components import BaseEvent, GlobalContext
from dataclasses import dataclass
from collections.abc import Collection
from bot.core.enums import EventCategory, EventSource
from typing import NamedTuple, Any
from enum import StrEnum

@dataclass(kw_only=True)
class MessageEvent(BaseEvent):
    categories: Collection[EventCategory] = (EventCategory.Message, EventCategory.Generic)
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

class ChatRoomCapabilities(NamedTuple):
    multiline: bool
    max_message_length: int

class UserRole(StrEnum):
    BOT_ADMINISTRATOR = 'bot_admin'
    ADMINISTRATOR = 'admin'
    MODERATOR = 'moderator'
    USER = 'user'
    
    def level(self) -> int:
        return USER_ROLE_HIERARCHY_VALUES.get(self, 0)

class BucketType(StrEnum):
    USER = "user"
    CHANNEL = "channel"
    GUILD = "guild"
    GLOBAL = "global"

USER_ROLE_HIERARCHY: tuple[UserRole | tuple[UserRole, ...], ...] = (
    UserRole.BOT_ADMINISTRATOR,
    UserRole.ADMINISTRATOR,
    UserRole.MODERATOR,
    UserRole.USER
)
USER_ROLE_HIERARCHY_VALUES: dict[str, int] = {}
for i, u in enumerate(reversed(USER_ROLE_HIERARCHY)):
    if isinstance(u, tuple):
        for su in u:
            USER_ROLE_HIERARCHY_VALUES[su] = i
    else:
        USER_ROLE_HIERARCHY_VALUES[u] = i

class BaseCheck:
    """
    Base class for all checks.
    
    Checks are used to validate whether a user is allowed to execute a command.
    They can return True to indicate success, or a string with an error message.
    
    To display a custom error message when conversion fails,
    raise command_exceptions.CheckError in the convert method.
    """
    title: str | None = None
    short_help: str | None = None
    help: str | None = None
    hide_in_help: bool = False

    async def check(self, g_ctx: GlobalContext, event: BaseEvent) -> bool | str:  # pyright: ignore[reportUnusedParameter]
        raise NotImplementedError
