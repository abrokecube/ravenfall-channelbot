from __future__ import annotations
from typing import TYPE_CHECKING
from .command_enums import ParameterKind

if TYPE_CHECKING:
    from .commands import Parameter, Cooldown

class CommandError(Exception):
    """Base exception for command-related errors."""
    def __init__(self, message: str = "Command error"):
        self.message = message
        super().__init__(self.message)

class CheckFailure(CommandError):
    """Raised when a command check fails."""
    def __init__(self, message: str = "Check failed"):
        super().__init__(message)

class VerificationFailure(CommandError):
    """Raised when a command verification fails."""
    def __init__(self, message: str = "Verification failed"):
        super().__init__(message)

class CommandOnCooldown(CommandError):
    """Raised when a command is on cooldown."""
    def __init__(self, cooldown: Cooldown, retry_after: float):
        self.retry_after = retry_after
        self.cooldown = cooldown
        super().__init__(f"Command is on cooldown. Try again in {retry_after:.2f}s")

class CommandRegistrationError(CommandError):
    """Raised when there's an error registering a command or redeem."""
    def __init__(self, name: str, item_type: str = "Command"):
        self.display_name = name
        self.item_type = item_type
        super().__init__(f"{item_type} '{name}' already exists")

class ArgumentError(CommandError):
    """Base exception for argument parsing errors."""
    pass

class UnknownFlagError(ArgumentError):
    """Raised when an unknown flag is provided."""
    def __init__(self, flag_name: str):
        self.flag_name = flag_name
        super().__init__(f"Unknown flag '{flag_name}'")

class DuplicateParameterError(ArgumentError):
    """Raised when a parameter is provided multiple times."""
    def __init__(self, parameter: Parameter):
        self.parameter = parameter
        super().__init__(f"Multiple values provided for parameter '{parameter.display_name}'")

class MissingRequiredArgumentError(ArgumentError):
    """Raised when a required argument is missing."""
    def __init__(self, parameter: Parameter):
        self.parameter = parameter
        keyword_only = parameter.kind == ParameterKind.KEYWORD_ONLY
        arg_type = "keyword-only argument" if keyword_only else "argument"
        super().__init__(f"Missing required {arg_type}: {parameter.display_name}")

class UnknownArgumentError(ArgumentError):
    """Raised when unknown arguments are provided."""
    def __init__(self, args: list):
        self.arguments = args
        args_str = ', '.join(f"'{arg}'" for arg in args) if isinstance(args[0], str) else ' '.join(str(a) for a in args)
        super().__init__(f"Unknown arguments: {args_str}")

class ArgumentConversionError(ArgumentError):
    """Raised when argument conversion fails."""
    def __init__(self, message: str = None, value: str = None, parameter: Parameter = None, original_error: Exception = None):
        self.value = value
        self.original_error = original_error
        self.parameter = parameter
        self.message = message
        if message:
            error_msg = message
        else:
            error_msg = f"Cannot convert '{value}' into {parameter.type_title} ({parameter.display_name})"
        # if original_error:
        #     error_msg += f": {original_error}"
        super().__init__(error_msg)

class EmptyFlagValueError(ArgumentConversionError):
    """Raised when a flag is provided without a value."""
    def __init__(self, parameter: Parameter = None):
        super().__init__(f"Expected a value for '{parameter.display_name}'", None, parameter, None)
