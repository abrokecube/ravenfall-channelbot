from collections.abc import Awaitable, Callable

from .events import RavenfallEvent

type RavenfallInstanceEventHook = Callable[[RavenfallEvent], Awaitable[None]]
