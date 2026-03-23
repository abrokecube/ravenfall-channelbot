import asyncio
import functools
import logging
from collections.abc import Collection
from twitchAPI.twitch import Twitch
from typing import Any, cast, override

from colorama import Back, Fore
from sqlalchemy import select
from twitchAPI import helper
from twitchAPI.chat import Chat, ChatMessage, ChatUser
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.object.api import TwitchUser
from twitchAPI.twitch import Twitch
from twitchAPI.type import AuthScope as TWAuthScope
from twitchAPI.type import InvalidTokenException, MissingScopeException, ChatEvent

from bot.core.components import BaseEventSource, EventManager
from bot.integrations.chat_messages.enums import UserRole
from bot.db.service import DatabaseService

from . import TwitchAuth
from .services import TwitchService
from .events import TwitchMessageEvent

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
        self.bot_twitch: Twitch | None = None
        self._twitch_service: TwitchService = TwitchService(self)
        self._auth_lock: asyncio.Lock = asyncio.Lock()
        self.bot_user: TwitchUser | None = None

    async def _save_user_token(
        self,
        user_id: str,
        user_login: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
    ):
        db = self.global_context.require_service(DatabaseService)
        async with db.get_session() as session:
            result = await session.execute(
                select(TwitchAuth).where(TwitchAuth.user_id == user_id)
            )
            obj = result.scalar_one_or_none()
            if not obj:
                if not all((user_login, access_token, refresh_token)):
                    msg = "user_login, access_token, and refresh_token must be supplied for new entries"
                    raise ValueError(msg)
                obj = TwitchAuth(
                    user_id=user_id,
                    user_name=user_login,
                    access_token=access_token,
                    refresh_token=refresh_token,
                )
                await session.flush()
            if user_login:
                obj.user_name = user_login
            if access_token:
                obj.access_token = access_token
            if refresh_token:
                obj.refresh_token = refresh_token

    async def _twitchio_token_callback(
        self, access_token: str, user_id: str | None = None
    ):
        if not user_id:
            return
        await self._save_user_token(user_id, access_token=access_token)

    async def _fetch_user_auth(self, user_id: str):
        db = self.global_context.require_service(DatabaseService)
        async with db.get_session() as session:
            result = await session.execute(
                select(TwitchAuth).where(TwitchAuth.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def _get_twitch_auth_instance(
        self,
        user_id: int | str,
        user_name: str | None = None,
        scopes: list[AuthScope] | None = None,
        twitch: Twitch | None = None,
    ) -> Twitch:
        if not self.bot_twitch:
            msg = "Event source has not been initialized"
            raise RuntimeError(msg)
        if isinstance(user_id, int):
            user_id = str(user_id)
        if not scopes:
            scopes = []
        __ = self.global_context.require_service(DatabaseService)

        save_new_tokens = True
        access_token, refresh_token = None, None
        twitch_auth = await self._fetch_user_auth(user_id)
        if twitch_auth is not None:
            access_token = twitch_auth.access_token
            refresh_token = twitch_auth.refresh_token
            if not user_name:
                user_name = twitch_auth.user_name
            save_new_tokens = False

        if not twitch:
            twitch = await Twitch(self.app_id, authenticate_app=False)
        while True:
            if access_token is None or refresh_token is None:
                auth = UserAuthenticator(self.bot_twitch, scopes, True)
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
                access_token = twitch.get_user_auth_token()
                if not access_token:
                    print_to_console(f"{Fore.LIGHTRED_EX}Failed to authenticate")
                    continue
                user: TwitchUser | None = None
                if save_new_tokens or not user_name:
                    user = await helper.first(twitch.get_users())
                    if user:
                        await self._save_user_token(
                            user.id, user.login, access_token, refresh_token
                        )
                else:
                    await self._save_user_token(user_id, access_token=access_token)
                twitch.user_auth_refresh_callback = functools.partial(
                    self._twitchio_token_callback, user_id=user_id
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

    async def authenticate_user(
        self, user_id: str, scopes: Collection[AuthScope]
    ) -> Twitch:
        """Authenticate a user. If a matching token is not found in the database, it will ask for authentication."""
        t = await self._get_twitch_auth_instance(user_id, scopes=list(scopes))
        self._twitch_service.twitches[user_id] = t
        return t

    @override
    async def setup(self, event_manager: EventManager):
        db = await self.global_context.wait_for_service(DatabaseService)
        self.bot_twitch = await Twitch(
            self.app_id, self.app_secret, target_app_auth_scope=self.app_scopes
        )
        self.bot_twitch = await self._get_twitch_auth_instance(
            self.bot_user_id, scopes=list(self.bot_user_scopes), twitch=self.bot_twitch
        )
        self.bot_user = await helper.first(self.bot_twitch.get_users())

        self.twitch_chat = Chat(self.bot_twitch)
        self.twitch_chat.start()
        self.twitch_chat.register_event(ChatEvent.MESSAGE, self._chat_on_message)
        self.global_context.register_service(TwitchService, self._twitch_service)

    async def join_chat(self, channel_name: str | Collection[str]):
        """Connect to a channel's chat."""
        if not self.twitch_chat:
            msg = "Twitch event source has not been initialized."
            raise RuntimeError(msg)

    def _get_user_roles(self, user: ChatUser, room_id: str | None):
        roles: set[UserRole] = set()
        if user.id in self.bot_admin_uids:
            roles.add(UserRole.BOT_ADMINISTRATOR)
        if user.id == room_id:
            roles.add(UserRole.ADMINISTRATOR)
        if user.lead_mod:
            roles.add(UserRole.ADMINISTRATOR)
        if user.mod:
            roles.add(UserRole.MODERATOR)
        roles.add(UserRole.USER)
        return roles

    async def _chat_on_message(self, message: ChatMessage):
        if not message.room:
            msg = "Message has no room property"
            raise ValueError(msg)
        if not self.bot_user:
            # msg = "Event source has not finished setup"
            return
        if not self.bot_twitch:
            return
        channel_id = message.room.room_id
        channel_login = message.room.name
        channel_twitch = self._twitch_service.twitches.get(channel_id or "")
        if not channel_twitch:
            LOGGER.warning(
                f"Received a message from {channel_login}, but the bot has no authentication stored for this channel."
            )
            return
        await self.send_event(
            TwitchMessageEvent(
                data=message,
                text=message.text,
                id=message.id,
                author_login=message.user.name,
                author_name=message.user.display_name,
                author_id=message.user.id,
                author_roles=self._get_user_roles(message.user, channel_id),
                room_name=channel_login,
                room_id=channel_id,
                bot_user_id=self.bot_user.id,
                bot_user_login=self.bot_user.login,
                bot_user_name=self.bot_user.display_name,
                bot_twitch=self.bot_twitch,
                channel_twitch=channel_twitch,
                twitch_chat=message.chat,
            )
        )
