from __future__ import annotations

import logging
from typing import override

from pydantic import BaseModel

from bot.core.components import BaseService
from bot.integrations.chat_messages import GlobalMessengerService
from bot.integrations.ravenfall import RavenfallService
from bot.integrations.twitch.consts import EVENT_SOURCE_TWITCH
from bot.services.config_service import ConfigService

LOGGER = logging.getLogger(__name__)


class RavenfallChannelsConfig(BaseModel):
    """Ravenfall channel service config."""

    instances: list[RavenfallInstance] = []


class RavenfallInstance(BaseModel):
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


class RavenfallChannelService(BaseService):
    """Service for sending stuff to ravenfall channels idk."""

    def __init__(self) -> None:
        super().__init__()
        self.linked_channels: dict[str, list[RavenfallLinkedChannel]] = {}

    @override
    async def setup(self) -> None:
        config_service: ConfigService = await self.global_context.wait_for_service(
            ConfigService
        )
        __ = await self.global_context.wait_for_service(GlobalMessengerService)
        ravenfall_service: RavenfallService = await self.global_context.wait_for_service(
            RavenfallService
        )
        config = config_service.get_table(
            "services.ravenfall_channels", RavenfallChannelsConfig
        )
        a = {x.channel_name for x in ravenfall_service.event_source.ravenfall_instances}
        b = {x.twitch_login for x in config.instances}
        diff = b - a
        for item in diff:
            LOGGER.warning(f"Unknown channel '{item}'")

        for instance in config.instances:
            if instance.twitch_login in a:
                self.linked_channels[instance.twitch_login] = instance.channels

        for instance in ravenfall_service.event_source.ravenfall_instances:
            if instance.twitch_login not in self.linked_channels:
                continue
            has_twitch = False
            for channel in self.linked_channels[instance.twitch_login]:
                if (
                    channel.platform == EVENT_SOURCE_TWITCH
                    and channel.id == instance.channel_id
                ):
                    has_twitch = True

            if not has_twitch:
                self.linked_channels[instance.twitch_login].append(
                    RavenfallLinkedChannel(
                        platform=EVENT_SOURCE_TWITCH,
                        id=instance.channel_id,
                    )
                )

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
