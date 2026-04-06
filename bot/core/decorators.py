from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .components import BaseDispatcher, BaseEvent, Cooldown
from .listeners import GenericListener, LambdaListener

if TYPE_CHECKING:
    from .modals import MetaFilter

# Matchers


def meta_filter_decorator[T: Callable[..., Any]](
    meta_filter: MetaFilter,
    listener_cls: type[GenericListener] = GenericListener,
    dispatcher_type: type[BaseDispatcher] = BaseDispatcher,
) -> Callable[[T], T]:
    def decorator(func: T) -> T:
        setattr(func, "_listener_meta_filter", meta_filter)
        setattr(func, "_listener_dispatcher", dispatcher_type)
        setattr(func, "_listener_class", listener_cls)
        return func

    return decorator


def lambda_filter_decorator[T: Callable[..., Any], E: BaseEvent](
    event_types: list[type[E]],
    match_fn: Callable[[E], bool],
    listener_cls: type[LambdaListener] = LambdaListener,
    dispatcher_type: type[BaseDispatcher] = BaseDispatcher,
) -> Callable[[T], T]:
    def decorator(func: T) -> T:
        setattr(
            func,
            "_listener_init_params",
            {"event_types": event_types, "match_fn": match_fn},
        )
        setattr(func, "_listener_dispatcher", dispatcher_type)
        setattr(func, "_listener_class", listener_cls)
        return func

    return decorator


def on_match[E: BaseEvent](
    event_types: type[E] | list[type[E]], match_fn: Callable[[E], bool]
):
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
        type: The bucket type for the cooldown.

    """

    def decorator(func: T) -> T:
        setattr(func, "_listener_cooldown", Cooldown(rate, per, type_))
        return func

    return decorator


# Add-ons
