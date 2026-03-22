from __future__ import annotations
from bot.core.components import BaseService

from twitchAPI.twitch import Twitch


class TwitchService(BaseService):
    """Service for twitch related stuff."""

    def __init__(self) -> None:
        super().__init__()
        # self.twitches: dict[str, Twitch] = {}
        # yo instead of initializing a Twitch every time just call set_user_authentication with verify=false
