from __future__ import annotations

from enum import IntEnum, auto


class ParameterType(IntEnum):
    """Parameter types for command parameters."""

    POSITIONAL_ONLY = auto()
    POSITIONAL_OR_KEYWORD = auto()
    VAR_POSITIONAL = auto()
    KEYWORD_ONLY = auto()
    VAR_KEYWORD = auto()
