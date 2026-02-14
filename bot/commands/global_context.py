from __future__ import annotations
from typing import TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from twitchAPI.chat import Chat

@dataclass
class GlobalContext:
    twitch_chat: Chat
    