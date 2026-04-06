from collections.abc import Awaitable, Callable

from msgspec import Struct

from bot.integrations.ravenfall.events import RavenfallEvent

type RavenfallInstanceEventHook = Callable[[RavenfallEvent], Awaitable[None]]


class RavenfallConfig(Struct):
    """Configuration model for a Ravenfall instance."""

    twitch_id: str
    twitch_login: str
    query_server_base_url: str
    middleman_connection_id: str | None = None
