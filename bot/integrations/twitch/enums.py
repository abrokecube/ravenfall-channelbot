from enum import Enum


class TwitchCustomRewardRedemptionStatus(Enum):
    UNFULFILLED = "UNFULFILLED"
    FULFILLED = "FULFILLED"
    CANCELED = "CANCELED"
