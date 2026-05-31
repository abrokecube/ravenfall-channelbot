from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast, overload, override

from bidict import bidict
from cachetools import TTLCache
from colorama import Back, Fore
from pydantic import Field
from sqlalchemy import select
from twitchAPI.chat import Chat
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.twitch import Twitch
from twitchAPI.type import AuthScope as TWAuthScope
from twitchAPI.type import (
    ChatEvent,
    EventSubSubscriptionConflict,
    InvalidTokenException,
    MissingScopeException,
)

from bot.core.components import BaseEventSource
from bot.db.session import get_async_session
from bot.integrations.chat_messages.enums import UserRole
from bot.integrations.twitch.exceptions import EventSubUnsubscriptionFailureError
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigModel, ConfigService

from . import MessageRateMode, TwitchChannel, events
from .enums import (
    TOPIC_REQUIRES_TARGET_CHANNEL,
    EventSubTopic,
    MessageDeliveryMode,
    MessageReceiveMode,
)
from .models import (
    ConnectedChat,
    EventSubChannelTopic,
    TwitchAuth,
    TwitchChannelSettings,
    TwitchCustomReward,
)
from .services import TwitchService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Collection

    from sqlalchemy.ext.asyncio import AsyncSession
    from twitchAPI.chat import ChatMessage, ChatUser
    from twitchAPI.object import eventsub
    from twitchAPI.object.api import TwitchUser

    from bot.core.components import EventManager

    from .models import EventSubRevocationDict

LOGGER = logging.getLogger(__name__)

AuthScope = TWAuthScope


def print_to_console(msg: str):
    """Print a message to the console."""
    print(  # noqa: T201
        f"{Fore.LIGHTYELLOW_EX}{Back.RESET}"
        f"twitch_event_source:{Fore.RESET} {msg}{Fore.RESET}{Back.RESET}"
    )


class TwitchConfig(ConfigModel):
    """Configuration for Twitch integration."""

    config_table_name: ClassVar[str | None] = "integrations.twitch"

    app_id: str
    app_secret: str
    bot_user_id: str
    bot_admin_uids: set[str] = Field(default_factory=set)


class TwitchEventSub:
    """Manages EventSub subscriptions for a channel."""

    def __init__(
        self,
        event_source: TwitchEventSource,
        twitch_channel: TwitchChannel,
        channel_id: str,
    ) -> None:
        self.event_source: TwitchEventSource = event_source
        self.twitch_channel: TwitchChannel = twitch_channel
        self.channel_id: str = channel_id
        self.eventsub_ws: EventSubWebsocket | None = None
        self.eventsub_subs: bidict[EventSubChannelTopic, str] = bidict()

    async def _eventsub_revocation_handler(self, thing: Any):  # pyright: ignore[reportAny, reportExplicitAny]
        data = cast("EventSubRevocationDict", thing)
        sub_id = data.get("subscription", {}).get("id")
        if sub_id not in self.eventsub_subs.inverse:
            LOGGER.warning(
                f"Revoked subscription id {sub_id} was not recorded during subscription"
            )
            return
        del self.eventsub_subs.inverse[sub_id]

    async def add_eventsub_subscription(
        self, topic: EventSubTopic, target_channel_id: str | None = None
    ):
        """Subscribe to an eventsub subscription."""
        if not self.eventsub_ws:
            self.eventsub_ws = EventSubWebsocket(
                self.twitch_channel.twitch,
                revocation_handler=self._eventsub_revocation_handler,
            )
            self.eventsub_ws.start()
        if topic not in TOPIC_REQUIRES_TARGET_CHANNEL:
            target_channel_id = None
        if topic in TOPIC_REQUIRES_TARGET_CHANNEL and not target_channel_id:
            msg = "target_channel is required for this topic"
            raise ValueError(msg)

        ch_t = EventSubChannelTopic(target_channel_id, topic)

        if target_channel_id is None:
            target_channel_id = ""

        if ch_t in self.eventsub_subs:
            return self.eventsub_subs[ch_t]
        sub_id = ""
        match topic:
            case EventSubTopic.CHANNEL_CHAT_MESSAGE:
                sub_id = await self.eventsub_ws.listen_channel_chat_message(
                    target_channel_id,
                    self.channel_id,
                    self.event_source._ev_on_chat_message,
                )
            case EventSubTopic.CHANNEL_POINTS_CUSTOM_REWARD_REDEMPTION_ADD:
                sub_id = await self.eventsub_ws.listen_channel_points_custom_reward_redemption_add(  # noqa: E501
                    self.channel_id,
                    self.event_source._ev_channel_points_custom_reward_redemption_add,
                )
        if sub_id:
            self.eventsub_subs[ch_t] = sub_id
        return sub_id

    @overload
    async def remove_eventsub_subscription(
        self,
        *,
        subscription_id: str = ...,
        topic: None = ...,
        target_channel_id: str | None = ...,
    ) -> EventSubChannelTopic: ...
    @overload
    async def remove_eventsub_subscription(
        self,
        *,
        subscription_id: None = ...,
        topic: EventSubTopic = ...,
        target_channel_id: str | None = ...,
    ) -> str: ...
    async def remove_eventsub_subscription(
        self,
        *,
        subscription_id: str | None = None,
        topic: EventSubTopic | None = None,
        target_channel_id: str | None = None,
    ):
        """Unsubscribe from an eventsub subscription."""
        if subscription_id and (topic or target_channel_id):
            msg = (
                "One of subscription_id and target_channel_id + "
                "subscription may be specified."
            )
            raise ValueError(msg)
        if not self.eventsub_ws:
            msg = "Twitch event source has not been initialized."
            raise RuntimeError(msg)

        if topic not in TOPIC_REQUIRES_TARGET_CHANNEL:
            target_channel_id = None
        if topic in TOPIC_REQUIRES_TARGET_CHANNEL and not target_channel_id:
            msg = "target_channel is required for this topic"
            raise ValueError(msg)

        if not subscription_id:
            if topic:
                if topic not in self.eventsub_subs:
                    msg = (
                        f"A matching subscription for "
                        f"{self.channel_id}:{topic}:{target_channel_id} was not found"
                    )
                    raise EventSubUnsubscriptionFailureError(msg)
                ch_t = EventSubChannelTopic(target_channel_id, topic)
                subscription_id = self.eventsub_subs[ch_t]
            else:
                msg = (
                    "channel_id and subscription must be specified "
                    "if subscription_id is None"
                )
                raise ValueError(msg)
        if subscription_id not in self.eventsub_subs.inv:
            msg = f"Subscription id {subscription_id} was not found"
            raise ValueError(msg)
        success = await self.eventsub_ws.unsubscribe_topic(subscription_id)
        if not success:
            msg = f"Failed to unsubscribe to {subscription_id}"
            raise EventSubUnsubscriptionFailureError(msg)
        _topic = self.eventsub_subs.inverse.pop(subscription_id)
        if topic is not None:
            return subscription_id
        return _topic

    async def unsubscribe_all(self):
        """Unsubscribe from all subscriptions."""
        if not self.eventsub_ws:
            return
        __ = self.eventsub_ws.unsubscribe_all()

    async def update_twitch(self, twitch_channel: TwitchChannel):
        """Update current twitch instance with new authentication."""
        if not self.eventsub_ws:
            return
        if (
            self.twitch_channel.twitch.get_user_auth_token()
            == twitch_channel.twitch.get_user_auth_token()
        ):
            return
        topics = list(self.eventsub_subs.keys())
        __ = self.eventsub_ws.stop()
        self.eventsub_subs.clear()
        self.twitch_channel = twitch_channel
        self.eventsub_ws = EventSubWebsocket(
            self.twitch_channel.twitch,
            revocation_handler=self._eventsub_revocation_handler,
        )
        self.eventsub_ws.start()
        for topic in topics:
            __ = await self.add_eventsub_subscription(topic.topic, topic.channel_id)

    async def stop(self):
        """Stop the event source."""
        if self.eventsub_ws:
            await self.eventsub_ws.stop()


_USER_CACHE_UNKNOWN_USER = -1


class TwitchEventSource(BaseEventSource, ConfigSubscriberMixin):
    """Event source for Twitch events. Includes a TwitchService."""

    def __init__(
        self,
        app_scopes: Collection[AuthScope] | None = None,
        bot_user_scopes: Collection[AuthScope] | None = None,
    ):
        super().__init__()
        self.app_id: str = ""
        self.app_secret: str = ""
        self.bot_user_id: str = ""
        self.twitch_chat: Chat | None = None
        self.bot_admin_uids: set[str] = set()
        self.app_scopes: list[AuthScope] = list(app_scopes or [])
        self.bot_user_scopes: list[AuthScope] = list(bot_user_scopes or [])
        self.bot_twitch: Twitch | None = None
        self._twitch_service: TwitchService = TwitchService(self)
        self._auth_lock: asyncio.Lock = asyncio.Lock()
        self.bot_user: TwitchUser | None = None
        self.bot_eventsub: TwitchEventSub | None = None
        self.eventsubs: dict[str, TwitchEventSub] = {}
        self.connected_chats: dict[str, ConnectedChat] = {}

        self.twitch_channels: dict[str, TwitchChannel] = {}
        self._user_cache: TTLCache[str, TwitchUser | Literal[-1]] = TTLCache[
            str, TwitchUser | Literal[-1]
        ](1024, 60 * 15)

    async def _save_user_token(
        self,
        user_id: str,
        user_login: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
    ):
        LOGGER.debug("Saving user token for %s", user_id)
        if access_token is not None:
            LOGGER.debug(
                "Access token: %s... (length %d)",
                access_token[:5],
                len(access_token),
            )
        if refresh_token is not None:
            LOGGER.debug(
                "Refresh token: %s... (length %d)",
                refresh_token[:5],
                len(refresh_token),
            )

        async with get_async_session() as session:
            result = await session.execute(
                select(TwitchAuth).where(TwitchAuth.user_id == user_id)
            )
            obj = result.scalar_one_or_none()
            if not obj:
                if not all((user_login, access_token, refresh_token)):
                    msg = (
                        "user_login, access_token, "
                        "and refresh_token must be supplied for new entries"
                    )
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
        self,
        access_token: str,  # pyright: ignore[reportUnusedParameter]
        refresh_token: str,  # pyright: ignore[reportUnusedParameter]
        *,
        user_id: str | None = None,
    ):
        LOGGER.debug("Ignoring token refresh callback for user_id %s", user_id)
        # if not user_id:
        #     return
        # await self._save_user_token(
        #     user_id, access_token=access_token, refresh_token=refresh_token
        # )

    async def _fetch_user_auth(self, user_id: str, session: AsyncSession):
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
    ) -> TwitchChannel:
        if not self.bot_twitch:
            msg = "Event source has not been initialized"
            raise RuntimeError(msg)
        if isinstance(user_id, int):
            user_id = str(user_id)
        if not scopes:
            scopes = []

        save_new_tokens = True
        access_token, refresh_token = None, None
        async with get_async_session() as session:
            twitch_auth = await self._fetch_user_auth(user_id, session)
            if twitch_auth is not None:
                access_token = twitch_auth.access_token
                refresh_token = twitch_auth.refresh_token
                if not user_name:
                    user_name = twitch_auth.user_name
                save_new_tokens = False

        if not twitch:
            if self.bot_twitch:
                twitch = self.bot_twitch.clone()
            else:
                twitch = await Twitch(
                    self.app_id,
                    self.app_secret,
                    authenticate_app=False,
                    target_app_auth_scope=self.app_scopes,
                )
        twitch_channel = TwitchChannel(twitch, user_id, scopes)

        while True:
            if access_token is None or refresh_token is None:
                auth = UserAuthenticator(self.bot_twitch, scopes, True)
                print_to_console(f"Auth scopes: {', '.join([x.value for x in scopes])}")
                print_to_console(
                    f"{Fore.LIGHTYELLOW_EX}Please authenticate with the Twitch account: "
                    f"{user_name or user_id}"
                )
                result = cast(
                    "tuple[str, str] | None", await auth.authenticate(use_browser=False)
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
                    user = await anext(twitch.get_users())
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
            except Exception as e:  # noqa: BLE001
                print_to_console(
                    f"{Fore.LIGHTRED_EX}Error setting user authentication: {e}"
                )
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue

            if user is not None:
                if user.id == str(user_id):
                    return twitch_channel
                print_to_console(
                    f"{Fore.LIGHTRED_EX}Token does not match user, please try again"
                )
                access_token = None
                refresh_token = None
                save_new_tokens = True
                continue
            return twitch_channel

    async def _fetch_channel_settings(
        self, channel_id: str, session: AsyncSession
    ) -> TwitchChannelSettings:
        db_result = await session.execute(
            select(TwitchChannelSettings).where(TwitchChannelSettings.id == channel_id)
        )
        result = db_result.scalar_one_or_none()
        if not result:
            result = TwitchChannelSettings(id=channel_id)
            session.add(result)
            await session.flush()
        return result

    async def authenticate_user(
        self,
        user_id: str,
        scopes: Collection[AuthScope],
        *,
        add_required_scopes: bool = False,
    ) -> TwitchChannel:
        """Authenticate a user.

        If a matching token is not found in the database, it will ask for authentication.
        """
        if not user_id or not user_id.isdigit():
            msg = f"{user_id} is not a valid Twitch user id"
            raise ValueError(msg)
        if add_required_scopes:
            scope_set = set(scopes)
            required_scopes = {
                AuthScope.CHANNEL_BOT,
                AuthScope.MODERATION_READ,
            }
            scopes = scope_set.union(required_scopes)
        t = await self._get_twitch_auth_instance(user_id, scopes=list(scopes))
        self.twitch_channels[user_id] = t
        if user_id in self.eventsubs:
            await self.eventsubs[user_id].update_twitch(t)
        return t

    def is_user_authenticated(self, user_id: str) -> bool:
        """Check if a user id has been authenticated."""
        return user_id in self.twitch_channels

    @override
    async def setup(self, event_manager: EventManager):
        config = await self.global_context.wait_for_service(ConfigService)
        self.inject_config_service(config)

        twitch_config = config.get_table(TwitchConfig)
        __ = self.subscribe_config(TwitchConfig)

        self.app_id = twitch_config.app_id
        self.app_secret = twitch_config.app_secret
        self.bot_user_id = twitch_config.bot_user_id
        self.bot_admin_uids = twitch_config.bot_admin_uids
        self.bot_twitch = await Twitch(
            self.app_id, self.app_secret, target_app_auth_scope=self.app_scopes
        )
        bot_channel = await self._get_twitch_auth_instance(
            self.bot_user_id, scopes=list(self.bot_user_scopes), twitch=self.bot_twitch
        )
        self.bot_twitch = bot_channel.twitch
        self.bot_user = await anext(self.bot_twitch.get_users())
        if self.bot_user:
            self.bot_eventsub = TwitchEventSub(
                self,
                TwitchChannel(self.bot_twitch, self.bot_user_id, self.app_scopes),
                self.bot_user.id,
            )

        self.twitch_chat = await Chat(self.bot_twitch)
        self.twitch_chat.start()
        self.twitch_chat.register_event(ChatEvent.MESSAGE, self._chat_on_message)
        await self.global_context.register_service(self._twitch_service)

    @override
    async def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ):
        if not isinstance(config, TwitchConfig):
            return
        self.bot_admin_uids = config.bot_admin_uids
        if len(changed_fields.difference({"bot_admin_uids"})) > 0:
            LOGGER.warning("Config changed, requires bot restart to apply changes.")

    @override
    async def teardown(self):
        if self.twitch_chat:
            self.twitch_chat.stop()
        tasks: list[Awaitable[None]] = []
        if self.bot_eventsub:
            tasks.append(self.bot_eventsub.stop())
        tasks.extend(x.twitch.close() for x in self.twitch_channels.values())
        tasks.extend(ev.stop() for ev in self.eventsubs.values())
        tasks.append(self._twitch_service.teardown())
        __ = await asyncio.gather(*tasks, return_exceptions=True)

    async def get_users(
        self, user_ids: list[str] | None = None, logins: list[str] | None = None
    ):
        """Gets information about one or more specified Twitch users."""
        if not self.bot_twitch:
            raise RuntimeError
        if user_ids is not None and logins is not None:
            msg = "Only either user_ids or logins can be specified, not both."
            raise ValueError(msg)
        if user_ids is None and logins is None:
            msg = "user_ids or logins must be specified."
            raise ValueError(msg)
        main_list: list[str] = []
        if user_ids:
            main_list = user_ids
        if logins:
            main_list = logins
        user_list: list[TwitchUser | None] = []
        for item in main_list:
            result = self._user_cache.get(item)
            if result != _USER_CACHE_UNKNOWN_USER:
                user_list.append(result)
            if result is None:
                self._user_cache[item] = _USER_CACHE_UNKNOWN_USER
        fetch_indeces: list[int] = [x for x, y in enumerate(user_list) if y is None]
        fetch_list: list[str] = [main_list[x] for x in fetch_indeces]
        if not fetch_list:
            for item in user_list:
                if item is not None:
                    yield item
            return

        if user_ids:
            user_ids = fetch_list
        if logins:
            logins = fetch_list

        for item in user_list:
            if item is not None:
                yield item

        async def _fetch_user_chunks():
            if not self.bot_twitch:
                return
            for i in range(0, len(fetch_list), 100):
                chunk = fetch_list[i : i + 100]
                async for u in self.bot_twitch.get_users(
                    user_ids=chunk if user_ids is not None else None,
                    logins=chunk if logins is not None else None,
                ):
                    yield u
                await asyncio.sleep(1)  # safety sleep

        async for u in _fetch_user_chunks():
            yield u
            self._user_cache[u.id] = u
            self._user_cache[u.login] = u

    async def join_chat(
        self,
        *,
        channel_id: str | Collection[str] | None = None,
        channel_name: str | Collection[str] | None = None,
        mode: MessageReceiveMode | None = None,
    ) -> list[str]:
        """Connect to a channel's chat."""
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
        async with get_async_session() as session:
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
                    result = cast(
                        "list[str]", await self.twitch_chat.join_room(user.login)
                    )
                    if result:
                        failed.extend(result)
                        has_failed = True
                elif settings.message_receive_mode == MessageReceiveMode.EVENTSUB:
                    try:
                        await self._add_eventsub_chat_message_subscription(settings)
                        settings.message_delivery_mode = MessageDeliveryMode.HELIX
                    except Exception:  # noqa: BLE001
                        LOGGER.warning(
                            "Failed to subscribe to eventsub chat", exc_info=True
                        )
                        failed.append(user.login)
                        has_failed = True
                if not has_failed:
                    self.connected_chats[user.id] = ConnectedChat(settings, user.login)
                    LOGGER.info(
                        f"Twitch: Connected to {user.login}:{user.id} "
                        f"with mode {settings.message_receive_mode}"
                    )
                idx += 1
            return failed

    async def leave_chat(
        self,
        *,
        channel_id: str | Collection[str] | None = None,
        channel_name: str | Collection[str] | None = None,
    ):
        """Disconnect from a channel's chat."""
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
        async for user in self.bot_twitch.get_users(
            logins=channel_name, user_ids=channel_id
        ):
            if not user:
                failed.append(channel_list[idx])
                continue
            if user.id not in self.connected_chats:
                LOGGER.info(f"Twitch: Not connected to {user.login}:{user.id}, skipping")
                continue
            settings = self.connected_chats[user.id]
            if settings.message_receive_mode == MessageReceiveMode.IRC:
                try:
                    await self.twitch_chat.leave_room(user.login)
                    del self.connected_chats[user.id]
                except Exception:  # noqa: BLE001
                    failed.append(channel_list[idx])
            elif settings.message_receive_mode == MessageReceiveMode.EVENTSUB:
                try:
                    await self.remove_eventsub_subscriptions(
                        channel_id=user.id,
                        topics=EventSubTopic.CHANNEL_CHAT_MESSAGE,
                    )
                    # remove_eventsub_subscriptions handles deleting
                    # from self.connected_chats
                except Exception:  # noqa: BLE001
                    failed.append(channel_list[idx])
        return failed

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
        channel_twitch = self.twitch_channels.get(channel_id or " ")
        if message.user.id == self.bot_user_id:
            settings = self.connected_chats[channel_id]
            if "moderator" in message.user.badges:  # pyright: ignore[reportUnknownMemberType]
                settings.message_rate = MessageRateMode.UPGRADED
            else:
                settings.message_rate = MessageRateMode.STANDARD

        if not channel_twitch:
            LOGGER.warning(
                f"Received a message from {channel_login}, "
                "but the bot has no authentication stored for this channel."
            )
            return
        await self.send_event(
            events.TwitchIRCMessageEvent(
                data=message,
                text=message.text,
                id=message.id,
                author_login=message.user.name,
                author_name=message.user.display_name,
                author_id=message.user.id,
                author_roles=self._get_user_roles(message.user, channel_id),
                room_name=channel_login,
                room_id=channel_id,
                channel_id=channel_id,
                channel_login=channel_login,
                channel_display_name=None,
                bot_user_id=self.bot_user.id,
                bot_user_login=self.bot_user.login,
                bot_user_name=self.bot_user.display_name,
                bot_twitch=self.bot_twitch,
                channel_twitch=channel_twitch,
                twitch_service=self._twitch_service,
            )
        )

    async def add_eventsub_subscriptions(
        self, channel_id: str, subscriptions: EventSubTopic | Collection[EventSubTopic]
    ):
        """Subscribe to eventsub subscriptions."""
        if not self.is_user_authenticated(channel_id):
            msg = "This channel has not been authenticated. Call authenticate_user first."
            raise ValueError(msg)
        if channel_id not in self.eventsubs:
            self.eventsubs[channel_id] = TwitchEventSub(
                self, self.twitch_channels[channel_id], channel_id
            )
        eventsub = self.eventsubs[channel_id]
        if isinstance(subscriptions, EventSubTopic):
            subscriptions = [subscriptions]
        else:
            subscriptions = list(subscriptions)
        for s in subscriptions:
            if s == EventSubTopic.CHANNEL_CHAT_MESSAGE:
                msg = "Use join_chat to subscribe to chat events"
                raise ValueError(msg)
            with contextlib.suppress(EventSubSubscriptionConflict):
                _ = await eventsub.add_eventsub_subscription(s)

    async def _add_eventsub_chat_message_subscription(
        self, channel_settings: TwitchChannelSettings
    ):
        if not self.bot_eventsub:
            msg = "bot_eventsub has not been initialized"
            raise RuntimeError(msg)
        eventsub = self.bot_eventsub
        # if not channel_settings.id in self.eventsubs:
        #     self.eventsubs[channel_settings.id] = TwitchEventSub(
        #         self,
        #         self.twitches[channel_settings.id],
        #         channel_settings.id,
        #     )
        # eventsub = self.eventsubs[channel_settings.id]
        _ = await eventsub.add_eventsub_subscription(
            EventSubTopic.CHANNEL_CHAT_MESSAGE, channel_settings.id
        )

    async def remove_eventsub_subscriptions(
        self,
        *,
        subscription_ids: str | Collection[str] | None = None,
        channel_id: str | None = None,
        topics: EventSubTopic | Collection[EventSubTopic] | None = None,
    ):
        """Unsubscribe from eventsub subscriptions."""
        if subscription_ids and (channel_id or topics):
            msg = (
                "One of subscription_ids and channel_id + subscriptions may be specified."
            )
            raise ValueError(msg)
        if channel_id not in self.eventsubs:
            return
        if channel_id and not self.is_user_authenticated(channel_id):
            return

        if subscription_ids and isinstance(subscription_ids, str):
            subscription_ids = [subscription_ids]
        if topics and isinstance(topics, EventSubTopic):
            topics = [topics]

        eventsub = self.eventsubs[channel_id]

        has_chat = False
        if subscription_ids:
            for sub_id in subscription_ids:
                topic = await eventsub.remove_eventsub_subscription(
                    subscription_id=sub_id
                )
                if topic == EventSubTopic.CHANNEL_CHAT_MESSAGE:
                    has_chat = True
        elif topics:
            has_chat = EventSubTopic.CHANNEL_CHAT_MESSAGE in topics
            for sub_topic in topics:
                __ = await eventsub.remove_eventsub_subscription(topic=sub_topic)

        if has_chat:
            if channel_id not in self.connected_chats:
                LOGGER.warning(
                    f"Unsubscribed to a chat message topic in {channel_id}, "
                    "but the subscription was not registered as a connected chat "
                    "beforehand"
                )
                return
            connected_chat = self.connected_chats[channel_id]
            if connected_chat.message_receive_mode != MessageReceiveMode.EVENTSUB:
                LOGGER.warning(
                    f"Unsubscribed to a chat message topic in {channel_id}, "
                    "but the message_receive_mode was not EventSub"
                )
                return
            del self.connected_chats[channel_id]

    def _get_user_roles_eventsub(
        self,
        user_id: str,
        room_id: str,
        badges: list[eventsub.ChatMessageBadge] | None = None,
    ):
        if not badges:
            badges = []
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
        channel_twitch = self.twitch_channels.get(data.broadcaster_user_id or "")
        if not channel_twitch:
            LOGGER.warning(
                f"Received a message from {data.broadcaster_user_login}, "
                "but the bot has no authentication stored for this channel."
            )
            return
        if event.event.chatter_user_id == self.bot_user_id:
            settings = self.connected_chats[event.event.broadcaster_user_id]
            for badge in event.event.badges:
                if badge.set_id == "moderator":
                    settings.message_rate = MessageRateMode.UPGRADED
                    break
            else:
                settings.message_rate = MessageRateMode.STANDARD
        await self.send_event(
            events.TwitchEventSubMessageEvent(
                data=data,
                text=data.message.text,
                id=data.message_id,
                author_login=data.chatter_user_login,
                author_name=data.chatter_user_name,
                author_id=data.chatter_user_id,
                author_roles=self._get_user_roles_eventsub(
                    data.chatter_user_id, data.broadcaster_user_id, data.badges
                ),
                room_id=data.broadcaster_user_id,
                room_name=data.broadcaster_user_login,
                channel_id=data.broadcaster_user_id,
                channel_login=data.broadcaster_user_login,
                channel_display_name=data.broadcaster_user_name,
                bot_user_id=self.bot_user.id,
                bot_user_login=self.bot_user.login,
                bot_user_name=self.bot_user.display_name,
                bot_twitch=self.bot_twitch,
                channel_twitch=channel_twitch,
                twitch_service=self._twitch_service,
            )
        )

    async def _ev_channel_points_custom_reward_redemption_add(
        self, event: eventsub.ChannelPointsCustomRewardRedemptionAddEvent
    ):
        if not self.bot_user:
            return
        if not self.bot_twitch:
            return
        data = event.event
        channel_twitch = self.twitch_channels.get(data.broadcaster_user_id or "")
        if not channel_twitch:
            LOGGER.warning(
                f"Received a message from {data.broadcaster_user_login}, "
                "but the bot has no authentication stored for this channel."
            )
            return
        async with get_async_session() as session:
            result = await session.execute(
                select(TwitchCustomReward.key).where(
                    TwitchCustomReward.channel_id == data.broadcaster_user_id,
                    TwitchCustomReward.reward_id == data.reward.id,
                )
            )
            internal_keys = tuple(result.scalars().all())
        await self.send_event(
            events.TwitchRedemptionEvent(
                data=data,
                text=data.user_input,
                id=data.id,
                author_login=data.user_login,
                author_name=data.user_name,
                author_id=data.user_id,
                author_roles=self._get_user_roles_eventsub(
                    data.user_id,
                    data.broadcaster_user_id,
                ),
                room_name=data.broadcaster_user_login,
                room_id=data.broadcaster_user_id,
                channel_id=data.broadcaster_user_id,
                channel_login=data.broadcaster_user_login,
                channel_display_name=data.broadcaster_user_name,
                bot_user_id=self.bot_user.id,
                bot_user_login=self.bot_user.login,
                bot_user_name=self.bot_user.display_name,
                bot_twitch=self.bot_twitch,
                channel_twitch=channel_twitch,
                twitch_service=self._twitch_service,
                redeem_name=data.reward.title,
                redeem_id=data.reward.id,
                redeem_cost=data.reward.cost,
                internal_keys=internal_keys,
            )
        )
