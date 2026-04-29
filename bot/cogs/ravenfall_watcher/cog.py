from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, override

from bot.core.components import Cog
from bot.integrations.commands import CommandError, CommandEvent, command  # noqa: TC001
from bot.integrations.process_manager import ProcessManagerService
from bot.integrations.ravenfall import RavenfallService
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigService
from bot.services.event_waiter import EventWaiterService
from bot.services.ravenfall_channels import RavenfallChannelService

from .config import WatcherConfig
from .watcher import RavenfallWatcher

if TYPE_CHECKING:
    from bot.core.components import EventManager
    from bot.integrations.ravenfall import RavenfallInstance

    from .base_classes import BaseGroupCollector

LOGGER = logging.getLogger(__name__)


class RavenfallWatcherCog(Cog, ConfigSubscriberMixin):
    """Manages Ravenfall's health."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)
        self.watchers: list[RavenfallWatcher] = []
        self.collectors: list[BaseGroupCollector[RavenfallInstance]] = []
        self.config: WatcherConfig = WatcherConfig(instances=[], ravenfall_folder="")
        self.restart_lock: asyncio.Lock = asyncio.Lock()

    @override
    async def setup(self) -> None:
        proc_service = await self.global_context.wait_for_service(ProcessManagerService)
        config_service = await self.global_context.wait_for_service(ConfigService)
        ravenfall_service = await self.global_context.wait_for_service(RavenfallService)
        __ = await self.global_context.wait_for_service(RavenfallChannelService)
        __ = await self.global_context.wait_for_service(EventWaiterService)
        self.inject_config_service(config_service)

        config = self.subscribe_config("cogs.ravenfall_watcher", WatcherConfig)
        self.config = config

        self.watchers = []
        for instance in config.instances:
            ravenfall = ravenfall_service.get_ravenfall_instance(
                channel_name=instance.channel_name
            )
            if not ravenfall:
                LOGGER.warning(f"Instance {instance} not found, check config.")
                continue
            watcher = RavenfallWatcher(
                ravenfall,
                self,
                instance,
                ravenfall_service,
                proc_service,
                self.event_manager,
                self.collectors,
            )
            await watcher.start()
            self.watchers.append(watcher)

        for c in self.collectors:
            c.start()

    @override
    async def teardown(self) -> None:
        for w in self.watchers:
            await w.stop()

    @command()
    async def rfrestartstatus(self, ctx: CommandEvent, instance_name: str):
        """Check if a ravenfall instance is currently in the restart process."""
        instance_name = instance_name.lower()
        for instance in self.watchers:
            if instance.config.channel_name.lower() == instance_name:
                if instance.restart_lock.locked():
                    await ctx.reply(f"{instance_name} is currently restarting.")
                else:
                    await ctx.reply(f"{instance_name} is not restarting.")
                break
        else:
            msg = "Instance not found."
            raise CommandError(msg)

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
