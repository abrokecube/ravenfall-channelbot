from enum import Enum, StrEnum, auto


class TwitchCustomRewardRedemptionStatus(Enum):
    UNFULFILLED = "UNFULFILLED"
    FULFILLED = "FULFILLED"
    CANCELED = "CANCELED"


class MessageDeliveryMode(StrEnum):
    IRC = "irc"
    HELIX = "helix"


class MessageReceiveMode(StrEnum):
    IRC = "irc"
    EVENTSUB = "eventsub"


class MessageRateMode(StrEnum):
    STANDARD = "standard"  # 1 message per second + 20 msgs per 30 seconds globally
    UPGRADED = (
        "upgraded"  # 100 msgs per 30 seconds globally (still adds to the 20/30 bucket)
    )


class EventSubTopic(Enum):
    CHANNEL_CHAT_MESSAGE = auto()
