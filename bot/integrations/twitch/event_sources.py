import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable, Collection
from sqlalchemy.ext.asyncio import AsyncSession
from twitchAPI.object import eventsub
from twitchAPI.twitch import Twitch
from typing import Any, Concatenate, Protocol, cast, override

from colorama import Back, Fore
from sqlalchemy import select
from twitchAPI import helper
from twitchAPI.chat import Chat, ChatMessage, ChatUser
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.object.api import TwitchUser
from twitchAPI.type import AuthScope as TWAuthScope
from twitchAPI.type import (
    InvalidTokenException,
    MissingScopeException,
    ChatEvent,
    EventSubSubscriptionConflict,
)
from twitchAPI.eventsub.websocket import EventSubWebsocket

from bot.core.components import BaseEventSource, EventManager
from bot.integrations.chat_messages.enums import UserRole
from bot.db.service import DatabaseService
from bot.integrations.twitch.exceptions import EventSubUnsubscriptionFailure

from . import TwitchAuth, TwitchChannelSettings
from .services import TwitchService
from .events import TwitchEventSubMessageEvent, TwitchMessageEvent
from .enums import MessageReceiveMode, EventSubTopic
from .models import EventSubChannelTopic, EventSubRevocationDict, ConnectedChat
import contextlib
from bidict import bidict

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
        self.eventsub_ws: EventSubWebsocket | None = None
        self.eventsub_subs: bidict[EventSubChannelTopic, str] = bidict()
        self.connected_chats: dict[str, ConnectedChat] = {}

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

    async def _fetch_channel_settings(
        self, channel_id: str, session: AsyncSession
    ) -> TwitchChannelSettings:
        db_result = await session.execute(
            select(TwitchChannelSettings).where(TwitchChannelSettings.id == channel_id)
        )
        result = db_result.scalar_one_or_none()
        if not result:
            result = TwitchChannelSettings(id=channel_id)
            await session.flush()
        return result

    async def authenticate_user(
        self, user_id: str, scopes: Collection[AuthScope]
    ) -> Twitch:
        """Authenticate a user. If a matching token is not found in the database, it will ask for authentication."""
        t = await self._get_twitch_auth_instance(user_id, scopes=list(scopes))
        self._twitch_service.twitches[user_id] = t
        return t

    def is_user_authenticated(self, user_id: str) -> bool:
        """Check if a user id has been authenticated."""
        return user_id in self._twitch_service.twitches

    @override
    async def setup(self, event_manager: EventManager):
        __ = await self.global_context.wait_for_service(DatabaseService)
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

    async def join_chat(
        self,
        *,
        channel_id: str | Collection[str] | None = None,
        channel_name: str | Collection[str] | None = None,
        mode: MessageReceiveMode | None = None,
    ) -> list[str]:
        """Connect to a channel's chat."""
        db = self.global_context.require_service(DatabaseService)
        if (not self.twitch_chat) or (not self.bot_twitch):
            msg = "Twitch event source has not been initialized."
            raise RuntimeError(msg)
        if channel_id is not None and channel_name is not None:
            msg = "Only one of channel_id and channel_name can be specified"
            raise ValueError(msg)
        channel_list = []
        if channel_name is not None:
            if isinstance(channel_name, str):
                channel_name = [channel_name]
            else:
                channel_name = list(channel_name)
            channel_list = channel_name
        if channel_id is not None:
            if isinstance(channel_id, str):
                channel_id = [channel_id]
            else:
                channel_id = list(channel_id)
            channel_list = channel_id
        failed: list[str] = []
        idx = 0
        async with db.get_session() as session:
            async for user in self.bot_twitch.get_users(
                logins=channel_name, user_ids=channel_id
            ):
                if not user:
                    failed.append(channel_list[idx])
                    continue
                if user.id in self.connected_chats:
                    LOGGER.info(
                        f"Twitch: Already connected to {user.login}:{user.id}, skipping"
                    )
                    continue
                settings = await self._fetch_channel_settings(user.id, session)
                if mode:
                    settings.message_receive_mode = mode
                has_failed = False
                if settings.message_receive_mode == MessageReceiveMode.IRC:
                    result = cast(list[str], await self.twitch_chat.join_room(user.login))
                    if result:
                        failed.extend(result)
                        has_failed = True
                elif settings.message_receive_mode == MessageReceiveMode.EVENTSUB:
                    try:
                        await self._add_eventsub_chat_message_subscription(settings)
                    except:
                        failed.append(user.login)
                        has_failed = True
                if not has_failed:
                    self.connected_chats[user.id] = ConnectedChat(settings)
                idx += 1
            return failed

    async def leave_chat(
        self,
        *,
        channel_id: str | Collection[str] | None = None,
        channel_name: str | Collection[str] | None = None,
    ):
        if (not self.twitch_chat) or (not self.bot_twitch):
            msg = "Twitch event source has not been initialized."
            raise RuntimeError(msg)
        if channel_id is not None and channel_name is not None:
            msg = "Only one of channel_id and channel_name can be specified"
            raise ValueError(msg)
        if channel_name is not None:
            if isinstance(channel_name, str):
                channel_name = [channel_name]
            else:
                channel_name = list(channel_name)
        if channel_id is not None:
            if isinstance(channel_id, str):
                channel_id = [channel_id]
            else:
                channel_id = list(channel_id)

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
        channel_twitch = self._twitch_service.twitches.get(channel_id or " ")
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
                twitch_service=self._twitch_service,
            )
        )

    async def _eventsub_revocation_handler(self, thing: Any):  # pyright: ignore[reportAny, reportExplicitAny]
        data = cast(EventSubRevocationDict, thing)
        sub_id = data.get("subscription", {}).get("id")
        if not sub_id in self.eventsub_subs.inverse:
            LOGGER.warning(
                f"Revoked subscription id {sub_id} was not recorded during subscription"
            )
            return
        del self.eventsub_subs.inverse[sub_id]

    async def add_eventsub_subscriptions(
        self, channel_id: str, subscriptions: EventSubTopic | Collection[EventSubTopic]
    ):
        """Subscribe to eventsub subscriptions."""
        if not self.bot_twitch:
            msg = "Twitch event source has not been initialized."
            raise RuntimeError(msg)
        if not self.eventsub_ws:
            self.eventsub_ws = EventSubWebsocket(
                self.bot_twitch, revocation_handler=self._eventsub_revocation_handler
            )
            self.eventsub_ws.start()
        if not self.is_user_authenticated(channel_id):
            msg = "This channel has not been authenticated. Call authenticate_user first."
            raise ValueError(msg)
        if isinstance(subscriptions, EventSubTopic):
            subscriptions = [subscriptions]
        else:
            subscriptions = list(subscriptions)
        for s in subscriptions:
            with contextlib.suppress(EventSubSubscriptionConflict):
                await self._add_eventsub_subscription(channel_id, s)

    async def _add_eventsub_subscription(
        self, channel_id: str, subscription: EventSubTopic
    ):
        """Subscribe to an eventsub subscription."""
        if not self.eventsub_ws:
            msg = "Twitch event source has not been initialized."
            raise RuntimeError(msg)
        ch_t = EventSubChannelTopic(channel_id=channel_id, topic=subscription)
        if ch_t in self.eventsub_subs:
            return
        sub_id = ""
        match subscription:
            case EventSubTopic.CHANNEL_CHAT_MESSAGE:
                # sub_id = await self.eventsub_ws.listen_channel_chat_message(
                #     channel_id, self.bot_user_id, self._ev_on_chat_message
                # )
                msg = (
                    "Use TwitchEventSource.join_chat to subscribe to chat message events."
                )
                raise ValueError(msg)
        if sub_id:
            self.eventsub_subs[ch_t] = sub_id

    async def _add_eventsub_chat_message_subscription(
        self, channel_settings: TwitchChannelSettings
    ):
        if not self.eventsub_ws:
            msg = "Twitch event source has not been initialized."
            raise RuntimeError(msg)
        ch_t = EventSubChannelTopic(
            channel_id=channel_settings.id, topic=EventSubTopic.CHANNEL_CHAT_MESSAGE
        )
        if ch_t in self.eventsub_subs:
            return
        sub_id = await self.eventsub_ws.listen_channel_chat_message(
            channel_settings.id, self.bot_user_id, self._ev_on_chat_message
        )
        if sub_id:
            self.eventsub_subs[ch_t] = sub_id

    async def _remove_eventsub_subscription(self, subscription_id: str):
        if not self.eventsub_ws:
            msg = "Twitch event source has not been initialized."
            raise RuntimeError(msg)
        if not subscription_id in self.eventsub_subs.inv:
            msg = f"Subscription id {subscription_id} was not found"
            raise ValueError(msg)
        success = await self.eventsub_ws.unsubscribe_topic(subscription_id)
        if not success:
            msg = f"Failed to unsubscribe to {subscription_id}"
            raise EventSubUnsubscriptionFailure(msg)
        removed_topic = self.eventsub_subs.inverse.pop(subscription_id)
        if removed_topic.topic == EventSubTopic.CHANNEL_CHAT_MESSAGE:
            if not removed_topic.channel_id in self.connected_chats:
                LOGGER.warning(
                    f"Unsubscribed to a chat message topic in {removed_topic.channel_id}, but the subscription was not registered as a connected chat beforehand"
                )
                return
            connected_chat = self.connected_chats[removed_topic.channel_id]
            if connected_chat.message_receive_mode != MessageReceiveMode.EVENTSUB:
                LOGGER.warning(
                    f"Unsubscribed to a chat message topic in {removed_topic.channel_id}, but the message_receive_mode was not EventSub"
                )
                return
            del self.connected_chats[removed_topic.channel_id]

    def _get_user_roles_eventsub(
        self, user_id: str, room_id: str, badges: list[eventsub.ChatMessageBadge]
    ):
        roles: set[UserRole] = set()
        if user_id in self.bot_admin_uids:
            roles.add(UserRole.BOT_ADMINISTRATOR)
        if user_id == room_id:
            roles.add(UserRole.ADMINISTRATOR)

        for b in badges:
            match b.set_id:
                case "moderator":
                    roles.add(UserRole.MODERATOR)
                case "lead_moderator":
                    roles.add(UserRole.ADMINISTRATOR)
                case _:
                    pass

        roles.add(UserRole.USER)
        return roles

    async def _ev_on_chat_message(self, event: eventsub.ChannelChatMessageEvent):
        if not self.bot_user:
            return
        if not self.bot_twitch:
            return
        data = event.event
        channel_twitch = self._twitch_service.twitches.get(data.broadcaster_user_id or "")
        if not channel_twitch:
            LOGGER.warning(
                f"Received a message from {data.broadcaster_user_login}, but the bot has no authentication stored for this channel."
            )
            return
        await self.send_event(
            TwitchEventSubMessageEvent(
                data=data,
                text=data.message.text,
                id=data.message_id,
                author_login=data.chatter_user_login,
                author_name=data.chatter_user_name,
                author_id=data.chatter_user_id,
                author_roles=self._get_user_roles_eventsub(
                    data.chatter_user_id, data.broadcaster_user_id, data.badges
                ),
                room_name=data.broadcaster_user_login,
                room_id=data.broadcaster_user_id,
                bot_user_id=self.bot_user.id,
                bot_user_login=self.bot_user.login,
                bot_user_name=self.bot_user.display_name,
                bot_twitch=self.bot_twitch,
                channel_twitch=channel_twitch,
                twitch_service=self._twitch_service,
            )
        )
