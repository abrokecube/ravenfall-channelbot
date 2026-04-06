from __future__ import annotations
from bot.core.components import BaseEvent, GlobalContext

EVENT_CATEGORY_MESSAGE = "message"


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
        raise NotImplementedError
