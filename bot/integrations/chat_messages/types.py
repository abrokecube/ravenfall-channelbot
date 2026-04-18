from collections.abc import Awaitable, Callable

from bot.core.components import BaseEvent

CheckFuncType = Callable[[BaseEvent], bool | Awaitable[bool]]
