from typing import Concatenate, Callable, TypedDict, Any
from collections.abc import Awaitable
from .global_context import GlobalContext
from .events import BaseEvent
from .converters import BaseConverter

ListenerFuncType = Callable[Concatenate[GlobalContext, BaseEvent, ...], None | Awaitable[None]]
VerifierType = Callable[Concatenate[GlobalContext, BaseEvent, ...], bool | str | Awaitable[bool | str]]
CheckFuncType = Callable[[BaseEvent], bool | Awaitable[bool]]
EventProcessor = Callable[[GlobalContext, BaseEvent], None | BaseEvent | Awaitable[None | BaseEvent]]

class ParameterConfig(TypedDict):
    aliases: list[str]
    greedy: bool
    hidden: bool
    help: str
    regex: str
    display_name: str
    converter: BaseConverter | type[BaseConverter]
