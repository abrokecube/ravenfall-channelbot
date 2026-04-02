from bot.integrations.chat_messages import EVENT_CATEGORY_MESSAGE
from .types import VerifierType, ParameterConfig
from typing import Any, cast
from collections.abc import Callable
from bot.core.modals import MetaFilter
from . import BaseConverter
from .dispatchers import CommandDispatcher


def command[T: Callable[..., Any]](
    name: str | None = None,
    short_help: str | None = None,
    help: str | None = None,
    aliases: list[str] | None = None,
    verifier: VerifierType | None = None,
    hidden: bool = False,
    **kwargs: Any,
) -> Callable[[T], T]:
    if not aliases:
        aliases = []

    def decorator(func: T):
        kwargs.update(
            {
                "name": name,
                "short_help": short_help,
                "help_": help,
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
    greedy: bool = False,
    hidden: bool = False,
    help_: str = "",
    regex: str = "",
    display_name: str = "",
    converter: BaseConverter | type[BaseConverter] | None = None,
) -> Callable[[T], T]:
    """Configure a command parameter.

    Args:
        name: The name of the parameter to configure.
        aliases: Optional alias or list of aliases for the parameter.
        greedy: If True, the parameter will consume all remaining input as a single string.
        hidden: If True, the parameter will be hidden from help documentation.
        help: Help text for the parameter.
        regex: Regex pattern to match for this parameter.

    """
    if not aliases:
        aliases = []
    if isinstance(aliases, str):
        aliases = [aliases]

    def decorator(func: T) -> T:
        if not hasattr(func, "_listener_command_params"):
            setattr(func, "_listener_command_params", {})
        command_params = cast(
            dict[str, ParameterConfig], getattr(func, "_listener_command_params")
        )
        command_params[name] = {
            "aliases": aliases,
            "greedy": greedy,
            "hidden": hidden,
            "help_": help_,
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

    The verifier function should accept (ctx, *args, **kwargs) matching the command's signature.
    It should return True (pass), False (fail), or a string (fail with message).
    """

    def decorator(func: T) -> T:
        setattr(func, "_listener_command_verifier", verifier_func)
        return func

    return decorator
