from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bot.clients import ravenfall_query as rq
from bot.core import EVENT_CATEGORY_GENERIC
from bot.core.components import BaseEvent

from . import enums

EVENT_SOURCE_RAVENFALL = "ravenfall"

if TYPE_CHECKING:
    from bot.integrations.ravenfall.event_sources import RavenfallInstance


@dataclass(kw_only=True)
class RavenfallEvent(BaseEvent):
    """Base class for Ravenfall events."""

    categories: Collection[str] = (EVENT_CATEGORY_GENERIC,)
    platform: str = EVENT_SOURCE_RAVENFALL
    ravenfall: RavenfallInstance


@dataclass(kw_only=True)
class RavenfallOnlineEvent(RavenfallEvent):
    """A connection to Ravenfall was established."""

    data: None = None


@dataclass(kw_only=True)
class RavenfallOfflineEvent(RavenfallEvent):
    """A connection to Ravenfall was lost."""

    data: None = None


@dataclass(kw_only=True)
class RaidStartedEvent(RavenfallEvent):
    """A raid has spawned."""

    data: rq.Raid
    reason: enums.RaidStartReason


@dataclass(kw_only=True)
class RaidEndedEvent(RavenfallEvent):
    """A raid has ended."""

    data: None = None
    reason: enums.RaidEndReason


@dataclass(kw_only=True)
class DungeonSpawnedEvent(RavenfallEvent):
    """A dungeon has spawned."""

    data: None = None
    reason: enums.DungeonStartReason
    name: str


@dataclass(kw_only=True)
class DungeonPreparedEvent(RavenfallEvent):
    """A dungeon has been loaded."""

    data: None = None
    name: str
    joined_player_count: int
    enemy_count: int


@dataclass(kw_only=True)
class DungeonStartedEvent(RavenfallEvent):
    """A dungeon has started."""

    data: rq.Dungeon


@dataclass(kw_only=True)
class DungeonEndedEvent(RavenfallEvent):
    """A dungeon has ended."""

    data: None = None
    reason: enums.DungeonEndReason


@dataclass(kw_only=True)
class RavenfallReadyEvent(RavenfallEvent):
    """Ravenfall has finished the startup process."""

    data: None = None


@dataclass(kw_only=True)
class ObservedPlayerChangedEvent(RavenfallEvent):
    """The currently observed player has changed."""

    player: rq.Player | None
    data: rq.Player | None
