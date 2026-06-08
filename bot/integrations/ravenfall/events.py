from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, override

from bot.clients import ravenfall_middleman as rm
from bot.core import EVENT_CATEGORY_GENERIC
from bot.core.components import BaseEvent

if TYPE_CHECKING:
    from collections.abc import Collection

    from bot.clients import ravenfall_query as rq
    from bot.integrations.ravenfall.event_sources import RavenfallInstance
    from bot.integrations.ravenfall.models import RavenfallFormattedMessage

    from . import enums
    from .matcher import Match

LOGGER = logging.getLogger(__name__)

EVENT_SOURCE_RAVENFALL = "ravenfall"


class MessageOrigin(Enum):
    """Middleman message origin."""

    STREAM = auto()
    PROCESSOR = auto()


@dataclass(kw_only=True)
class BaseMiddlemanMessage(BaseEvent):
    """Message from Ravenbot/Ravenfall from the middleman."""

    categories: Collection[str] = (EVENT_CATEGORY_GENERIC,)
    platform: str = EVENT_SOURCE_RAVENFALL

    data: Any  # pyright: ignore[reportExplicitAny]
    message: Any  # pyright: ignore[reportExplicitAny]
    orig_message: Any  # pyright: ignore[reportExplicitAny]
    ravenfall: RavenfallInstance
    is_msg_from_api: bool
    message_source: MessageOrigin

    def block(self) -> None:
        """Blocks the message from being sent to its destination."""


@dataclass(kw_only=True)
class RavenfallMessageEvent(BaseMiddlemanMessage):
    """Message from Ravenfall."""

    data: rm.RavenfallStreamMessage | rm.RavenfallProcessorMessage
    message: rm.RavenfallMessage | RavenfallFormattedMessage
    orig_message: (
        rm.RavenfallMessage | rm.FrozenRavenfallMessage | RavenfallFormattedMessage
    )
    message_match: Match | None

    @override
    def block(self):
        if isinstance(self.data, rm.RavenfallProcessorMessage):
            self.data.block()
        else:
            LOGGER.warning("RavenfallMessageEvent: cannot block a stream message")


@dataclass(kw_only=True)
class RavenBotMessageEvent(BaseMiddlemanMessage):
    """Message from RavenBot."""

    data: rm.RavenBotStreamMessage | rm.RavenBotProcessorMessage
    message: rm.RavenBotMessage
    orig_message: rm.RavenBotMessage | rm.FrozenRavenBotMessage

    @override
    def block(self):
        if isinstance(self.data, rm.RavenBotProcessorMessage):
            self.data.block()
        else:
            LOGGER.warning("RavenBotMessageEvent: cannot block a stream message")


@dataclass(kw_only=True)
class RavenNestEvent(BaseEvent):
    """Base class for Ravenfall events."""

    categories: Collection[str] = (EVENT_CATEGORY_GENERIC,)
    platform: str = EVENT_SOURCE_RAVENFALL


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
class RavenNestOnlineEvent(RavenNestEvent):
    """A connection to RavenNest was established."""

    data: None = None


@dataclass(kw_only=True)
class RavenNestOfflineEvent(RavenNestEvent):
    """A connection to RavenNest was lost."""

    data: None = None


@dataclass(kw_only=True)
class RavenNestUpdaterOnlineEvent(RavenNestEvent):
    """A connection to RavenNest was lost."""

    data: None = None


@dataclass(kw_only=True)
class RavenNestUpdaterOfflineEvent(RavenNestEvent):
    """A connection to RavenNest was lost."""

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
class DungeonReachedBossEvent(RavenfallEvent):
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
class TownLevelUpEvent(RavenfallEvent):
    """A town has leveled up."""

    data: rq.Village


@dataclass(kw_only=True)
class MultiplierChangedEvent(RavenfallEvent):
    """The EXP multiplier has changed."""

    data: rq.GameMultiplier
    change_type: enums.MultiplierChangeType


@dataclass(kw_only=True)
class ObservedPlayerChangedEvent(RavenfallEvent):
    """The currently observed player has changed."""

    player: rq.Player | None
    data: rq.Player | None


@dataclass(kw_only=True)
class PlayerJoinedEvent(RavenfallEvent):
    """A player has joined."""

    player: rq.Player
    data: rq.Player


@dataclass(kw_only=True)
class PlayerLeftEvent(RavenfallEvent):
    """A player has left."""

    player: rq.Player
    data: rq.Player
