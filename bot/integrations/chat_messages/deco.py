from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from bot.core.components import BaseDispatcher
from bot.core.decorators import lambda_filter_decorator
from bot.integrations.chat_messages.events import MessageEvent

from . import BaseCheck
from .checks import FunctionCheck

if TYPE_CHECKING:
    from .types import CheckFuncType


def on_message(match_fn: Callable[[MessageEvent], bool]):
    """Decorator for registering a function as a message event listener with a filter."""
    return lambda_filter_decorator(
        [MessageEvent], match_fn, dispatcher_type=BaseDispatcher
    )


def checks[T: Callable[..., Any]](
    *predicates: CheckFuncType | BaseCheck | type[BaseCheck],
) -> Callable[[T], T]:
    """Add checks to a command.

    Args:
        *predicates: One or more functions or Check classes/instances.

    """

    def decorator(func: T) -> T:
        if not hasattr(func, "_listener_command_checks"):
            setattr(func, "_listener_command_checks", [])

        processed_checks: list[BaseCheck] = []
        for p in predicates:
            if isinstance(p, type):
                if issubclass(p, BaseCheck):
                    processed_checks.append(p())
                else:
                    msg = (
                        f"Invalid check class {p.__name__}: "
                        "must be a subclass of BaseCheck"
                    )
                    raise TypeError(msg)
            elif isinstance(p, BaseCheck):
                processed_checks.append(p)
            else:
                processed_checks.append(FunctionCheck(p))

        cast("list[BaseCheck]", getattr(func, "_listener_command_checks")).extend(
            processed_checks
        )
        return func

    return decorator
