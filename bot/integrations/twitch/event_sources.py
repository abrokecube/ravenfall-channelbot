from bot.integrations.twitch.services import TwitchService


from typing import override, cast
from bot.core.components import BaseEventSource, EventManager
from bot.db.service import DatabaseService
from collections.abc import Collection

from twitchAPI.object.api import TwitchUser
from twitchAPI.twitch import Twitch
from twitchAPI.chat import Chat
from twitchAPI.type import AuthScope as TWAuthScope
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.type import MissingScopeException, InvalidTokenException
from twitchAPI import helper

from colorama import Fore, Back
import logging

import asyncio

from .services import TwitchService

LOGGER = logging.getLogger(__name__)

AuthScope = TWAuthScope


def print_to_console(msg: str):
    """Print a message to the console."""
    print(  # noqa: T201
        f"{Fore.LIGHTYELLOW_EX}{Back.RESET}twitch_event_source:{Fore.RESET} {msg}{Fore.RESET}{Back.RESET}"
    )


class TwitchEventSource(BaseEventSource):
    """Event source for Twitch events. Includes a TwitchService."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        bot_user_id: str,
        bot_admin_uids: Collection[str] | None = None,
        app_scopes: Collection[AuthScope] | None = None,
        bot_user_scopes: Collection[AuthScope] | None = None,
    ):
        super().__init__()
        self.app_id: str = app_id
        self.app_secret: str = app_secret
        self.bot_user_id: str = bot_user_id
        self.twitch_chat: Chat | None = None
        self.bot_admin_uids: set[str] = set(bot_admin_uids or [])
        self.app_scopes: list[AuthScope] = list(app_scopes or [])
        self.bot_user_scopes: list[AuthScope] = list(bot_user_scopes or [])
        self.app_twitch: Twitch | None = None
        self._twitch_service: TwitchService = TwitchService()
        self._auth_lock: asyncio.Lock = asyncio.Lock()

    async def _get_twitch_auth_instance(
        self,
        user_id: int | str,
        user_name: str | None = None,
        scopes: list[AuthScope] | None = None,
    ) -> Twitch:
        if not scopes:
            scopes = []
        db = self.global_context.require_service(DatabaseService)

        save_new_tokens = True
        access_token, refresh_token = None, None
        result = await db.get_twitch_tokens(user_id)
        if result is not None:
            access_token, refresh_token = result
            save_new_tokens = False

        while True:
            async with self._auth_lock:
                pass
            if access_token is None or refresh_token is None:
                auth = UserAuthenticator(twitch, scopes, True)
                print_to_console(f"Auth scopes: {', '.join([x.value for x in scopes])}")
                print_to_console(
                    f"{Fore.LIGHTYELLOW_EX}Please authenticate with the Twitch account: {user_name or user_id}"
                )
                result = cast(
                    tuple[str, str] | None, await auth.authenticate(use_browser=False)
                )
                if result is None:
                    continue
                access_token, refresh_token = result

            try:
                await twitch.set_user_authentication(access_token, scopes, refresh_token)
                user: TwitchUser | None = None
                if save_new_tokens:
                    user = await helper.first(twitch.get_users())
                    if user:
                        await db.update_twitch_tokens(
                            user.id, access_token, refresh_token, user.login
                        )
            except MissingScopeException:
                print_to_console(f"{Fore.LIGHTRED_EX}Token is missing scopes")
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue
            except InvalidTokenException:
                print_to_console(f"{Fore.LIGHTRED_EX}Invalid token")
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue
            except Exception as e:
                print_to_console(
                    f"{Fore.LIGHTRED_EX}Error setting user authentication: {e}"
                )
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue

            if user is not None:
                if user.id == str(user_id):
                    return twitch
                print_to_console(
                    f"{Fore.LIGHTRED_EX}Token does not match user, please try again"
                )
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue
            return twitch

    async def authenticate_user(self, user_id: str, scopes: Collection[AuthScope]):
        t = await self._get_twitch_auth_instance(user_id, scopes=list(scopes))
        self._twitch_service.twitches[user_id] = t

    @override
    async def setup(self, event_manager: EventManager):
        self.app_twitch = await Twitch(
            self.app_id, self.app_secret, target_app_auth_scope=self.app_scopes
        )
        db = await self.global_context.wait_for_service(DatabaseService)

    async def join_chat(self, channel_name: str | Collection[str]):
        if not self.twitch_chat:
            msg = "Twitch event source has not been initialized by an EventManager."
            raise RuntimeError(msg)
