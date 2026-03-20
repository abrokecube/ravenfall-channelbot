from bot.core.components import BaseEvent
from typing import Callable
from collections.abc import Awaitable

CheckFuncType = Callable[[BaseEvent], bool | Awaitable[bool]]
