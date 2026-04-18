from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bot.core import EVENT_SOURCE_ANY
from bot.core.components import BaseEvent

from . import EVENT_CATEGORY_COMMAND

if TYPE_CHECKING:
    from collections.abc import Collection

    from bot.integrations.chat_messages.events import MessageEvent

    from . import CommandArgs


@dataclass(kw_only=True)
class CommandEvent(BaseEvent):
    """Event representing a command invocation."""

    categories: Collection[str] = (EVENT_CATEGORY_COMMAND,)
    platform: str = EVENT_SOURCE_ANY
    data: object | None = None
    message: MessageEvent
    prefix: str
    invoked_with: str
    parsed_args: CommandArgs
    parameters_text: str
    specified_parameters: set[str] = field(default_factory=set)
