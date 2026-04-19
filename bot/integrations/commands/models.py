from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from .listeners import CommandListener


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
