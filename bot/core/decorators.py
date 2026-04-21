from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .components import BaseDispatcher, BaseEvent, Cooldown, ListenerMetadata
from .listeners import GenericListener, LambdaListener

if TYPE_CHECKING:
    from .modals import MetaFilter


def _get_or_create_metadata_list(func: Callable[..., Any]) -> list[ListenerMetadata]:
    """Get or create the listener metadata list on a function.

    Args:
        func: The function to get metadata from.

    Returns:
        The list of ListenerMetadata objects.
    """
    metadata_list: list[ListenerMetadata] | None = getattr(
        func, "_listener_metadata", None
    )
    if metadata_list is None:
        metadata_list = []
        setattr(func, "_listener_metadata", metadata_list)
    return metadata_list


# Matchers


def meta_filter_decorator[T: Callable[..., Any]](
    meta_filter: MetaFilter,
    listener_cls: type[GenericListener] = GenericListener,
    dispatcher_type: type[BaseDispatcher] = BaseDispatcher,
) -> Callable[[T], T]:
    """Create a listener that matches events based on a meta filter.

    Args:
        meta_filter: The meta filter to use.
        listener_cls: The listener class to use.
        dispatcher_type: The dispatcher type to use.
    """

    def decorator(func: T) -> T:
        metadata = ListenerMetadata(
            dispatcher=dispatcher_type,
            listener_cls=listener_cls,
            meta_filter=meta_filter,
        )
        _get_or_create_metadata_list(func).append(metadata)
        return func

    return decorator


def lambda_filter_decorator[T: Callable[..., Any], E: BaseEvent](
    event_types: list[type[E]],
    match_fn: Callable[[E], bool],
    listener_cls: type[LambdaListener] = LambdaListener,
    dispatcher_type: type[BaseDispatcher] = BaseDispatcher,
) -> Callable[[T], T]:
    """Create a listener that matches events based on a function.

    Args:
        event_types: The event types to match.
        match_fn: The function to determine if an event matches.
        listener_cls: The listener class to use.
        dispatcher_type: The dispatcher type to use.
    """

    def decorator(func: T) -> T:
        metadata = ListenerMetadata(
            dispatcher=dispatcher_type,
            listener_cls=listener_cls,
            init_kwargs={"event_types": event_types, "match_fn": match_fn},
        )
        _get_or_create_metadata_list(func).append(metadata)
        return func

    return decorator


def on_match[E: BaseEvent](
    event_types: type[E] | list[type[E]], match_fn: Callable[[E], bool]
):
    """Create a listener that matches events based on a function.

    Args:
        event_types: The event types to match.
        match_fn: The function to determine if an event matches.
    """
    if not isinstance(event_types, list):
        event_types = [event_types]
    return lambda_filter_decorator(event_types, match_fn)


def cooldown[T: Callable[..., Any]](
    rate: int, per: float, type_: str | list[str] = "user"
) -> Callable[[T], T]:
    """Apply a cooldown to a command.

    Args:
        rate: Number of uses allowed.
        per: Time period in seconds.
        type_: The bucket type for the cooldown.

    """

    def decorator(func: T) -> T:
        metadata_list = _get_or_create_metadata_list(func)
        if metadata_list:
            metadata_list[-1].cooldown = Cooldown(rate, per, type_)
        else:
            metadata = ListenerMetadata(cooldown=Cooldown(rate, per, type_))
            metadata_list.append(metadata)
        return func

    return decorator


def priority[T: Callable[..., Any]](value: int) -> Callable[[T], T]:
    """Apply a priority to a listener.

    Args:
        value: The priority value. Higher values execute first.

    """

    def decorator(func: T) -> T:
        metadata_list = _get_or_create_metadata_list(func)
        if metadata_list:
            metadata_list[-1].priority = value
        else:
            metadata = ListenerMetadata(priority=value)
            metadata_list.append(metadata)
        return func

    return decorator


# Add-ons
