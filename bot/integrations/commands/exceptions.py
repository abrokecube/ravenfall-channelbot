from __future__ import annotations

from typing import TYPE_CHECKING

from bot.core.exceptions import ListenerError

from .enums import ParameterType

if TYPE_CHECKING:
    from collections.abc import Sequence

    from . import Parameter


class CommandError(ListenerError):
    """Raised when a non-fatal error occurs in a command."""

    def __init__(self, message: str = "Command error"):
        super().__init__(message)


class VerificationFailureError(ListenerError):
    """Raised when a listener verification fails."""

    def __init__(self, message: str = "Verification failed"):
        super().__init__(message)


class ArgumentError(ListenerError):
    """Base exception for argument parsing errors."""


class UnknownFlagError(ArgumentError):
    """Raised when an unknown flag is provided."""

    def __init__(self, flag_name: str):
        self.flag_name: str = flag_name
        super().__init__(f"Unknown flag '{flag_name}'")


class DuplicateParameterError(ArgumentError):
    """Raised when a parameter is provided multiple times."""

    def __init__(self, parameter: Parameter):
        self.parameter: Parameter = parameter
        super().__init__(
            f"Multiple values provided for parameter '{parameter.display_name}'"
        )


class MissingRequiredArgumentError(ArgumentError):
    """Raised when a required argument is missing."""

    def __init__(self, parameter: Parameter):
        self.parameter: Parameter = parameter
        keyword_only = parameter.kind == ParameterType.KEYWORD_ONLY
        arg_type = "keyword-only argument" if keyword_only else "argument"
        super().__init__(f"Missing required {arg_type}: {parameter.display_name}")


class UnknownArgumentError(ArgumentError):
    """Raised when unknown arguments are provided."""

    def __init__(self, args: Sequence[object]):
        self.arguments: tuple[object, ...] = tuple(args)
        args_str = (
            ", ".join(f"'{arg}'" for arg in args)
            if isinstance(args[0], str)
            else " ".join(str(a) for a in args)
        )
        super().__init__(f"Unknown arguments: {args_str}")


class ArgumentConversionError(ArgumentError):
    """Raised when argument conversion fails."""

    def __init__(
        self,
        message: str,
        value: str | None = None,
        parameter: Parameter | None = None,
        original_error: Exception | None = None,
    ):
        self.value: str | None = value
        self.original_error: Exception | None = original_error
        self.parameter: Parameter | None = parameter
        super().__init__(message)


class EmptyFlagValueError(ArgumentConversionError):
    """Raised when a flag is provided without a value."""

    def __init__(self, parameter: Parameter):
        super().__init__(
            f"Expected a value for '{parameter.display_name}'", None, parameter, None
        )
