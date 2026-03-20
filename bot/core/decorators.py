from typing import Callable, Any, cast
from .enums import EventCategory, Dispatcher, BucketType
from .modals import MetaFilter
from .cooldown import Cooldown
from .converters import BaseConverter
from .checks import BaseCheck, FunctionCheck
from .events import BaseEvent, TwitchRedemptionEvent, MessageEvent
from .listeners import LambdaListener, GenericListener
from .types import VerifierType, ParameterConfig, CheckFuncType

# Matchers

def meta_filter_decorator[T: Callable[..., Any]](
    meta_filter: MetaFilter,
    listener_cls: type[GenericListener] = GenericListener, 
    dispatcher_type: Dispatcher = Dispatcher.Generic) -> Callable[[T], T]:
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
    dispatcher_type: Dispatcher = Dispatcher.Generic) -> Callable[[T], T]:
    def decorator(func: T) -> T:
        setattr(func, "_listener_init_params", {
            "event_types": event_types,
            "match_fn": match_fn
        })
        setattr(func, "_listener_dispatcher", dispatcher_type)
        setattr(func, "_listener_class", listener_cls)
        return func
    return decorator


def on_match[E: BaseEvent](event_types: type[E] | list[type[E]], match_fn: Callable[[E], bool]):
    if not isinstance(event_types, list):
        event_types = [event_types]
    return lambda_filter_decorator(event_types, match_fn)


def command[T: Callable[..., Any]](
    name: str | None = None, short_help: str | None = None, help: str | None = None,
    aliases: list[str] | None = None, verifier: VerifierType | None = None, hidden: bool = False, **kwargs: Any) -> Callable[[T], T]:
    if not aliases:
        aliases = []
    def decorator(func: T):
        kwargs.update({
            "name": name,
            "short_help": short_help,
            "help": help,
            "aliases": aliases,
            "verifier": verifier,
            "hidden": hidden
        })
        setattr(func, "_listener_init_params", kwargs)
        setattr(func, "_listener_meta_filter", MetaFilter(
            (EventCategory.Message,), True,
            [], False
        ))
        setattr(func, "_listener_dispatcher", Dispatcher.Command)
        return func
    return decorator

# Add-ons




