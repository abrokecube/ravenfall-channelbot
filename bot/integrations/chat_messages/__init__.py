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

class Cooldown:
    def __init__(self, rate: int, per: float, bucket: str | list[str] = BucketType.USER):
        self.rate: int = rate
        self.per: float = per

        if not isinstance(bucket, list):
            bucket = [bucket]
        self.bucket: list[str] = bucket
        self._windows: dict[str, list[float]] = {}
    
    def _get_bucket_key(self, event: BaseEvent) -> str:
        if hasattr(event, "get_bucket_key"):
            keys: list[str] = [str(event.get_bucket_key(t)) for t in self.bucket]
            return ":".join(keys)
        return ""

    def get_retry_after(self, event: BaseEvent) -> float:
        import time
        now = time.time()
        key = self._get_bucket_key(event)
        
        if key not in self._windows:
            return 0.0
            
        window = self._windows[key]
        # Remove expired timestamps
        window = [t for t in window if now - t < self.per]
        self._windows[key] = window
        
        if len(window) < self.rate:
            return 0.0
            
        return self.per - (now - window[0])

    def update_rate_limit(self, event: BaseEvent):
        import time
        now = time.time()
        key = self._get_bucket_key(event)
        
        if key not in self._windows:
            self._windows[key] = []
            
        window = self._windows[key]
        # Remove expired timestamps
        window = [t for t in window if now - t < self.per]
        window.append(now)
        self._windows[key] = window

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
