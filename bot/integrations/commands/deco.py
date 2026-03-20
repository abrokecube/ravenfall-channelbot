from .types import VerifierType
from typing import Any
from collections.abc import Callable

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
