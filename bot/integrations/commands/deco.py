from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from bot.core.modals import MetaFilter
from bot.integrations.chat_messages import EVENT_CATEGORY_MESSAGE

from .dispatchers import CommandDispatcher

if TYPE_CHECKING:
    from . import BaseConverter
    from .types import ParameterConfig, VerifierType


def command[T: Callable[..., Any]](
    name: str | None = None,
    short_help_text: str | None = None,
    help_text: str | None = None,
    aliases: list[str] | None = None,
    verifier: VerifierType | None = None,
    *,
    hidden: bool = False,
    **kwargs: Any,  # pyright: ignore[reportAny, reportExplicitAny]
) -> Callable[[T], T]:
    """Decorator to mark a function as a command listener."""
    if not aliases:
        aliases = []

    def decorator(func: T):
        kwargs.update(
            {
                "name": name,
                "short_help": short_help_text,
                "help_": help_text,
                "aliases": aliases,
                "verifier": verifier,
                "hidden": hidden,
            }
        )
        setattr(func, "_listener_init_params", kwargs)
        setattr(
            func,
            "_listener_meta_filter",
            MetaFilter((EVENT_CATEGORY_MESSAGE,), True, [], False),
        )
        setattr(func, "_listener_dispatcher", CommandDispatcher)
        return func

    return decorator


def parameter[T: Callable[..., Any]](
    name: str,
    aliases: str | list[str] | None = None,
    description: str = "",
    regex: str = "",
    display_name: str = "",
    converter: BaseConverter | type[BaseConverter] | None = None,
    *,
    greedy: bool = False,
    hidden: bool = False,
) -> Callable[[T], T]:
    """Configure a command parameter.

    Args:
        name: The name of the parameter to configure.
        aliases: Optional alias or list of aliases for the parameter.
        description: Help text for the parameter.
        help: Help text for the parameter.
        regex: Regex pattern to match for this parameter.
        display_name: Optional display name for the parameter in help text.
        converter: Optional converter to use for this parameter.
        Can be a BaseConverter instance or a subclass of BaseConverter.
        greedy: If True, the parameter will consume all remaining input
        as a single string.
        hidden: If True, the parameter will be hidden from help documentation.
    """
    if not aliases:
        aliases = []
    if isinstance(aliases, str):
        aliases = [aliases]

    def decorator(func: T) -> T:
        if not hasattr(func, "_listener_command_params"):
            setattr(func, "_listener_command_params", {})
        command_params = cast(
            "dict[str, ParameterConfig]", getattr(func, "_listener_command_params")
        )
        command_params[name] = {
            "aliases": aliases,
            "greedy": greedy,
            "hidden": hidden,
            "help_": description,
            "regex": regex,
            "display_name": display_name,
            "converter": converter,
        }
        return func

    return decorator


def verification[T: Callable[..., Any]](
    verifier_func: VerifierType,
) -> Callable[[T], T]:
    """Add a verification function to a command.

    The verifier function should accept (ctx, *args, **kwargs)
    matching the command's signature.
    It should return True (pass), False (fail), or a string (fail with message).
    """

    def decorator(func: T) -> T:
        setattr(func, "_listener_command_verifier", verifier_func)
        return func

    return decorator
