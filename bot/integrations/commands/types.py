from bot.integrations.commands.events import CommandEvent
from . import BaseConverter
from typing import TypedDict, Concatenate
from collections.abc import Callable, Awaitable
from bot.core.components import GlobalContext

VerifierType = Callable[
    Concatenate[GlobalContext, CommandEvent, ...], bool | str | Awaitable[bool | str]
]


class ParameterConfig(TypedDict):
    aliases: list[str]
    greedy: bool
    hidden: bool
    help_: str
    regex: str
    display_name: str
    converter: BaseConverter | type[BaseConverter] | None
