from __future__ import annotations
from enum import IntEnum, auto, StrEnum, Enum

class EventCategory(IntEnum):
    Generic = auto()
    Message = auto()
    Command = auto()
    RavenBotMessage = auto()
    RavenfallMessage = auto()

class EventSource(IntEnum):
    Unknown = auto()
    Twitch = auto()
    RavenBot = auto()
    Ravenfall = auto()
    HTTPRequest = auto()

class Dispatcher(IntEnum):
    Base = auto()
    Generic = auto()
    Command = auto()

class BucketType(IntEnum):
    USER = auto()
    CHANNEL = auto()
    GUILD = auto()
    GLOBAL = auto()

class ParameterKind(IntEnum):
    POSITIONAL_ONLY = auto()
    POSITIONAL_OR_KEYWORD = auto()
    VAR_POSITIONAL = auto()
    KEYWORD_ONLY = auto()
    VAR_KEYWORD = auto()

class UserRole(StrEnum):
    BOT_ADMINISTRATOR = 'bot_admin'
    ADMINISTRATOR = 'admin'
    MODERATOR = 'moderator'
    USER = 'user'
    
    def level(self) -> int:
        return USER_ROLE_HIERARCHY_VALUES.get(self, 0)

USER_ROLE_HIERARCHY = (
    UserRole.BOT_ADMINISTRATOR,
    UserRole.ADMINISTRATOR,
    UserRole.MODERATOR,
    UserRole.USER
)
USER_ROLE_HIERARCHY_VALUES: dict[UserRole, int] = {}
for i, u in enumerate(reversed(USER_ROLE_HIERARCHY)):
    if isinstance(u, tuple):
        for su in u:
            USER_ROLE_HIERARCHY_VALUES[su] = i
    else:
        USER_ROLE_HIERARCHY_VALUES[u] = i

class TwitchCustomRewardRedemptionStatus(Enum):
    UNFULFILLED = 'UNFULFILLED'
    FULFILLED = 'FULFILLED'
    CANCELED = 'CANCELED'
