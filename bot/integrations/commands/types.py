from collections.abc import Awaitable, Callable
from typing import Concatenate

from bot.core.components import GlobalContext
from bot.integrations.commands.events import CommandEvent

VerifierType = Callable[
    Concatenate[GlobalContext, CommandEvent, ...], bool | str | Awaitable[bool | str]
]
