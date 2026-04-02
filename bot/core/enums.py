from __future__ import annotations
from enum import StrEnum


class BucketType(StrEnum):
    USER = "user"
    CHANNEL = "channel"
    GUILD = "guild"
    GLOBAL = "global"
