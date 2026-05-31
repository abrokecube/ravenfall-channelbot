from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, override

from bot.cogs.ravenfall_watcher.base_classes import RavenfallWatcherGroupCollector
from bot.core.components import Cog
from bot.integrations.chat_messages import UserRole, checks
from bot.integrations.chat_messages.utils import min_permission_level
from bot.integrations.commands import (
    CommandError,
    CommandEvent,
    MinPermissionLevel,
    RangeFloat,
    command,
    parameter,
)
from bot.integrations.process_manager import ProcessManagerService
from bot.integrations.ravenfall import (
    RavenfallInstance,
    RavenfallInstanceConverter,
    RavenfallService,
)
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigService
from bot.services.event_waiter import EventWaiterService
from bot.services.ravenfall_channels import RavenfallChannelService
from bot.services.ravenfall_multichat import RavenfallMultichatService
from utils.format_time import TimeSize, format_seconds
from utils.routines import routine

from . import collectors
from .config import WatcherConfig
from .service import RavenfallWatcherService
from .watcher import NoRestartTaskError, RavenfallWatcher, RestartCancelFailureError

if TYPE_CHECKING:
    from bot.core.components import EventManager

    from .base_classes import BaseGroupCollector

LOGGER = logging.getLogger(__name__)


class RavenfallWatcherCog(Cog, ConfigSubscriberMixin):
    """Manages Ravenfall's health."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)
        self.watchers: list[RavenfallWatcher] = []
        self.ravenfall_instance_to_watcher: dict[RavenfallInstance, RavenfallWatcher] = {}
        self.channel_name_to_watcher: dict[str, RavenfallWatcher] = {}
        self.alerting_collectors: list[BaseGroupCollector[RavenfallInstance]] = []
        self.non_alerting_collectors: list[BaseGroupCollector[RavenfallInstance]] = []
        self.config: WatcherConfig = WatcherConfig(
            instances=[], ravenfall_folder="", ravenbot_folder=""
        )
        self.restart_lock: asyncio.Lock = asyncio.Lock()

    @override
    async def setup(self) -> None:
        proc_service = await self.global_context.wait_for_service(ProcessManagerService)
        config_service = await self.global_context.wait_for_service(ConfigService)
        ravenfall_service = await self.global_context.wait_for_service(RavenfallService)
        __ = await self.global_context.wait_for_service(RavenfallChannelService)
        __ = await self.global_context.wait_for_service(EventWaiterService)
        multichat_service = await self.global_context.wait_for_service(
            RavenfallMultichatService
        )
        process_manager_service = await self.global_context.wait_for_service(
            ProcessManagerService
        )

        self.inject_config_service(config_service)

        config = self.subscribe_config(WatcherConfig)
        self.config = config

        self.watchers = []
        ravenfall_instances: list[RavenfallInstance] = []
        for instance in config.instances:
            ravenfall = ravenfall_service.get_ravenfall_instance(
                channel_name=instance.channel_name
            )
            if not ravenfall:
                LOGGER.warning(f"Instance {instance} not found, check config.")
                continue
            ravenfall_instances.append(ravenfall)
            watcher = RavenfallWatcher(
                ravenfall,
                self,
                instance,
                ravenfall_service,
                proc_service,
                self.event_manager,
                self.alerting_collectors,
            )
            await watcher.start()
            self.watchers.append(watcher)
            self.ravenfall_instance_to_watcher[ravenfall] = watcher
            self.channel_name_to_watcher[ravenfall.channel_name.lower()] = watcher

        self.alerting_collectors = [
            collectors.MultiplierCheck(
                ravenfall_instances, ravenfall_service, self.global_context
            ),
            collectors.ItemCountCheck(ravenfall_instances, multichat_service),
            collectors.RamUsageCheck(
                ravenfall_instances, process_manager_service, self, self.watchers
            ),
            collectors.RavenfallFrozenCheck(ravenfall_instances, self.global_context),
        ]
        self.non_alerting_collectors = [
            collectors.DesyncCheck(
                ravenfall_instances, multichat_service, self.global_context
            )
        ]

        watcher_service = RavenfallWatcherService(self)
        for c in self.alerting_collectors:
            if isinstance(c, RavenfallWatcherGroupCollector):
                c.inject_watcher_service(watcher_service)
            c.start()
        for c in self.non_alerting_collectors:
            if isinstance(c, RavenfallWatcherGroupCollector):
                c.inject_watcher_service(watcher_service)
            c.start()
        await self.global_context.register_service(watcher_service)
        __ = self.update_boosts_routine.start()

    @override
    async def teardown(self) -> None:
        for w in self.watchers:
            await w.stop()
        for c in self.alerting_collectors:
            c.stop()
        for c in self.non_alerting_collectors:
            c.stop()
        self.update_boosts_routine.stop()

    @routine(delta=timedelta(hours=3), wait_first=True)
    async def update_boosts_routine(self):
        """Refresh the boosts for each Ravenfall instance."""
        ravenfall_message_service = self.global_context.require_service(
            RavenfallChannelService
        )
        for watcher in self.watchers:
            if watcher.ravenfall_restart_lock.locked():
                async with watcher.ravenfall_restart_lock:
                    pass
            while True:
                village = await watcher.ravenfall.get_village()
                if not village:
                    await asyncio.sleep(10)
                else:
                    break
            boosts = village.boost
            if len(boosts) != 1:
                continue
            await ravenfall_message_service.send_channel_message(
                f"{watcher.config.ravenbot_prefix}town {boosts[0].skill.name.lower()}",
                watcher.config.channel_name,
            )
            await asyncio.sleep(120)

    def _get_watcher_or_error(self, instance: RavenfallInstance):
        result = self.ravenfall_instance_to_watcher.get(instance)
        if not result:
            msg = "This Ravenfall instance is not being monitored."
            raise CommandError(msg)
        return result

    def _check_permission(
        self,
        ctx: CommandEvent,
        instance: RavenfallInstance,
        min_role: UserRole = UserRole.BOT_ADMINISTRATOR,
    ):
        if instance.channel_id != ctx.message.room_id and not min_permission_level(
            ctx.message, min_role
        ):
            msg = "You do not have permission to specify an instance."
            raise CommandError(msg)

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @command("rfrestart status")
    async def rfrestartstatus(self, ctx: CommandEvent, *, instance: RavenfallInstance):
        """Get the auto-restart status of Ravenfall."""
        watcher = self._get_watcher_or_error(instance)
        if watcher.ravenfall_restart_lock.locked():
            await ctx.reply("Ravenfall is currently restarting.")
            return
        if watcher.restart_timeline.get_is_playing():
            seconds_left = watcher.restart_timeline.get_current_time()
            seconds_left_formatted = format_seconds(
                -seconds_left, TimeSize.LONG, 2, include_zero=False
            )
            restart_reason = watcher.restart_reason.rstrip(".")
            reply = (
                f"Ravenfall will restart in {seconds_left_formatted} "
                f"with reason: {restart_reason}."
            )
            await ctx.reply(reply)
            return
        if watcher.auto_restart_timer.get_is_running():
            seconds_left = await watcher.auto_restart_timer.get_time_remaining()
            if watcher.config.restart_warning_times:
                seconds_left += watcher.config.restart_warning_times[0]
            seconds_left_formatted = format_seconds(
                seconds_left, TimeSize.LONG, 2, include_zero=False
            )
            reply = f"Ravenfall is scheduled to restart in {seconds_left_formatted}."
            await ctx.reply(reply)
            return
        await ctx.reply("No active restart task.")

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter("seconds", converter=RangeFloat(0, None))
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    @command()
    async def rfrestart(
        self,
        ctx: CommandEvent,
        seconds: float = 30,
        *,
        reason: str = "Queued restart.",
        force: bool = False,
        instance: RavenfallInstance,
    ):
        """Queue a restart of Ravenfall."""
        self._check_permission(ctx, instance)
        watcher = self._get_watcher_or_error(instance)
        if force:
            await watcher.restart_ravenfall(reason=reason)
        if (
            watcher.restart_timeline.get_is_playing()
            and -watcher.restart_timeline.get_current_time() < seconds
        ):
            await ctx.reply("A restart has already been queued.")
            return
        if watcher.ravenfall_restart_lock.locked():
            raise CommandError("Ravenfall is currently restarting.")
        await watcher.queue_restart(seconds, reason)
        await ctx.reply("Restart queued.")

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    @command("rfrestart cancel")
    async def rfrestart_cancel(
        self,
        ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Cancel an active restart task."""
        self._check_permission(ctx, instance)
        watcher = self._get_watcher_or_error(instance)
        try:
            await watcher.cancel_restart()
        except RestartCancelFailureError:
            raise CommandError("Ravenfall is currently restarting.") from None
        except NoRestartTaskError:
            raise CommandError("There is no active restart task.") from None
        await ctx.reply("Restart canceled.")

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter("seconds", converter=RangeFloat(0, None))
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    @command("rfrestart postpone")
    async def rfrestart_postpone(
        self,
        ctx: CommandEvent,
        seconds: float = 30,
        *,
        instance: RavenfallInstance,
    ):
        """Postpone a restart task."""
        self._check_permission(ctx, instance)
        watcher = self._get_watcher_or_error(instance)
        try:
            await watcher.postpone_restart(seconds)
        except RestartCancelFailureError:
            raise CommandError("Ravenfall is currently restarting.") from None
        except NoRestartTaskError:
            raise CommandError("There is no active restart task.") from None
        await ctx.reply("Restart postponed.")

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    @command("rfrestart auto stop")
    async def rfrestart_stop(
        self,
        ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Stop auto-restarts from occurring."""
        self._check_permission(ctx, instance)
        watcher = self._get_watcher_or_error(instance)
        if watcher.ravenfall_restart_lock.locked():
            raise CommandError("Ravenfall is currently restarting.")
        if watcher.get_restarts_are_paused():
            raise CommandError("Auto-restarts are already paused")
        await watcher.pause_auto_restarts()
        await ctx.reply("Auto restarts are now paused.")

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    @command("rfrestart auto resume")
    async def rfrestart_resume(
        self,
        ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Resume auto-restarts."""
        self._check_permission(ctx, instance)
        watcher = self._get_watcher_or_error(instance)
        if not watcher.get_restarts_are_paused():
            raise CommandError("Auto-restarts are already active.")
        await watcher.resume_auto_restarts()
        await ctx.reply("Auto restarts have been resumed.")

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    @command("rfrestart bot")
    async def rfrestart_bot(
        self,
        ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Restart the instance's associated RavenBot."""
        self._check_permission(ctx, instance)
        watcher = self._get_watcher_or_error(instance)
        if watcher.ravenbot_restart_lock.locked():
            await ctx.reply("A restart is already underway.")
            return
        await watcher.restart_ravenbot()

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    @command()
    async def middleman_connection_status(
        self, ctx: CommandEvent, *, instance: RavenfallInstance
    ):
        """Check the connection status of the middleman."""
        self._check_permission(ctx, instance)
        watcher = self._get_watcher_or_error(instance)
        if not watcher.ravenfall.get_is_linked_to_middleman():
            raise CommandError("Ravenfall is not linked to a middleman.")
        conn_status = (await watcher.ravenfall.get_middleman_connection_status()).status
        time_formatted = format_seconds(
            conn_status.time_until_close, TimeSize.SMALL_SPACES, 2, include_zero=False
        )
        await ctx.reply(
            f"Client connected: {conn_status.client_connected} - "
            f"Server connected: {conn_status.server_connected} - "
            f"Time until close: {time_formatted}"
        )
