from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from bot.core.components import BaseEvent
from bot.core.enums import EventCategory, EventSource
from bot.integrations.chat_messages.events import MessageEvent

from . import CommandArgs


@dataclass(kw_only=True)
class CommandEvent(BaseEvent):
    categories: Collection[EventCategory] = (EventCategory.Command,)
    platform: EventSource = EventSource.Any
    data: Any | None = None
    message: MessageEvent
    prefix: str
    invoked_with: str
    parsed_args: CommandArgs
    parameters_text: str
    specified_parameters: set[str] = field(default_factory=set)
