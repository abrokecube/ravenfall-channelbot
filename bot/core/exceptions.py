# pyright: reportAny=false, reportExplicitAny=false
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cooldown import Cooldown
from .modals import Parameter
from .enums import ParameterType


class ListenerError(Exception):
    """Base exception for listener-related errors."""
    def __init__(self, message: str = "Listener error"):
        self.message: str = message
        super().__init__(self.message)
        
class CommandError(ListenerError):
    """Raised when a non-fatal error occurs in a command"""
    def __init__(self, message: str = "Command error"):
        super().__init__(message)
        
class CheckFailure(ListenerError):
    """Raised when a listener check fails."""
    def __init__(self, message: str = "Check failed"):
        super().__init__(message)

class VerificationFailure(ListenerError):
    """Raised when a listener verification fails."""
    def __init__(self, message: str = "Verification failed"):
        super().__init__(message)

class ListenerOnCooldown(ListenerError):
    """Raised when a listener is on cooldown."""
    def __init__(self, cooldown: Cooldown, retry_after: float):
        self.retry_after: float = retry_after
        self.cooldown: Cooldown = cooldown
        super().__init__(f"Listener is on cooldown. Try again in {retry_after:.2f}s")

class ListenerRegistrationError(ListenerError):
    """Raised when there's an error registering a listener or redeem."""
    def __init__(self, name: str, item_type: str = "Listener"):
        self.display_name: str = name
        self.item_type: str = item_type
        super().__init__(f"{item_type} '{name}' already exists")

class ArgumentError(ListenerError):
    """Base exception for argument parsing errors."""
    pass

class UnknownFlagError(ArgumentError):
    """Raised when an unknown flag is provided."""
    def __init__(self, flag_name: str):
        self.flag_name: str = flag_name
        super().__init__(f"Unknown flag '{flag_name}'")

class DuplicateParameterError(ArgumentError):
    """Raised when a parameter is provided multiple times."""
    def __init__(self, parameter: Parameter):
        self.parameter: Parameter = parameter
        super().__init__(f"Multiple values provided for parameter '{parameter.display_name}'")

class MissingRequiredArgumentError(ArgumentError):
    """Raised when a required argument is missing."""
    def __init__(self, parameter: Parameter):
        self.parameter: Parameter = parameter
        keyword_only = parameter.kind == ParameterType.KEYWORD_ONLY
        arg_type = "keyword-only argument" if keyword_only else "argument"
        super().__init__(f"Missing required {arg_type}: {parameter.display_name}")

class UnknownArgumentError(ArgumentError):
    """Raised when unknown arguments are provided."""
    def __init__(self, args: list[Any]):
        self.arguments: list[Any] = args
        args_str = ', '.join(f"'{arg}'" for arg in args) if isinstance(args[0], str) else ' '.join(str(a) for a in args)
        super().__init__(f"Unknown arguments: {args_str}")

class ArgumentConversionError(ArgumentError):
    """Raised when argument conversion fails."""
    def __init__(self, message: str, value: str | None = None, parameter: Parameter | None = None, original_error: Exception | None = None):
        self.value: str | None = value
        self.original_error: Exception | None = original_error
        self.parameter: Parameter | None = parameter
        super().__init__(message)

class EmptyFlagValueError(ArgumentConversionError):
    """Raised when a flag is provided without a value."""
    def __init__(self, parameter: Parameter):
        super().__init__(f"Expected a value for '{parameter.display_name}'", None, parameter, None)
