from typing import List, Callable, Type
from .enums import EventCategory, EventSource, Dispatcher, BucketType
from .modals import MetaFilter
from .cooldown import Cooldown
from .converters import BaseConverter
from .checks import BaseCheck, FunctionCheck
from .events import BaseEvent, TwitchRedemptionEvent, MessageEvent
from .listeners import LambdaListener, GenericListener

# Matchers

def _meta_filter_decorator(
    meta_filter: MetaFilter,
    listener_cls: type[GenericListener] = GenericListener, 
    dispatcher_type: Dispatcher = Dispatcher.Generic):
    def decorator(func):
        func._listener_meta_filter = meta_filter
        func._listener_dispatcher = dispatcher_type
        func._listener_class = listener_cls
        return func
    return decorator

def _lambda_filter_decorator(
    event_types: list[type[BaseEvent]], 
    match_fn: Callable[[BaseEvent], bool], 
    listener_cls: type[LambdaListener] = LambdaListener, 
    dispatcher_type: Dispatcher = Dispatcher.Generic):
    def decorator(func):
        func._listener_init_params = {
            "event_types": event_types,
            "match_fn": match_fn
        }
        func._listener_dispatcher = dispatcher_type
        func._listener_class = listener_cls
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

def on_match(event_types: type[BaseEvent] | list[type[BaseEvent]], match_fn: Callable[[BaseEvent], bool]):
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

def command(
    name: str | None = None, short_help: str | None = None, help: str | None = None,
    aliases: List[str] = [], verifier: Callable = None, hidden: bool = False, **kwargs):
    def decorator(func):
        kwargs.update({
            "name": name,
            "short_help": short_help,
            "help": help,
            "aliases": aliases,
            "verifier": verifier,
            "hidden": hidden
        })
        func._listener_init_params = kwargs
        func._listener_meta_filter = MetaFilter(
            (EventCategory.Message,), True,
            [], False
        )
        func._listener_dispatcher = Dispatcher.Command
        return func
    return decorator

# Add-ons

def cooldown(rate: int, per: float, type: BucketType | List[BucketType] = BucketType.USER):
    """Decorator to apply a cooldown to a command.
    
    Args:
        rate: Number of uses allowed.
        per: Time period in seconds.
        type: The bucket type for the cooldown.
    """
    def decorator(func):
        func._listener_cooldown = Cooldown(rate, per, type)
        return func
    return decorator

def parameter(
    name: str, aliases: str | List[str] = [],
    greedy: bool = False, hidden: bool = False,
    help: str = None, regex: str = None,
    display_name: str = None, converter: BaseConverter | type[BaseConverter] | None = None
    ):
    """Decorator to configure a command parameter.
    
    Args:
        name: The name of the parameter to configure.
        aliases: Optional alias or list of aliases for the parameter.
        greedy: If True, the parameter will consume all remaining input as a single string.
        hidden: If True, the parameter will be hidden from help documentation.
        help: Help text for the parameter.
        regex: Regex pattern to match for this parameter.
    """
    def decorator(func):
        if not hasattr(func, '_listener_command_params'):
            func._listener_command_params = {}
        func._listener_command_params[name] = {
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

def verification(verifier_func):
    """Decorator to add a verification function to a command.
    
    The verifier function should accept (ctx, *args, **kwargs) matching the command's signature.
    It should return True (pass), False (fail), or a string (fail with message).
    """
    def decorator(func):
        func._listener_command_verifier = verifier_func
        return func
    return decorator

CheckFunc = Callable[[BaseEvent], bool]
def checks(*predicates: CheckFunc | BaseCheck | Type[BaseCheck]):
    """Decorator to add checks to a command.
    
    Args:
        *predicates: One or more functions or Check classes/instances.
    """
    def decorator(func):
        if not hasattr(func, '_command_checks'):
            func._listener_command_checks = []
        
        processed_checks = []
        for p in predicates:
            if isinstance(p, type) and issubclass(p, BaseCheck):
                processed_checks.append(p())
            elif isinstance(p, BaseCheck):
                processed_checks.append(p)
            else:
                processed_checks.append(FunctionCheck(p))
                
        func._listener_command_checks.extend(processed_checks)
        return func
    return decorator
