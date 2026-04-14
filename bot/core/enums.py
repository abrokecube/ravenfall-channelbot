from __future__ import annotations

from enum import StrEnum


class BucketType(StrEnum):
    """Enum for rate limiting bucket types."""

    USER = "user"
    CHANNEL = "channel"
    GUILD = "guild"
    GLOBAL = "global"
