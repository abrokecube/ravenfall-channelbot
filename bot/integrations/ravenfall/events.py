from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from bot.core.components import BaseEvent

if TYPE_CHECKING:
    from bot.integrations.ravenfall.event_sources import RavenfallInstance


@dataclass(kw_only=True)
class RavenfallEvent(BaseEvent):
    """Base class for Ravenfall events."""

    ravenfall: RavenfallInstance
