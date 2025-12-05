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
