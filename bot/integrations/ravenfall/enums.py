from enum import Enum, auto


class RFChannelEvent(Enum):
    """Dungeon/Raid events."""

    NONE = 0
    DUNGEON = 1
    RAID = 2


class RFChannelSubEvent(Enum):
    """Dungeon/raid sub-events."""

    NONE = 0
    DUNGEON_PREPARE = 1
    DUNGEON_READY = 2
    DUNGEON_STARTED = 3
    DUNGEON_BOSS = 4
    RAID = 5


class RaidStartReason(Enum):
    """Raid start reason."""

    UNKNOWN = auto()
    RANDOM_EVENT = auto()
    SCROLL_USED = auto()


class RaidEndReason(Enum):
    """Raid end reason."""

    UNKNOWN = auto()
    BOSS_DEFEATED = auto()
    TIME_EXPIRED = auto()
    # CANCELLED = auto()


class DungeonStartReason(Enum):
    """Dungeon start reason."""

    UNKNOWN = auto()
    RANDOM_EVENT = auto()
    SCROLL_USED = auto()


class DungeonEndReason(Enum):
    """Dungeon end reason."""

    UNKNOWN = auto()
    BOSS_DEFEATED = auto()
    PLAYERS_DEFEATED = auto()
    # CANCELLED = auto()
