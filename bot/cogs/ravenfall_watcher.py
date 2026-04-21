from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, override

from pydantic import BaseModel

from bot.core.components import Cog, GlobalContext
from bot.core.decorators import on_match
from bot.integrations.commands import CommandError, CommandEvent, command
from bot.integrations.process_manager import ProcessManagerService
from bot.integrations.ravenfall import RavenfallOfflineEvent, RavenfallService
from bot.integrations.ravenfall.event_sources import RavenfallInstance
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.mixins.event_receiver import EventReceiverMixin
from bot.services.config_service import ConfigService

if TYPE_CHECKING:
    from bot.core.components import EventManager
    from bot.integrations.ravenfall.event_sources import RavenfallInstance

LOGGER = logging.getLogger(__name__)


class InstanceConfig(BaseModel):
    """Ravenfall instance config.

    'channel_name' maps to 'twitch_login' on the ravenfall config
    """

    channel_name: str
    sandboxie_box_name: str | None = None
    start_command: str


class WatcherConfig(BaseModel):
    """Ravenfall watcher cog config."""

    instances: list[InstanceConfig]
    ravenfall_folder: str


class RavenfallWatcher(EventReceiverMixin):
    """Watches a Ravenfall instance."""

    def __init__(
        self,
        ravenfall: RavenfallInstance,
        watcher_config: WatcherConfig,
        instance_config: InstanceConfig,
        ravenfall_service: RavenfallService,
        process_service: ProcessManagerService,
        event_manager: EventManager,
    ) -> None:
        self.ravenfall: RavenfallInstance = ravenfall
        self.config: InstanceConfig = instance_config
        self.watcher_config: WatcherConfig = watcher_config
        self.ravenfall_service: RavenfallService = ravenfall_service
        self.process_service: ProcessManagerService = process_service
        self.inject_event_manager(event_manager)

    async def kill_ravenfall(self) -> bool:
        """Kills ravenfall.

        (Ravenfall will start back up anyway)
        """
        config = self.config
        result = await self.process_service.kill_process(
            "Ravenfall.exe", config.sandboxie_box_name
        )
        return result.code == 0

    async def restart_ravenfall(self):
        """Restarts ravenfall."""
        config = self.config
        __ = await self.process_service.kill_process(
            "Ravenfall.exe", config.sandboxie_box_name
        )
        code = 1
        while code != 0:
            result = await self.process_service.spawn_process(
                config.start_command,
                config.sandboxie_box_name,
                self.watcher_config.ravenfall_folder,
            )
            code = result.code
            await asyncio.sleep(10)

    @on_match(RavenfallOfflineEvent)
    async def on_offline(
        self, _g_ctx: GlobalContext, _event: RavenfallOfflineEvent, _match: object
    ):
        """Runs when Ravenfall goes offline."""
        await self.restart_ravenfall()


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

        config = self.subscribe_config("cogs.ravenfall_watcher", WatcherConfig)

        self.watchers = []
        for instance in config.instances:
            ravenfall = ravenfall_service.get_ravenfall_instance(
                channel_name=instance.channel_name
            )
            if not ravenfall:
                LOGGER.warning(f"Instance {instance} not found, check config.")
                continue
            self.watchers.append(
                RavenfallWatcher(
                    ravenfall,
                    config,
                    instance,
                    ravenfall_service,
                    proc_service,
                    self.event_manager,
                )
            )

    @command()
    async def test_restart_proc(self, ctx: CommandEvent, instance_name: str):
        """Restart a ravenfall instance."""
        instance_name = instance_name.lower()
        for instance in self.watchers:
            if instance.config.channel_name.lower() == instance_name:
                await ctx.reply(f"Restarting {instance_name}...")
                await instance.restart_ravenfall()
                await ctx.reply("Restart complete.")
                break
        else:
            msg = "Instance not found."
            raise CommandError(msg)

    @command()
    async def test_kill_proc(self, ctx: CommandEvent, instance_name: str):
        """Kill a ravenfall instance."""
        instance_name = instance_name.lower()
        for instance in self.watchers:
            if instance.config.channel_name.lower() == instance_name:
                await ctx.reply(f"Kill {instance_name}...")
                result = await instance.kill_ravenfall()
                if result:
                    await ctx.reply("Done.")
                else:
                    msg = "Kill failed."
                    raise CommandError(msg)
                break
        else:
            msg = "Instance not found."
            raise CommandError(msg)
