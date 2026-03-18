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

def _meta_filter_decorator[T: Callable[..., Any]](
    meta_filter: MetaFilter,
    listener_cls: type[GenericListener] = GenericListener, 
    dispatcher_type: Dispatcher = Dispatcher.Generic) -> Callable[[T], T]:
    def decorator(func: T) -> T:
        setattr(func, "_listener_meta_filter", meta_filter)
        setattr(func, "_listener_dispatcher", dispatcher_type)
        setattr(func, "_listener_class", listener_cls)
        return func
    return decorator

def _lambda_filter_decorator[T: Callable[..., Any], E: BaseEvent](
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

# def on_message(*, platforms: list[EventSource] | None=None):
#     _sources = []
#     if platforms:
#         _sources = tuple(platforms)
#     meta_filter = MetaFilter(
#         (EventCategory.Message,), True, 
#         _sources, bool(platforms)            
#     )
#     return _meta_filter_decorator(meta_filter)

def on_match[E: BaseEvent](event_types: type[E] | list[type[E]], match_fn: Callable[[E], bool]):
    if not isinstance(event_types, list):
        event_types = [event_types]
    return _lambda_filter_decorator(event_types, match_fn)

def on_message(match_fn: Callable[[MessageEvent], bool]):
    return _lambda_filter_decorator(
        [MessageEvent], match_fn, dispatcher_type=Dispatcher.Generic
    )

def on_twitch_redeem(match_fn: Callable[[TwitchRedemptionEvent], bool]):
    return _lambda_filter_decorator(
        [TwitchRedemptionEvent], match_fn, dispatcher_type=Dispatcher.TwitchRedeem
    )

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

def cooldown[T: Callable[..., Any]](rate: int, per: float, type: BucketType | list[BucketType] = BucketType.USER) -> Callable[[T], T]:
    """Decorator to apply a cooldown to a command.
    
    Args:
        rate: Number of uses allowed.
        per: Time period in seconds.
        type: The bucket type for the cooldown.
    """
    def decorator(func: T) -> T:
        setattr(func, "_listener_cooldown", Cooldown(rate, per, type))
        return func
    return decorator

def parameter[T: Callable[..., Any]](
    name: str, aliases: str | list[str] | None = None,
    greedy: bool = False, hidden: bool = False,
    help: str = "", regex: str = "",
    display_name: str = "", converter: BaseConverter | type[BaseConverter] | None = None
    ) -> Callable[[T], T]:
    """Decorator to configure a command parameter.
    
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
    def decorator(func: T) -> T:
        if not hasattr(func, '_listener_command_params'):
            setattr(func, '_listener_command_params', {})
        command_params = cast(ParameterConfig, getattr(func, '_listener_command_params'))
        command_params[name] = {
            'aliases': aliases,
            'greedy': greedy,
            'hidden': hidden,
            'help': help,
            'regex': regex,
            'display_name': display_name,
            'converter': converter
        }
        return func
    return decorator

def verification[T: Callable[..., Any]](verifier_func: VerifierType) -> Callable[[T], T]:
    """Decorator to add a verification function to a command.
    
    The verifier function should accept (ctx, *args, **kwargs) matching the command's signature.
    It should return True (pass), False (fail), or a string (fail with message).
    """
    def decorator(func: T) -> T:
        setattr(func, '_listener_command_verifier', verifier_func)
        return func
    return decorator

def checks[T: Callable[..., Any]](*predicates: CheckFuncType | BaseCheck | type[BaseCheck]) -> Callable[[T], T]:
    """Decorator to add checks to a command.
    
    Args:
        *predicates: One or more functions or Check classes/instances.
    """
    def decorator(func: T) -> T:
        if not hasattr(func, '_listener_command_checks'):
            setattr(func, '_listener_command_checks', [])
        
        processed_checks: list[BaseCheck] = []
        for p in predicates:
            if isinstance(p, type) and issubclass(p, BaseCheck):
                processed_checks.append(p())
            elif isinstance(p, BaseCheck):
                processed_checks.append(p)
            else:
                processed_checks.append(FunctionCheck(p))
                
        cast(list[BaseCheck], getattr(func, '_listener_command_checks')).extend(processed_checks)
        return func
    return decorator
