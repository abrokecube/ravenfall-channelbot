from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, ClassVar, NamedTuple, override

from pydantic import BaseModel, Field

from bot.core.components import BaseService
from bot.core.decorators import on_match
from bot.integrations.chat_messages import GlobalMessengerService, MessageEvent
from bot.integrations.twitch.consts import EVENT_SOURCE_TWITCH
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.mixins.event_receiver import EventReceiverMixin
from bot.services.config_service import ConfigModel, ConfigService

if TYPE_CHECKING:
    from collections.abc import Collection

    from bot.core.components import EventManager, GlobalContext
    from bot.integrations.ravenfall import RavenfallInstance

LOGGER = logging.getLogger(__name__)


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

    async def get_channels(self, instance_name: str) -> list[RavenfallLinkedChannel]:
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
        tasks: list[Awaitable[None]] = []
        for callback in self.message_event_callbacks:
            callback_setting = self.callback_settings[callback]
            if (
                callback_setting.instances is None
                or matching_instance in callback_setting.instances
            ):
                tasks.append(callback(event, matching_instance))
        __ = await asyncio.gather(*tasks)
