from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from bot.core.components import ListenerMetadata
from bot.core.decorators import _get_or_create_metadata_list
from bot.core.modals import MetaFilter
from bot.integrations.chat_messages import EVENT_CATEGORY_MESSAGE

from .dispatchers import CommandDispatcher

if TYPE_CHECKING:
    from . import BaseConverter
    from .types import ParameterConfig, VerifierType


@dataclasses.dataclass
class CommandMetadata:
    """Metadata for command listeners set by decorators."""

    name: str | None = None
    short_help_text: str | None = None
    help_text: str | None = None
    aliases: list[str] = dataclasses.field(default_factory=list)
    verifier: VerifierType | None = None
    hidden: bool = False
    priority: int = 0
    title: str | None = None


def _get_or_create_command_metadata_list(
    func: Callable[..., Any],
) -> list[CommandMetadata]:
    """Get or create the command metadata list on a function.

    Args:
        func: The function to get metadata from.

    Returns:
        The list of CommandMetadata objects.
    """
    metadata_list: list[CommandMetadata] | None = getattr(
        func, "_command_metadata", None
    )
    if metadata_list is None:
        metadata_list = []
        setattr(func, "_command_metadata", metadata_list)
    return metadata_list


def command[T: Callable[..., Any]](
    name: str | None = None,
    short_help_text: str | None = None,
    *,
    help_text: str | None = None,
    aliases: list[str] | None = None,
    verifier: VerifierType | None = None,
    hidden: bool = False,
    priority: int = 0,
    title: str | None = None,  # i forgot what this was for
) -> Callable[[T], T]:
    """Decorator to mark a function as a command listener."""
    if not aliases:
        aliases = []

    kwargs: dict[str, object] = {
        "name": name,
        "short_help_text": short_help_text,
        "help_text": help_text,
        "aliases": aliases,
        "verifier": verifier,
        "hidden": hidden,
        "priority": priority,
        "title": title,
    }

    def decorator(func: T):
        # Set core listener metadata
        listener_metadata = ListenerMetadata(
            dispatcher=CommandDispatcher,
            meta_filter=MetaFilter((EVENT_CATEGORY_MESSAGE,), True, [], False),
            init_params=kwargs,
            priority=priority,
        )
        _get_or_create_metadata_list(func).append(listener_metadata)

        # Set command-specific metadata
        command_metadata = CommandMetadata(
            name=name,
            short_help_text=short_help_text,
            help_text=help_text,
            aliases=aliases or [],
            verifier=verifier,
            hidden=hidden,
            priority=priority,
            title=title,
        )
        _get_or_create_command_metadata_list(func).append(command_metadata)

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
        # Keep _listener_command_params for now - listener will handle it
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
        # Update the last command metadata entry
        metadata_list = _get_or_create_command_metadata_list(func)
        if metadata_list:
            metadata_list[-1].verifier = verifier_func
        else:
            # Create a new metadata entry if none exists
            metadata = CommandMetadata(verifier=verifier_func)
            metadata_list.append(metadata)
        return func

    return decorator
