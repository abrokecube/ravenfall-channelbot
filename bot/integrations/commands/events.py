from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bot.core import EVENT_SOURCE_ANY
from bot.core.components import BaseEvent

if TYPE_CHECKING:
    from collections.abc import Collection

    from bot.integrations.chat_messages.events import MessageEvent

    from .classes import CommandArgs

EVENT_CATEGORY_COMMAND = "command"


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

    async def send(self, text: str, **kwargs: object):
        """Send a message in the same context as this event."""
        await self.message.send(text, **kwargs)

    async def reply(self, text: str, **kwargs: object):
        """Reply to this message."""
        await self.message.reply(text, **kwargs)
