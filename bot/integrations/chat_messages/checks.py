from __future__ import annotations

from inspect import isawaitable
from typing import TYPE_CHECKING, override

from . import BaseCheck

if TYPE_CHECKING:
    from bot.core.components import BaseEvent, GlobalContext

    from .types import CheckFuncType


class FunctionCheck(BaseCheck):
    """Check using a function that returns bool or str (failure message)."""

    def __init__(self, predicate: CheckFuncType):
        self.predicate: CheckFuncType = predicate
        self.title: str | None = predicate.__name__.replace("_", " ").title()
        self.help: str | None = getattr(predicate, "__doc__", "")

    @override
    async def check(self, g_ctx: GlobalContext, event: BaseEvent):
        result = self.predicate(event)
        if isawaitable(result):
            result = await result
        return result
