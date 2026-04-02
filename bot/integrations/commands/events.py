from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from bot.core import EVENT_SOURCE_ANY
from bot.core.components import BaseEvent
from bot.integrations.chat_messages.events import MessageEvent

from . import EVENT_CATEGORY_COMMAND, CommandArgs


@dataclass(kw_only=True)
class CommandEvent(BaseEvent):
    categories: Collection[str] = (EVENT_CATEGORY_COMMAND,)
    platform: str = EVENT_SOURCE_ANY
    data: Any | None = None
    message: MessageEvent
    prefix: str
    invoked_with: str
    parsed_args: CommandArgs
    parameters_text: str
    specified_parameters: set[str] = field(default_factory=set)
