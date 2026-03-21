from . import BaseConverter
from typing import TypedDict, Concatenate
from collections.abc import Callable, Awaitable
from bot.core.components import GlobalContext, BaseEvent

VerifierType = Callable[
    Concatenate[GlobalContext, BaseEvent, ...], bool | str | Awaitable[bool | str]
]


class ParameterConfig(TypedDict):
    aliases: list[str]
    greedy: bool
    hidden: bool
    help: str
    regex: str
    display_name: str
    converter: BaseConverter | type[BaseConverter] | None
