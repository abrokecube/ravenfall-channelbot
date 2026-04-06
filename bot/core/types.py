from collections.abc import Awaitable, Callable
from typing import Concatenate

from .components import BaseEvent, GlobalContext

type ListenerFuncType = Callable[
    Concatenate[GlobalContext, BaseEvent, ...], None | Awaitable[None]
]
type EventProcessor[T: BaseEvent] = Callable[
    [GlobalContext, T], None | T | Awaitable[None | T]
]
type EventProcessorCallback[T: BaseEvent] = Callable[[T], Awaitable[None]] | None
