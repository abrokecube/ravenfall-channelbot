from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, overload

from bot.core.components import BaseDispatcher
from bot.core.decorators import _get_or_create_metadata_list, lambda_filter_decorator
from bot.integrations.chat_messages.events import MessageEvent
from bot.integrations.chat_messages.models import ChatMessageMetadata

from . import BaseCheck
from .checks import FunctionCheck

if TYPE_CHECKING:
    from .types import CheckFuncType


@overload
def _get_chat_msg_metadata(
    func: Callable[..., object], *, raise_if_no_metadata: Literal[True] = True
) -> ChatMessageMetadata: ...
@overload
def _get_chat_msg_metadata(
    func: Callable[..., object], *, raise_if_no_metadata: Literal[False] = False
) -> ChatMessageMetadata | None: ...
def _get_chat_msg_metadata(
    func: Callable[..., object], *, raise_if_no_metadata: bool = True
) -> ChatMessageMetadata | None:
    listeners = _get_or_create_metadata_list(func)
    if not listeners:
        msg = (
            "No listener decorator. If there is one, make sure you place it "
            "below this decorator."
        )
        raise RuntimeError(msg)
    listener = listeners[-1]
    command_metadata = listener.init_kwargs.get("chat_message_metadata")
    if not isinstance(command_metadata, ChatMessageMetadata):
        if raise_if_no_metadata:
            msg = "Not a chat message listener"
            raise TypeError(msg)
        return None
    return command_metadata


def on_message(match_fn: Callable[[MessageEvent], object]):
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

        metadata = _get_chat_msg_metadata(func)
        metadata.checks.extend(processed_checks)
        return func

    return decorator
