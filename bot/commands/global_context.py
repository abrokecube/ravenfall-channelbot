from __future__ import annotations
from typing import TYPE_CHECKING, Dict, List
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from twitchAPI.chat import Chat
    from twitchAPI.twitch import Twitch
    from twitchAPI.eventsub.websocket import EventSubWebsocket

@dataclass
class GlobalContext:
    twitch_chat: Chat = None
    bot_twitch: Twitch = None
    channel_twitches: Dict[str, Twitch] = field(default_factory=dict)  # channel id -> Twitch
    _twitch_channel_eventsubs: List[EventSubWebsocket] = field(default_factory=list)
    