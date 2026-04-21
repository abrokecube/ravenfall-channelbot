from typing import override

from pydantic import BaseModel

from bot.core.components import Cog, EventManager
from bot.integrations.process_manager import ProcessManagerService
from bot.integrations.ravenfall import RavenfallService
from bot.integrations.ravenfall.event_sources import RavenfallInstance
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.mixins.event_receiver import EventReceiverMixin
from bot.services.config_service import ConfigService


class InstanceConfig(BaseModel):
    """Ravenfall instance config.

    'name' maps to 'twitch_login' on the ravenfall config
    """

    name: str


class WatcherConfig(BaseModel):
    """Ravenfall watcher cog config."""

    instances: list[InstanceConfig]


class RavenfallWatcher(EventReceiverMixin):
    """Watches a Ravenfall instance."""

    def __init__(
        self, ravenfall: RavenfallInstance, ravenfall_service: RavenfallService
    ) -> None:
        pass


class RavenfallWatcherCog(Cog, ConfigSubscriberMixin):
    """Manages Ravenfall's health."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)
        self.watchers: list[RavenfallWatcher] = []

    @override
    async def setup(self) -> None:
        proc_service = await self.global_context.wait_for_service(ProcessManagerService)
        config_service = await self.global_context.wait_for_service(ConfigService)
        ravenfall_service = await self.global_context.wait_for_service(RavenfallService)
        self.inject_config_service(config_service)

        self.watchers = [
            RavenfallWatcher(x, ravenfall_service)
            for x in ravenfall_service.get_all_ravenfall_instances()
        ]
