from __future__ import annotations
from enum import StrEnum

class EventCategory(StrEnum):
    Generic = "generic"
    Message = "message"
    Command = "command"
    RavenBotMessage = "ravenbot_message"
    RavenfallMessage = "ravenfall_message"

class EventSource(StrEnum):
    Any = "any"
    Twitch = "twitch"
    RavenBot = "ravenbot"
    Ravenfall = "ravenfall"
    HTTPRequest = "http_request"

class Dispatcher(StrEnum):
    Base = "base"
    Generic = "generic"
    Command = "command"

class BucketType(StrEnum):
    USER = "user"
    CHANNEL = "channel"
    GUILD = "guild"
    GLOBAL = "global"
