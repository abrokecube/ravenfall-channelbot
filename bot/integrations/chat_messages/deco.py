from bot.core.decorators import lambda_filter_decorator
from bot.integrations.chat_messages import MessageEvent, Cooldown
from bot.core.enums import Dispatcher
from .checks import FunctionCheck
from collections.abc import Callable
from typing import Any, cast
from . import BaseCheck
from .types import CheckFuncType

def on_message(match_fn: Callable[[MessageEvent], bool]):
    return lambda_filter_decorator(
        [MessageEvent], match_fn, dispatcher_type=Dispatcher.Generic
    )

def cooldown[T: Callable[..., Any]](rate: int, per: float, type: str | list[str] = "user") -> Callable[[T], T]:
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
