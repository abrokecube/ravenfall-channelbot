from typing import Concatenate, Callable
from collections.abc import Awaitable
from .components import GlobalContext, BaseEvent

ListenerFuncType = Callable[
    Concatenate[GlobalContext, BaseEvent, ...], None | Awaitable[None]
]
EventProcessor = Callable[
    [GlobalContext, BaseEvent], None | BaseEvent | Awaitable[None | BaseEvent]
]
EventProcessorCallback = Callable[[BaseEvent], Awaitable[None]] | None
