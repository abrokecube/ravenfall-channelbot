from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from bot.core import EVENT_CATEGORY_GENERIC
from bot.core.components import BaseEvent

if TYPE_CHECKING:
    from collections.abc import Collection


@dataclass(kw_only=True)
class ProcessSpawnEvent(BaseEvent):
    """Fired when a watched process is spawned."""

    categories: Collection[str] = (EVENT_CATEGORY_GENERIC,)
    platform: str = "system"


@dataclass(kw_only=True)
class ProcessKillEvent(BaseEvent):
    """Fired when a watched process is killed."""

    categories: Collection[str] = (EVENT_CATEGORY_GENERIC,)
    platform: str = "system"
