from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import TYPE_CHECKING

from bot.cogs.ravenfall_watcher.collectors import RestartBlocker
from bot.cogs.ravenfall_watcher.timer import SeekMode, Timeline, Timer
from bot.core.components import fire_and_forget
from bot.core.decorators import on_match
from bot.integrations.ravenfall import (
    RavenfallEvent,
    RavenfallOfflineEvent,
    RavenfallOnlineEvent,
    RavenfallReadyEvent,
)
from bot.mixins.event_receiver import EventReceiverMixin
from bot.services.event_waiter import EventWaiterService
from bot.services.ravenfall_channels import RavenfallChannelService
from utils.format_time import TimeSize, format_seconds

from .base_classes import BaseCollector

if TYPE_CHECKING:
    from collections.abc import Collection

    from bot.cogs.ravenfall_watcher.timer import EventContext
    from bot.core.components import BaseEvent, EventManager, GlobalContext
    from bot.integrations.process_manager import ProcessManagerService
    from bot.integrations.ravenfall import (
        RavenfallInstance,
        RavenfallService,
    )

    from .base_classes import BaseGroupCollector
    from .cog import RavenfallWatcherCog
    from .config import InstanceConfig

LOGGER = logging.getLogger(__name__)


class RavenfallWatcher(EventReceiverMixin):
    """Watches a Ravenfall instance."""

    def __init__(
        self,
        ravenfall: RavenfallInstance,
        watcher_cog: RavenfallWatcherCog,
        instance_config: InstanceConfig,
        ravenfall_service: RavenfallService,
        process_service: ProcessManagerService,
        event_manager: EventManager,
        group_collectors: Collection[BaseGroupCollector[RavenfallInstance]],
    ) -> None:
        self.ravenfall: RavenfallInstance = ravenfall
        self.config: InstanceConfig = instance_config
        self.watcher_cog: RavenfallWatcherCog = watcher_cog
        self.ravenfall_service: RavenfallService = ravenfall_service
        self.process_service: ProcessManagerService = process_service
        self.collectors: list[BaseCollector[RavenfallInstance]] = []
        self.global_ctx: GlobalContext = event_manager.global_context
        self.event_manager: EventManager = event_manager

        self._restart_blocker_collector: RestartBlocker = RestartBlocker(
            self.ravenfall, self.ravenfall_service
        )
        self.auto_restart_timer: Timer = Timer()
        self.restart_timeline: Timeline = Timeline(seek_mode=SeekMode.POINT)
        self.restart_reason: str = ""
        self.restart_lock: asyncio.Lock = asyncio.Lock()

        self.config.restart_warning_times.sort(reverse=True)

        for c in self.collectors:
            c.set_alert_callback(partial(self._collector_alerting, c))
            c.start()
        for c in group_collectors:
            c.set_alert_callback(
                self.ravenfall,
                partial(self._collector_alerting, c),
            )

    async def start(self):
        """Start the watcher, including setting up the restart timeline if configured."""
        await self.auto_restart_timer.register_callback(
            0, self._auto_restart_callback, from_end=True
        )
        if self.config.restart_warning_times:
            __ = await self.restart_timeline.add_event(
                -self.config.restart_warning_times[0],
                self.config.restart_warning_times[0],
                self._start_restart_blocker,
                self._stop_restart_blocker,
            )
            list_len = len(self.config.restart_warning_times)
            for x in range(list_len - 1):
                event_start = self.config.restart_warning_times[x]
                event_len = event_start - self.config.restart_warning_times[x + 1]
                end_callback = None
                if x == list_len - 2:
                    end_callback = self._announce_restart_countdown
                __ = await self.restart_timeline.add_event(
                    -event_start,
                    event_len,
                    self._announce_restart_countdown,
                    end_callback,
                )
        else:
            __ = await self.restart_timeline.add_event(
                -self.config.restart_unblock_min_seconds,
                self.config.restart_unblock_min_seconds,
                self._start_restart_blocker,
                self._stop_restart_blocker,
            )

        __ = await self.restart_timeline.add_event(
            0,
            0,
            self._execute_restart,
            None,
        )
        __ = await self.restart_timeline.add_event(
            -20,
            20,
            self._pre_restart,
            None,
        )
        self.inject_event_manager(self.event_manager)

    async def stop(self):
        """Stop the watcher and all its collectors."""
        for c in self.collectors:
            c.stop()
        await self.auto_restart_timer.stop()
        await self.restart_timeline.stop()

    async def _collector_alerting(
        self,
        collector: BaseCollector[RavenfallInstance]
        | BaseGroupCollector[RavenfallInstance],
    ):
        countdown_time = self.config.restart_unblock_min_seconds + 10
        if self.config.restart_warning_times:
            countdown_time = max(
                self.config.restart_warning_times[0],
                countdown_time,
            )
        if collector.is_urgent_failure:
            countdown_time = 5
        if isinstance(collector, BaseCollector):
            alert_reason = collector.get_alert_reason()
        else:
            alert_reason = collector.get_alert_reason(self.ravenfall)

        await self.queue_restart(countdown_time, alert_reason or "")

    async def _auto_restart_callback(self):
        countdown_time = self.config.restart_unblock_min_seconds + 10
        if self.config.restart_warning_times:
            countdown_time = max(
                self.config.restart_warning_times[0],
                countdown_time,
            )

        await self.queue_restart(countdown_time, "Scheduled auto-restart")

    async def queue_restart(self, countdown_seconds: float, reason: str = ""):
        """Queue a ravenfall restart with the specified countdown."""
        if self.restart_lock.locked():
            LOGGER.info(
                f"[{self.ravenfall.channel_name}] "
                "Restart already in progress, not queueing another."
            )
            return
        if self.restart_timeline.get_is_playing():
            if -countdown_seconds > await self.restart_timeline.get_current_time():
                await self.restart_timeline.seek(-countdown_seconds)
        else:
            await self.restart_timeline.start(-countdown_seconds, 0)
        self.restart_reason = reason

    async def _block_restart(self):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Blocking restart timeline")
        await self.restart_timeline.pause()

    async def _unblock_restart(self):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Unblocking restart timeline")
        if (
            -(await self.restart_timeline.get_current_time())
            <= self.config.restart_unblock_min_seconds
        ):
            await self.restart_timeline.seek(-self.config.restart_unblock_min_seconds)
        await self.restart_timeline.resume()

    async def _start_restart_blocker(self, _event_ctx: EventContext):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Starting restart blocker")
        self._restart_blocker_collector.set_alert_callback(self._block_restart)
        self._restart_blocker_collector.set_recovery_callback(self._unblock_restart)
        self._restart_blocker_collector.start()

    async def _stop_restart_blocker(self, _event_ctx: EventContext):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Stopping restart blocker")
        self._restart_blocker_collector.set_alert_callback(None)
        self._restart_blocker_collector.set_recovery_callback(None)
        self._restart_blocker_collector.stop()

    async def _announce_restart_countdown(self, event_ctx: EventContext):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Announcing restart countdown")
        await self._restart_blocker_collector.run_process_now()
        if not self._restart_blocker_collector.get_is_alerting():
            return
        channel = self.global_ctx.require_service(RavenfallChannelService)
        formatted_time = format_seconds(
            -event_ctx.current_time, TimeSize.LONG, 2, include_zero=False
        )
        await channel.send_global_message(
            f"Restarting Ravenfall in {formatted_time}!",
            "announcements.time_until_restart",
            self.ravenfall.channel_name,
        )

    async def _execute_restart(self, _event_ctx: EventContext):
        async with self.watcher_cog.restart_lock:
            await self.restart_ravenfall()

    async def _pre_restart(self, event_ctx: EventContext):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Pre-restart called")
        if event_ctx.event_progress - event_ctx.event_end < 1:
            return

    async def _post_restart(self):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Post-restart called")

    async def kill_ravenfall(self) -> bool:
        """Kills ravenfall.

        (The watcher will start ravenfall back up anyway)
        """
        LOGGER.info(f"[{self.ravenfall.channel_name}] Killed Ravenfall.")
        config = self.config
        result = await self.process_service.kill_process(
            "Ravenfall.exe", config.sandboxie_box_name
        )
        return result.code == 0

    async def restart_ravenfall(self, *, announce: bool = True):
        """Restarts ravenfall."""
        channel_service = self.global_ctx.get_service(RavenfallChannelService)
        if self.watcher_cog.restart_lock.locked() and announce and channel_service:
            await channel_service.send_global_message(
                "Waiting for other restart tasks to finish...",
                "announcements.waiting_for_restart",
                self.ravenfall.channel_name,
            )
        async with self.watcher_cog.restart_lock:
            async with self.restart_lock:
                LOGGER.info(f"[{self.ravenfall.channel_name}] Restarting Ravenfall.")
                if announce and channel_service:
                    await channel_service.send_global_message(
                        "Restarting Ravenfall...",
                        "announcements.restarting",
                        self.ravenfall.channel_name,
                    )
                config = self.config
                __ = await self.process_service.kill_process(
                    "Ravenfall.exe", config.sandboxie_box_name
                )
                code = 1
                while code != 0:
                    result = await self.process_service.spawn_process(
                        config.start_command,
                        config.sandboxie_box_name,
                        self.watcher_cog.config.ravenfall_folder,
                    )
                    code = result.code
                    await asyncio.sleep(10)

                def predicate(x: BaseEvent):
                    if not isinstance(x, RavenfallEvent):
                        return False
                    return x.ravenfall == self.ravenfall

                event_waiter = self.global_ctx.require_service(EventWaiterService)
                LOGGER.info(
                    f"[{self.ravenfall.channel_name}] Waiting for Ravenfall to come back online..."
                )
                __ = await event_waiter.wait_for(
                    RavenfallOnlineEvent, predicate=predicate, seconds_before=10
                )
                LOGGER.info(
                    f"[{self.ravenfall.channel_name}] Ravenfall is back online, waiting for it to be ready..."
                )

        try:
            async with asyncio.timeout(self.config.restart_timeout_seconds):
                __ = await event_waiter.wait_for(
                    RavenfallReadyEvent, predicate=predicate, seconds_before=5
                )
        except TimeoutError:
            LOGGER.info(f"[{self.ravenfall.channel_name}] Restart may have failed.")
            if channel_service:
                await channel_service.send_global_message(
                    (
                        "Failed to restart Ravenfall. "
                        f"{self.config.message_on_restart_timeout}"
                    ),
                    "announcements.restart_failed",
                    self.ravenfall.channel_name,
                )
            return
        await self._post_restart()

    @on_match(RavenfallOfflineEvent)
    async def on_offline(
        self, _g_ctx: GlobalContext, event: RavenfallOfflineEvent, _match: object
    ):
        """Runs when Ravenfall goes offline."""
        if self.restart_lock.locked():
            return
        if event.ravenfall == self.ravenfall:
            await self.restart_ravenfall()

    @on_match(RavenfallOnlineEvent)
    async def on_online(
        self, _g_ctx: GlobalContext, _event: RavenfallOnlineEvent, _match: object
    ):
        """Runs when Ravenfall goes online."""
        if self.config.auto_restart_period_seconds:
            try:
                uptime = await self.ravenfall.get_session()
            except Exception as e:
                LOGGER.warning(
                    f"[{self.ravenfall.channel_name}] Failed to fetch uptime: {e}"
                )
                return
            if not uptime:
                LOGGER.warning(f"[{self.ravenfall.channel_name}] Failed to fetch uptime.")
                return
            time_remaining = (
                self.config.auto_restart_period_seconds - uptime.seconds_since_start
            )
            await self.auto_restart_timer.stop()
            if time_remaining <= 0:
                # await self.queue_restart(10, "Scheduled auto-restart")
                fire_and_forget(self.restart_ravenfall())
                return
            LOGGER.info(
                f"[{self.ravenfall.channel_name}] Restarting in {format_seconds(time_remaining)} for scheduled auto-restart."
            )
            await self.auto_restart_timer.start(time_remaining)
