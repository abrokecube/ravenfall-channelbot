from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from .checks import BaseCheck


class ChatRoomCapabilities(NamedTuple):
    """Chat room capabilities."""

    multiline: bool
    max_message_length: int


@dataclass
class ChatMessageMetadata:
    """Metadata for chat message specific stuff."""

    checks: list[BaseCheck] = field(default_factory=list)
