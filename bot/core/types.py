from typing import Concatenate, Callable
from collections.abc import Awaitable
from .components import GlobalContext, BaseEvent

type ListenerFuncType = Callable[
    Concatenate[GlobalContext, BaseEvent, ...], None | Awaitable[None]
]
type EventProcessor[T: BaseEvent] = Callable[
    [GlobalContext, T], None | T | Awaitable[None | T]
]
type EventProcessorCallback[T: BaseEvent] = Callable[[T], Awaitable[None]] | None
