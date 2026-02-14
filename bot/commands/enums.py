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

class TwitchCustomRewardRedemptionStatus(Enum):
    UNFULFILLED = 'UNFULFILLED'
    FULFILLED = 'FULFILLED'
    CANCELED = 'CANCELED'
