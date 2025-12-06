from enum import Enum

class UserRole(Enum):
    BOT_OWNER = "bot_owner"
    ADMIN = "admin"
    MODERATOR = "moderator"
    SUBSCRIBER = "subscriber"
    USER = "user"

class OutputMessageType(Enum):
    SINGLE_LINE = "single_line"
    MULTI_LINE = "multiple_lines"

class Platform(Enum):
    TWITCH = "twitch"
    GENERIC = "generic"

class CustomRewardRedemptionStatus(Enum):
    UNFULFILLED = 'UNFULFILLED'
    FULFILLED = 'FULFILLED'
    CANCELED = 'CANCELED'

class ParameterKind(Enum):
    POSITIONAL_ONLY = 0
    POSITIONAL_OR_KEYWORD = 1
    VAR_POSITIONAL = 2
    KEYWORD_ONLY = 3
    VAR_KEYWORD = 4
