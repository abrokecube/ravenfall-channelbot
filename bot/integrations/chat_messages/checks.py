from __future__ import annotations

from inspect import isawaitable
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from bot.core.components import BaseEvent, GlobalContext

    from .types import CheckFuncType


class BaseCheck:
    """Base class for all checks.

    Checks are used to validate whether a user is allowed to execute a command.
    They can return True to indicate success, or a string with an error message.

    To display a custom error message when conversion fails,
    raise command_exceptions.CheckError in the convert method.
    """

    title: str | None = None
    short_help: str | None = None
    help: str | None = None
    will_hide_command_from_help: bool = False

    async def check(self, g_ctx: GlobalContext, event: BaseEvent) -> bool | str:  # pyright: ignore[reportUnusedParameter]
        """Return True if check succeeds.

        Otherwise, raise an exception.
        """
        raise NotImplementedError


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
