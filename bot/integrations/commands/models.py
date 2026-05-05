from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from .converters import BaseConverter
    from .listeners import CommandListener
    from .types import VerifierType


class CommandResponse(NamedTuple):
    """Response from a command execution."""

    text: str
    args: tuple[object, ...]
    kwargs: dict[str, object]


class CommandExecutionResult(NamedTuple):
    """Result from a command execution."""

    responses: list[CommandResponse]
    error: Exception | None


class CommandDispatchResult(NamedTuple):
    """Result from a command dispatch."""

    listener: CommandListener | None
    error: Exception | None


@dataclass
class ParameterConfig:
    """Parameter configuration."""

    aliases: list[str]
    greedy: bool
    hidden: bool
    help_text: str | None
    regex: str | None
    display_name: str | None
    converter: BaseConverter | type[BaseConverter] | None
    default: object


@dataclass
class CommandMetadata:
    """Metadata for command listeners set by decorators."""

    name: str | None = None
    short_help_text: str | None = None
    help_text: str | None = None
    aliases: list[str] = field(default_factory=list)
    verifier: VerifierType | None = None
    hidden: bool = False
    title: str | None = None
    parameters: dict[str, ParameterConfig] = field(default_factory=dict)
