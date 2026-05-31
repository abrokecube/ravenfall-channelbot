from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from types import NoneType
from typing import TYPE_CHECKING, ClassVar, NamedTuple, override

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from twitchAPI.object.api import TwitchUser  # noqa: TC002

from bot.clients.ravenfall_middleman import Sender
from bot.clients.ravenfall_query import Player
from bot.core.components import BaseService
from bot.core.decorators import on_match
from bot.db import Base
from bot.db.session import get_async_session
from bot.integrations.chat_messages import GlobalMessengerService, MessageEvent, UserRole
from bot.integrations.ravenfall import RavenfallService
from bot.integrations.twitch.consts import EVENT_SOURCE_TWITCH
from bot.integrations.twitch.services import TwitchService
from bot.mixins.event_receiver import EventReceiverMixin
from bot.services.config_service import ConfigModel, ConfigService
from bot.services.ravenfall_multichat import RavenfallMultichatService
import ravenpy

if TYPE_CHECKING:
    from collections.abc import Collection

    from bot.cogs.accounts.service import AccountService
    from bot.core.components import EventManager, GlobalContext
    from bot.integrations.ravenfall import RavenfallInstance

LOGGER = logging.getLogger(__name__)


class SenderData(Base):
    """Database model for storing Ravenfall sender information."""

    __tablename__: str = "ravenfall_sender_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_platform: Mapped[str] = mapped_column(String)
    channel_platform_id: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(String, nullable=True)  # Ravenfall User ID
    character_id: Mapped[str] = mapped_column(String, nullable=True)  # Ravenfall Char ID
    username: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String)
    color: Mapped[str | None] = mapped_column(String, nullable=True)
    platform: Mapped[str] = mapped_column(String)
    platform_id: Mapped[str] = mapped_column(String)
    is_broadcaster: Mapped[bool] = mapped_column(Boolean, default=False)
    is_moderator: Mapped[bool] = mapped_column(Boolean, default=False)
    is_subscriber: Mapped[bool] = mapped_column(Boolean, default=False)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)
    is_game_administrator: Mapped[bool] = mapped_column(Boolean, default=False)
    is_game_moderator: Mapped[bool] = mapped_column(Boolean, default=False)
    sub_tier: Mapped[int] = mapped_column(Integer, default=0)
    identifier: Mapped[str | None] = mapped_column(String, nullable=True)


class CharacterData(Base):
    __tablename__: str = "ravenfall_character_data"

    char_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String)
    char_index: Mapped[int] = mapped_column(String)
    twitch_id: Mapped[str] = mapped_column(String)

    twitch_username: Mapped[str] = mapped_column(String)
    identifier: Mapped[str] = mapped_column(String)
    is_moderator: Mapped[bool] = mapped_column(Boolean)
    is_admin: Mapped[bool] = mapped_column(Boolean)


class RavenfallChannelsConfig(ConfigModel):
    """Ravenfall channel service config."""

    config_table_name: ClassVar[str | None] = "services.ravenfall_channels"

    instances: list[ChannelRavenfallInstance] = Field(default_factory=list)


class ChannelRavenfallInstance(BaseModel):
    """One Ravenfall instance."""

    twitch_login: str
    channels: list[RavenfallLinkedChannel]


class RavenfallLinkedChannel(BaseModel):
    """External channel.

    Used by external integrations.
    """

    platform: str
    id: str
    categories: set[str] = set()
    exclude_categories: set[str] = set()
    is_primary: bool = False


class TupleOfAllTime(NamedTuple):
    """Tuple of all time."""

    platform: str
    ravenfall: RavenfallInstance


class CallbackSettings(NamedTuple):
    """Settings for a callback."""

    instances: set[RavenfallInstance] | None = None


type MessageCallback = Callable[[MessageEvent, RavenfallInstance], Awaitable[None]]


class RavenfallChannelService(BaseService, EventReceiverMixin):
    """Service for sending stuff to ravenfall channels idk."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__()
        self.linked_channels: dict[str, list[RavenfallLinkedChannel]] = {}
        self.event_manager: EventManager = event_manager
        self.message_event_callbacks: set[MessageCallback] = set()
        self.callback_settings: dict[MessageCallback, CallbackSettings] = {}
        self._channel_id_matcher: dict[str, list[TupleOfAllTime]] = defaultdict(list)
        self.char_data_fetch_lock: asyncio.Lock = asyncio.Lock()

    @override
    async def setup(self) -> None:
        config_service: ConfigService = await self.global_context.wait_for_service(
            ConfigService
        )
        __ = await self.global_context.wait_for_service(GlobalMessengerService)
        from bot.integrations.ravenfall import RavenfallService

        ravenfall_service: RavenfallService = await self.global_context.wait_for_service(
            RavenfallService
        )
        config = config_service.get_table(RavenfallChannelsConfig)
        a = {x.channel_name for x in ravenfall_service.event_source.ravenfall_instances}
        b = {x.twitch_login for x in config.instances}
        diff = b - a
        for item in diff:
            LOGGER.warning(f"Unknown channel '{item}'")

        for instance in config.instances:
            if instance.twitch_login in a:
                self.linked_channels[instance.twitch_login] = instance.channels
            else:
                self.linked_channels[instance.twitch_login] = []

        for instance in ravenfall_service.event_source.ravenfall_instances:
            has_twitch = False
            has_primary = False
            for channel in self.linked_channels[instance.twitch_login]:
                if channel.is_primary:
                    if not has_primary:
                        has_primary = True
                    else:
                        msg = (
                            "Multiple primary channels for "
                            f"Ravenfall instance {instance.twitch_login}."
                        )
                        raise ValueError(msg)

            for channel in self.linked_channels[instance.twitch_login]:
                if (
                    channel.platform == EVENT_SOURCE_TWITCH
                    and channel.id == instance.channel_id
                ):
                    has_twitch = True
                    channel.is_primary = not has_primary
                    has_primary = True
                    break

            if not has_twitch:
                self.linked_channels[instance.twitch_login].append(
                    RavenfallLinkedChannel(
                        platform=EVENT_SOURCE_TWITCH,
                        id=instance.channel_id,
                        is_primary=not has_primary,
                    )
                )
        self.inject_event_manager(self.event_manager)

        for twitch_login, channels in self.linked_channels.items():
            ravenfall_instance = ravenfall_service.get_ravenfall_instance(
                channel_name=twitch_login
            )
            if not ravenfall_instance:
                continue
            for channel in channels:
                self._channel_id_matcher[channel.id].append(
                    TupleOfAllTime(
                        platform=channel.platform,
                        ravenfall=ravenfall_instance,
                    )
                )

        await self.fill_missing_character_data()

    def get_channels(self, instance_name: str) -> list[RavenfallLinkedChannel]:
        """Get the channels for a Ravenfall instance."""
        if instance_name not in self.linked_channels:
            msg = f"Instance {instance_name} not found."
            raise ValueError(msg)
        return self.linked_channels[instance_name]

    async def send_global_message(
        self,
        text: str,
        category: str,
        instance_name: str,
    ):
        """Send a message to channels linked to a Ravenfall instance."""
        if instance_name not in self.linked_channels:
            return
        messenger = self.global_context.require_service(GlobalMessengerService)
        instance = self.linked_channels[instance_name]

        category_split = tuple(category.split("."))
        categories = [
            ".".join(category_split[: x + 1]) for x in range(len(category_split))
        ]
        for channel in instance:
            if len(channel.exclude_categories) > 0 and any(
                x in channel.exclude_categories for x in categories
            ):
                continue
            if len(channel.categories) == 0 or any(
                x in channel.categories for x in categories
            ):
                __ = await messenger.send(text, channel.platform, channel.id)

    async def get_primary_channel(self, instance_name: str) -> RavenfallLinkedChannel:
        """Get the primary channel for a Ravenfall instance."""
        if instance_name not in self.linked_channels:
            msg = f"Instance {instance_name} not found."
            raise ValueError(msg)
        instance = self.linked_channels[instance_name]
        for channel in instance:
            if channel.is_primary:
                return channel
        msg = f"Instance {instance_name} has no primary channel."
        raise ValueError(msg)

    async def send_channel_message(self, text: str, instance_name: str):
        """Send a message to the primary channel linked to a Ravenfall instance."""
        if instance_name not in self.linked_channels:
            msg = f"Instance {instance_name} not found."
            raise ValueError(msg)
        messenger = self.global_context.require_service(GlobalMessengerService)
        instance = self.linked_channels[instance_name]
        for channel in instance:
            if channel.is_primary:
                __ = await messenger.send(text, channel.platform, channel.id)
                break
        else:
            msg = f"No primary channel found for instance {instance_name}."
            raise ValueError(msg)

    def register_message_event_callback(
        self,
        callback: MessageCallback,
        ravenfall_channel_names: str | Collection[str] | None = None,
    ):
        """Register a callback for message events."""
        from bot.integrations.ravenfall import RavenfallService

        ravenfall_service = self.global_context.require_service(RavenfallService)

        self.message_event_callbacks.add(callback)
        if isinstance(ravenfall_channel_names, str):
            ravenfall_channel_names = [ravenfall_channel_names]
        if ravenfall_channel_names is None:
            ravenfall_channel_names = []
        ravenfall_instances: set[RavenfallInstance] | None = set()
        for name in ravenfall_channel_names:
            instance = ravenfall_service.get_ravenfall_instance(channel_name=name)
            if not instance:
                continue
            # pyrefly: ignore [missing-attribute]
            ravenfall_instances.add(instance)
        if not ravenfall_instances:
            ravenfall_instances = None
        self.callback_settings[callback] = CallbackSettings(instances=ravenfall_instances)

    def unregister_message_event_callback(self, callback: MessageCallback):
        """Unregister a callback for message events."""
        self.message_event_callbacks.discard(callback)
        __ = self.callback_settings.pop(callback, None)

    def get_matching_instance_for_message_event(self, event: MessageEvent):
        """Get the Ravenfall instance associated with the message event's channel."""
        if event.room_id not in self._channel_id_matcher:
            return None
        possible_instances = self._channel_id_matcher[event.room_id]
        matching_instance: RavenfallInstance | None = None
        for item in possible_instances:
            if item.platform == event.platform:
                matching_instance = item.ravenfall
                break
        if not matching_instance:
            return None
        return matching_instance

    @on_match(MessageEvent)
    async def _on_message_event(
        self, _g_ctx: GlobalContext, event: MessageEvent, _match: object
    ):
        matching_instance = self.get_matching_instance_for_message_event(event)
        if not matching_instance:
            return
        tasks: list[Awaitable[object]] = []
        for callback in self.message_event_callbacks:
            callback_setting = self.callback_settings[callback]
            if (
                callback_setting.instances is None
                or matching_instance in callback_setting.instances
            ):
                tasks.append(callback(event, matching_instance))
        if event.platform == EVENT_SOURCE_TWITCH:
            tasks.append(self._get_sender_data_from_twitch_msg_event(event))
        __ = await asyncio.gather(*tasks)

    async def _get_sender_data_from_twitch_msg_event(
        self, event: MessageEvent
    ) -> SenderData | None:
        """Save a Sender from a MessageEvent."""
        if event.platform != EVENT_SOURCE_TWITCH:
            return None

        from bot.integrations.twitch.events import (
            TwitchEventSubMessageEvent,
            TwitchIRCMessageEvent,
        )

        if isinstance(event, TwitchIRCMessageEvent):
            badges = event.data.user.badges  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            is_broadcaster = "broadcaster" in badges
            is_moderator = "moderator" in badges or is_broadcaster
            is_subscriber = "subscriber" in badges or "founder" in badges
            is_vip = "vip" in badges
            color = event.data.user.color
            sub_tier = 0
            if is_subscriber:
                sub_tier = 1
        elif isinstance(event, TwitchEventSubMessageEvent):
            # badges is a list of Badge objects
            badges_dict = {b.set_id: "1" for b in event.data.badges}
            is_broadcaster = "broadcaster" in badges_dict
            is_moderator = "moderator" in badges_dict or is_broadcaster
            is_subscriber = "subscriber" in badges_dict or "founder" in badges_dict
            is_vip = "vip" in badges_dict
            color = event.data.color
            sub_tier = 0
            if is_subscriber:
                sub_tier = 1
        else:
            return None

        async with get_async_session() as session:
            # Find if already exists
            stmt = select(SenderData).where(
                SenderData.channel_platform_id == event.room_id,
                SenderData.platform_id == event.author_id,
            )
            res = await session.execute(stmt)
            sender_data: SenderData | None = res.scalar_one_or_none()

            if not sender_data:
                sender_data = SenderData(
                    channel_platform=EVENT_SOURCE_TWITCH,
                    channel_platform_id=event.room_id,
                    platform="twitch",
                    platform_id=event.author_id,
                    username=event.author_login,
                    display_name=event.author_name,
                )
                session.add(sender_data)

            # Update details
            sender_data.username = event.author_login
            sender_data.display_name = event.author_name
            sender_data.color = color
            sender_data.is_broadcaster = is_broadcaster
            sender_data.is_moderator = is_moderator
            sender_data.is_subscriber = is_subscriber
            sender_data.is_vip = is_vip
            sender_data.sub_tier = sub_tier

            return sender_data

    async def get_sender_from_message_event_user(self, event: MessageEvent) -> Sender:
        """Convert a MessageEvent into a Ravenfall Sender object.

        Args:
            event: The MessageEvent to convert.

        Returns:
            A Sender object.

        Raises:
            ValueError: If the user has no connected Twitch account.
        """
        from bot.cogs.accounts.service import AccountService

        account_service: AccountService = self.global_context.require_service(
            AccountService
        )

        is_twitch = event.platform == EVENT_SOURCE_TWITCH

        if is_twitch:
            sender_data = await self._get_sender_data_from_twitch_msg_event(event)
            if not sender_data:
                raise ValueError("Sender data not found for Twitch message event")

            return Sender(
                id=sender_data.platform_id,
                character_id=sender_data.character_id or "",
                username=sender_data.username,
                display_name=sender_data.display_name,
                color=sender_data.color,
                platform=sender_data.platform,
                platform_id=sender_data.platform_id,
                is_broadcaster=sender_data.is_broadcaster,
                is_moderator=sender_data.is_moderator,
                is_subscriber=sender_data.is_subscriber,
                is_vip=sender_data.is_vip,
                is_game_administrator=sender_data.is_game_administrator,
                is_game_moderator=sender_data.is_game_moderator,
                sub_tier=sender_data.sub_tier,
                identifier=sender_data.identifier,
            )
        async with get_async_session() as session:
            account = await account_service.get_or_create_account(
                session,
                event.platform,
                event.author_id,
                event.author_login,
                event.author_name,
            )
            links = await account_service.get_account_links(
                session, account.id, EVENT_SOURCE_TWITCH
            )
        if not links:
            msg = f"User {event.author_login} has no connected Twitch account."
            raise ValueError(msg)

        twitch_link = links[0]
        for link in links:
            if link.is_primary:
                twitch_link = link
                break

        async with get_async_session() as session:
            stmt = select(SenderData).where(
                SenderData.channel_platform_id == event.room_id,
                SenderData.platform_id == twitch_link.platform_id,
            )
            res = await session.execute(stmt)
            sender_data = res.scalar_one_or_none()

            if sender_data:
                return Sender(
                    id=sender_data.platform_id,
                    character_id=sender_data.character_id or "",
                    username=sender_data.username,
                    display_name=sender_data.display_name,
                    color=sender_data.color,
                    platform=sender_data.platform,
                    platform_id=sender_data.platform_id,
                    is_broadcaster=sender_data.is_broadcaster,
                    is_moderator=sender_data.is_moderator,
                    is_subscriber=sender_data.is_subscriber,
                    is_vip=sender_data.is_vip,
                    is_game_administrator=sender_data.is_game_administrator,
                    is_game_moderator=sender_data.is_game_moderator,
                    sub_tier=sender_data.sub_tier,
                    identifier=sender_data.identifier,
                )

            return Sender(
                id=twitch_link.platform_id,
                character_id="",
                username=twitch_link.username,
                display_name=twitch_link.display_name or twitch_link.username,
                color=None,
                platform=EVENT_SOURCE_TWITCH,
                platform_id=twitch_link.platform_id,
                is_broadcaster=False,
                is_moderator=False,
                is_subscriber=False,
                is_vip=False,
                is_game_administrator=False,
                is_game_moderator=False,
                sub_tier=0,
                identifier=None,
            )

    async def _get_sender_data_from_twitch_id(
        self, channel_id: str, twitch_id: str
    ) -> SenderData | None:
        sender_data = None
        async with get_async_session() as session:
            stmt = select(SenderData).where(
                SenderData.channel_platform_id == channel_id,
                SenderData.platform_id == twitch_id,
            )
            res = await session.execute(stmt)
            sender_data = res.scalar_one_or_none()
        if sender_data:
            return sender_data

        twitch_srv = self.global_context.require_service(TwitchService)
        user = await anext(twitch_srv.get_users(user_ids=[twitch_id]), None)
        if user:
            return SenderData(
                channel_platform_id=channel_id,
                platform_id=user.id,
                platform="twitch",
                username=user.login,
                display_name=user.display_name,
                color="#000000",
                is_broadcaster=False,
                is_moderator=False,
                is_subscriber=False,
                is_vip=False,
                is_game_administrator=False,
                is_game_moderator=False,
                sub_tier=0,
                identifier=None,
            )
        return None

    async def _get_sender_data_from_twitch_username(
        self,
        channel_id: str,
        username: str,
        *,
        case_sensitive: bool = False,
        search_display_names: bool = False,
    ) -> SenderData | None:
        """Get SenderData from a Twitch username.

        Args:
            channel_id: The room id.
            username: The Twitch username to search for.
            case_sensitive: Whether the username search should be case sensitive.
            search_display_names: Whether to also search display names.

        Returns:
            A SenderData object if found, None otherwise.
        """
        async with get_async_session() as session:
            if case_sensitive:
                stmt = select(SenderData).where(
                    SenderData.channel_platform_id == channel_id,
                    SenderData.username == username,
                )
            else:
                stmt = select(SenderData).where(
                    SenderData.channel_platform_id == channel_id,
                    SenderData.username.ilike(username),
                )
            res = await session.execute(stmt)
            sender_data = res.scalar_one_or_none()

            if sender_data:
                return sender_data

            if search_display_names:
                if case_sensitive:
                    stmt = select(SenderData).where(
                        SenderData.channel_platform_id == channel_id,
                        SenderData.display_name == username,
                    )
                else:
                    stmt = select(SenderData).where(
                        SenderData.channel_platform_id == channel_id,
                        SenderData.display_name.ilike(username),
                    )
                res = await session.execute(stmt)
                sender_data = res.scalar_one_or_none()

                if sender_data:
                    return sender_data

        twitch_srv = self.global_context.require_service(TwitchService)
        user = await anext(twitch_srv.get_users(logins=[username]), None)
        if user:
            return SenderData(
                channel_platform_id=channel_id,
                platform_id=user.id,
                platform="twitch",
                username=user.login,
                display_name=user.display_name,
                color="#000000",
                is_broadcaster=False,
                is_moderator=False,
                is_subscriber=False,
                is_vip=False,
                is_game_administrator=False,
                is_game_moderator=False,
                sub_tier=0,
                identifier=None,
            )
        return None

    async def get_sender_by_twitch_id(
        self, channel_id: str, twitch_id: str
    ) -> Sender | None:
        """Get Sender from a twitch user id.

        Args:
            channel_id: The room id.
            twitch_id: The user twitch account id.

        Returns:
            A Sender object.
        """
        sender_data = await self._get_sender_data_from_twitch_id(channel_id, twitch_id)
        if not sender_data:
            return None

        return Sender(
            id=sender_data.platform_id,
            character_id=sender_data.character_id or "",
            username=sender_data.username,
            display_name=sender_data.display_name,
            color=sender_data.color,
            platform=sender_data.platform,
            platform_id=sender_data.platform_id,
            is_broadcaster=sender_data.is_broadcaster,
            is_moderator=sender_data.is_moderator,
            is_subscriber=sender_data.is_subscriber,
            is_vip=sender_data.is_vip,
            is_game_administrator=sender_data.is_game_administrator,
            is_game_moderator=sender_data.is_game_moderator,
            sub_tier=sender_data.sub_tier,
            identifier=sender_data.identifier,
        )

    async def get_sender_from_twitch_username(
        self,
        channel_id: str,
        username: str,
        *,
        case_sensitive: bool = False,
        search_display_names: bool = False,
    ) -> Sender | None:
        """Get Sender from a twitch username.

        Args:
            channel_id: The room id.
            username: The user twitch username.
            case_sensitive: Whether to search case sensitive.
            search_display_names: Whether to search display names.

        Returns:
            A Sender object.
        """
        sender_data = await self._get_sender_data_from_twitch_username(
            channel_id,
            username,
            case_sensitive=case_sensitive,
            search_display_names=search_display_names,
        )
        if not sender_data:
            return None

        return Sender(
            id=sender_data.platform_id,
            character_id=sender_data.character_id or "",
            username=sender_data.username,
            display_name=sender_data.display_name,
            color=sender_data.color,
            platform=sender_data.platform,
            platform_id=sender_data.platform_id,
            is_broadcaster=sender_data.is_broadcaster,
            is_moderator=sender_data.is_moderator,
            is_subscriber=sender_data.is_subscriber,
            is_vip=sender_data.is_vip,
            is_game_administrator=sender_data.is_game_administrator,
            is_game_moderator=sender_data.is_game_moderator,
            sub_tier=sender_data.sub_tier,
            identifier=sender_data.identifier,
        )

    async def get_sender_from_username(
        self,
        channel_id: str,
        username: str,
        platform: str,
        *,
        case_sensitive: bool = False,
        search_display_names: bool = False,
    ) -> Sender | None:
        """Get Sender from a username on a specified platform.

        Searches for users on the specified platform first, and if not found,
        searches the "twitch" platform. Utilizes the AccountService for finding
        user links to Twitch. If the platform is not "twitch" and it fails to
        find a matching Sender, returns None.

        Args:
            channel_id: The room id.
            username: The username to search for.
            platform: The platform to search on first.
            case_sensitive: Whether the username search should be case sensitive.
            search_display_names: Whether to also search display names.

        Returns:
            A Sender object if found, None otherwise.
        """
        if platform == EVENT_SOURCE_TWITCH:
            return await self.get_sender_from_twitch_username(
                channel_id,
                username,
                case_sensitive=case_sensitive,
                search_display_names=search_display_names,
            )

        from bot.cogs.accounts.service import AccountService

        account_service: AccountService = self.global_context.require_service(
            AccountService
        )

        async with get_async_session() as session:
            link = await account_service.find_link_by_username(
                session, platform, username, case_sensitive=case_sensitive
            )

            if not link and search_display_names:
                link = await account_service.find_link_by_display_name(
                    session, platform, username, case_sensitive=case_sensitive
                )

            if link:
                twitch_links = await account_service.get_account_links(
                    session, link.account_id, EVENT_SOURCE_TWITCH
                )
                if twitch_links:
                    twitch_link = twitch_links[0]
                    return await self.get_sender_by_twitch_id(
                        channel_id, twitch_link.platform_id
                    )

        if platform != EVENT_SOURCE_TWITCH:
            return await self.get_sender_from_twitch_username(
                channel_id,
                username,
                case_sensitive=case_sensitive,
                search_display_names=search_display_names,
            )

        return None

    async def fill_missing_character_data(self):
        """Fetches all missing character data."""
        ravenfall_srv = self.global_context.require_service(RavenfallService)
        ravennest = ravenfall_srv.get_ravennest()
        while True:
            tasks = [x.get_players() for x in ravenfall_srv.get_all_ravenfall_instances()]
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            if all(isinstance(x, list) for x in task_results):
                break
            LOGGER.warning(
                "Couldn't get data for all towns to fill missing char data, "
                "retrying in 5s"
            )
            await asyncio.sleep(5)

        players: list[Player] = []
        async with self.char_data_fetch_lock:
            for r in task_results:
                if not isinstance(r, list):
                    continue
                players.extend(r)
            players_dict = {x.id: x for x in players}
            async with get_async_session() as session:
                res = await session.execute(
                    select(CharacterData.char_id).where(
                        CharacterData.char_id.in_(players_dict.keys())
                    )
                )
                for char_id in res.scalars():
                    del players_dict[char_id]

            if len(players_dict) == 0:
                return

            semaphore = asyncio.Semaphore(5)

            async def char_fetch_task(char_id: str):
                """Fetch a ravenfall character."""
                async with semaphore:
                    return await ravennest.get_character_from_id(char_id)

            fetched_chars = await asyncio.gather(
                *[char_fetch_task(x) for x in players_dict.keys()], return_exceptions=True
            )
            counter = 0
            async with get_async_session() as session:
                for char_data in fetched_chars:
                    if not isinstance(char_data, ravenpy.Character):
                        continue
                    char_data_db = CharacterData(
                        char_id=char_data.char_id,
                        user_id=char_data.user_id,
                        char_index=char_data.index,
                        twitch_id=char_data.twitch_id,
                        twitch_username=char_data.user_name,
                        identifier=char_data.identifier,
                        is_moderator=char_data.is_moderator,
                        is_admin=char_data.is_admin,
                    )
                    session.add(char_data_db)
                    counter += 1
            LOGGER.info(f"Saved {counter} new characters.")

    async def refresh_char_data(self, char_id: str) -> None:
        """Refresh stored data for a character.

        Args:
            char_id: The Ravenfall character ID.
        """
        if self.char_data_fetch_lock.locked():
            async with self.char_data_fetch_lock:
                pass

        ravenfall_srv = self.global_context.require_service(RavenfallService)
        ravennest = ravenfall_srv.get_ravennest()
        char_data = await ravennest.get_character_from_id(char_id)
        if not char_data:
            return

        async with get_async_session() as session:
            stmt = select(CharacterData).where(CharacterData.char_id == char_id)
            res = await session.execute(stmt)
            char_data_db = res.scalar_one_or_none()

            if char_data_db:
                char_data_db.user_id = char_data.user_id
                char_data_db.char_index = char_data.index
                char_data_db.twitch_id = char_data.twitch_id
                char_data_db.twitch_username = char_data.user_name
                char_data_db.identifier = char_data.identifier
                char_data_db.is_moderator = char_data.is_moderator
                char_data_db.is_admin = char_data.is_admin
            else:
                char_data_db = CharacterData(
                    char_id=char_data.char_id,
                    user_id=char_data.user_id,
                    char_index=char_data.index,
                    twitch_id=char_data.twitch_id,
                    twitch_username=char_data.user_name,
                    identifier=char_data.identifier,
                    is_moderator=char_data.is_moderator,
                    is_admin=char_data.is_admin,
                )
                session.add(char_data_db)

    async def get_character_data_by_char_id(
        self, char_ids: list[str], session: AsyncSession
    ) -> list[CharacterData]:
        """Get CharacterData objects for a list of character IDs.

        If any of the characters are not in the database, fetches them from the
        server, creates a new CharacterData object, and adds them to the session.

        Args:
            char_ids: A list of character IDs to retrieve.
            session: The database session to use.

        Returns:
            A list of CharacterData objects matching the requested character IDs.
        """
        if not char_ids:
            return []

        stmt = select(CharacterData).where(CharacterData.char_id.in_(char_ids))
        res = await session.execute(stmt)
        existing_char_datas = list(res.scalars())

        existing_dict = {c.char_id: c for c in existing_char_datas}

        missing_ids = [cid for cid in char_ids if cid not in existing_dict]

        if missing_ids:
            if self.char_data_fetch_lock.locked():
                async with self.char_data_fetch_lock:
                    pass

            ravenfall_srv = self.global_context.require_service(RavenfallService)
            ravennest = ravenfall_srv.get_ravennest()

            async def fetch_one(cid: str) -> ravenpy.Character | None:
                try:
                    return await ravennest.get_character_from_id(cid)
                except Exception:
                    LOGGER.exception(f"Failed to fetch character data for ID {cid}")
                    return None

            fetched = await asyncio.gather(*(fetch_one(cid) for cid in missing_ids))

            for char_data in fetched:
                if char_data:
                    char_data_db = CharacterData(
                        char_id=char_data.char_id,
                        user_id=char_data.user_id,
                        char_index=char_data.index,
                        twitch_id=char_data.twitch_id,
                        twitch_username=char_data.user_name,
                        identifier=char_data.identifier,
                        is_moderator=char_data.is_moderator,
                        is_admin=char_data.is_admin,
                    )
                    session.add(char_data_db)
                    existing_dict[char_data.char_id] = char_data_db

        return [existing_dict[cid] for cid in char_ids if cid in existing_dict]

    async def get_character_data_by_twitch_id(
        self, twitch_ids: list[str], session: AsyncSession
    ) -> list[CharacterData]:
        """Get CharacterData objects for a list of character IDs.

        If any of the characters are not in the database, fetches them from the
        server, creates a new CharacterData object, and adds them to the session.

        Args:
            twitch_ids: A list of twitch IDs to retrieve.
            session: The database session to use.

        Returns:
            A list of CharacterData objects matching the requested Twitch IDs.
        """
        if not twitch_ids:
            return []
        _char_count = 3
        stmt = select(CharacterData).where(CharacterData.twitch_id.in_(twitch_ids))
        res = await session.execute(stmt)
        existing_char_datas = list(res.scalars())

        existing_dict = defaultdict[str, list[CharacterData]](list)
        for c in existing_char_datas:
            existing_dict[c.twitch_id].append(c)

        missing_ids = [tid for tid in twitch_ids if tid not in existing_dict]

        if missing_ids:
            if self.char_data_fetch_lock.locked():
                async with self.char_data_fetch_lock:
                    pass

            ravenfall_srv = self.global_context.require_service(RavenfallService)
            ravennest = ravenfall_srv.get_ravennest()

            async def fetch_three(tid: str) -> tuple[ravenpy.Character | None, ...]:
                results = await asyncio.gather(
                    ravennest.get_character(tid, 1),
                    ravennest.get_character(tid, 2),
                    ravennest.get_character(tid, 3),
                    return_exceptions=True,
                )
                out: tuple[ravenpy.Character | None, ...] = tuple(
                    x for x in results if isinstance(x, (ravenpy.Character, NoneType))
                )
                if len(out) < _char_count:
                    LOGGER.exception(
                        f"Failed to fetch all character data for ID {tid} "
                        f"(got {len(out)})"
                    )
                return out

            fetched = await asyncio.gather(*(fetch_three(cid) for cid in missing_ids))

            for char_set in fetched:
                for char_data in char_set:
                    if char_data:
                        char_data_db = CharacterData(
                            char_id=char_data.char_id,
                            user_id=char_data.user_id,
                            char_index=char_data.index,
                            twitch_id=char_data.twitch_id,
                            twitch_username=char_data.user_name,
                            identifier=char_data.identifier,
                            is_moderator=char_data.is_moderator,
                            is_admin=char_data.is_admin,
                        )
                        session.add(char_data_db)
                        existing_dict[char_data.twitch_id].append(char_data_db)

        return [
            c for cid in twitch_ids if cid in existing_dict for c in existing_dict[cid]
        ]

    async def send_multichat_command(
        self, text: str, instance_name: str, *, admin: bool = False
    ):
        """Send a command to ravenfall-multichat as the instance twitch user."""
        if instance_name not in self.linked_channels:
            msg = "Not a registered instance."
            raise ValueError(msg)
        multichat_srv = self.global_context.require_service(RavenfallMultichatService)
        ravenfall_srv = self.global_context.require_service(RavenfallService)
        instance = self.linked_channels[instance_name]
        instance_r = ravenfall_srv.get_ravenfall_instance(channel_name=instance_name)
        if not instance_r:
            msg = "No matching ravenfall instance"
            raise ValueError(msg)
        multichat = multichat_srv.get_client()
        output_channel_id = instance_r.channel_id
        for channel in instance:
            if channel.platform != EVENT_SOURCE_TWITCH:
                continue
            if channel.is_primary:
                output_channel_id = channel.id
        if not admin:
            await multichat.send_multichat_command(
                text,
                instance_r.channel_id,
                instance_r.channel_name,
                instance_r.channel_id,
                instance_r.channel_name,
                output_to_channel_id=output_channel_id,
            )
        else:
            await multichat.send_multichat_command(
                text,
                "0",
                "",
                instance_r.channel_id,
                instance_r.channel_name,
                output_to_channel_id=output_channel_id,
            )
