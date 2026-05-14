from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from bot.core.components import ListenerMetadata
from bot.core.decorators import _get_or_create_metadata_list
from bot.core.modals import MetaFilter
from bot.integrations.chat_messages import EVENT_CATEGORY_MESSAGE, ChatMessageMetadata

from .classes import _EMPTY
from .dispatchers import CommandDispatcher
from .models import CommandMetadata, ParameterConfig

if TYPE_CHECKING:
    from . import BaseConverter
    from .types import VerifierType


def _get_command_metadata(func: Callable[..., object]):
    listeners = _get_or_create_metadata_list(func)
    if not listeners:
        msg = "No listener decorator"
        raise RuntimeError(msg)
    listener = listeners[-1]
    command_metadata = listener.init_kwargs.get("command_dispatcher_metadata")
    if not isinstance(command_metadata, CommandMetadata):
        msg = "Not a command listener"
        raise TypeError(msg)
    return command_metadata


def command[T: Callable[..., object]](
    name: str | None = None,
    short_help_text: str | None = None,
    *,
    help_text: str | None = None,
    aliases: list[str] | None = None,
    verifier: VerifierType | None = None,
    hidden: bool = False,
    title: str | None = None,  # i forgot what this was for
) -> Callable[[T], T]:
    """Decorator to mark a function as a command listener."""
    if not aliases:
        aliases = []

    kwargs: dict[str, object] = {
        "command_dispatcher_metadata": CommandMetadata(
            name=name,
            short_help_text=short_help_text,
            help_text=help_text,
            verifier=verifier,
            hidden=hidden,
            title=title,
            aliases=aliases,
        ),
        "chat_message_metadata": ChatMessageMetadata(),
    }

    def decorator(func: T):
        # Set core listener metadata
        listener_metadata = ListenerMetadata(
            dispatcher=CommandDispatcher,
            meta_filter=MetaFilter((EVENT_CATEGORY_MESSAGE,), True, [], False),
            init_kwargs=kwargs,
            priority=0,
        )
        _get_or_create_metadata_list(func).append(listener_metadata)

        return func

    return decorator


def parameter[T: Callable[..., object]](
    name: str,
    aliases: str | list[str] | None = None,
    description: str = "",
    regex: str = "",
    display_name: str = "",
    converter: BaseConverter | type[BaseConverter] | None = None,
    default: object = _EMPTY,
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
        metadata = _get_command_metadata(func)
        metadata.parameters[name] = ParameterConfig(
            aliases=aliases,
            greedy=greedy,
            hidden=hidden,
            help_text=description,
            regex=regex,
            display_name=display_name,
            converter=converter,
            default=default,
        )
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
        metadata = _get_command_metadata(func)
        if metadata.verifier is not None:
            msg = "This command listener already has a verifier"
            raise RuntimeError(msg)
        metadata.verifier = verifier_func
        return func

    return decorator
