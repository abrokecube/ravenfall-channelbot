from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from functools import partial
from time import monotonic
from typing import TYPE_CHECKING

from bot.cogs.ravenfall_watcher.base_classes import BaseGroupCollector
from bot.core.components import fire_and_forget
from bot.core.decorators import on_match
from bot.integrations.ravenfall import (
    RavenfallEvent,
    RavenfallOfflineEvent,
    RavenfallOnlineEvent,
    RavenfallReadyEvent,
)
from bot.integrations.ravenfall.event_sources import RavenfallInstance
from bot.mixins.event_receiver import EventReceiverMixin
from bot.services.event_waiter import EventTypePredicate, EventWaiterService
from bot.services.ravenfall_channels import RavenfallChannelService
from bot.services.ravenfall_multichat import RavenfallMultichatService
from utils.format_time import TimeSize, format_seconds

from . import collectors
from .base_classes import BaseCollector
from .timeline import SeekMode, Timeline
from .timer import Timer

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Coroutine

    from bot.core.components import BaseEvent, EventManager, GlobalContext
    from bot.integrations.process_manager import ProcessManagerService
    from bot.integrations.ravenfall import (
        RavenfallInstance,
        RavenfallService,
    )

    from .base_classes import BaseGroupCollector
    from .cog import RavenfallWatcherCog
    from .config import InstanceConfig
    from .timeline import EventInfo, TimelineEvent

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

        self._restart_blocker_collector: collectors.RestartBlocker = (
            collectors.RestartBlocker(self.ravenfall, self.ravenfall_service, watcher_cog)
        )
        self.auto_restart_timer: Timer = Timer()
        self.restart_timeline: Timeline = Timeline()
        self.restart_timeline.set_seek_mode(SeekMode.POINT)
        self.restart_reason: str = ""
        self.restart_lock: asyncio.Lock = asyncio.Lock()

        self.config.restart_warning_times.sort(reverse=True)

        self.group_collectors: Collection[BaseGroupCollector[RavenfallInstance]] = (
            group_collectors
        )
        self._block_next_restart_countdown_message_until: float = 0
        self._restart_reason_announced: bool = False

    async def start(self):
        """Start the watcher, including setting up the restart timeline if configured."""
        self.collectors = [collectors.BuggedRaidCheck(self.ravenfall)]
        for c in self.collectors:
            c.set_alert_callback(partial(self._collector_alerting, c))
            c.start()
        for c in self.group_collectors:
            c.set_alert_callback(
                self.ravenfall,
                partial(self._collector_alerting, c),
            )

        await self.auto_restart_timer.register_callback(
            0, self._auto_restart_callback, from_end=True
        )
        if self.config.restart_warning_times:
            list_len = len(self.config.restart_warning_times)
            for x in range(list_len):
                event_start = self.config.restart_warning_times[x]
                event_end = (
                    self.config.restart_warning_times[x + 1]
                    if x + 1 < list_len
                    else min(event_start, 8)
                )
                __ = self.restart_timeline.add_event(
                    -event_start,
                    -event_end,
                    self._announce_restart_countdown,
                    None,
                )

        __ = self.restart_timeline.add_event(
            0,
            0,
            self._execute_restart,
            None,
        )
        __ = self.restart_timeline.add_event(
            -20,
            0,
            self._pre_restart,
            None,
        )

        if self.config.restart_warning_times:
            __ = self.restart_timeline.add_event(
                -self.config.restart_warning_times[0],
                0,
                self._start_restart_blocker,
                None,
            )
        else:
            __ = self.restart_timeline.add_event(
                -self.config.restart_unblock_min_seconds,
                0,
                self._start_restart_blocker,
                None,
            )

        self.inject_event_manager(self.event_manager)
        if self.ravenfall.is_online:
            fire_and_forget(self.on_online())
        else:
            fire_and_forget(self.restart_ravenfall())

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
        LOGGER.info(
            f"[{self.ravenfall.channel_name}] "
            f"Queueing restart in {countdown_seconds} seconds. Reason: {reason}"
        )
        self._block_next_restart_countdown_message_until = 0
        if self.restart_timeline.get_is_playing():
            if self.restart_reason != reason:
                self._restart_reason_announced = False
            if -countdown_seconds > self.restart_timeline.get_current_time():
                await self.restart_timeline.seek(-countdown_seconds)
        else:
            self._restart_reason_announced = False
            await self.restart_timeline.start(-countdown_seconds, 0)
        self.restart_reason = reason

    async def _block_restart(self):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Blocking restart timeline")
        if not self.restart_timeline.get_is_playing():
            return
        await self.restart_timeline.pause()
        channel = self.global_ctx.require_service(RavenfallChannelService)
        await channel.send_global_message(
            f"Postponing restart. "
            f"Reason: {self._restart_blocker_collector.get_alert_reason()}",
            "announcements.restart_postponed",
            self.ravenfall.channel_name,
        )

    async def _unblock_restart(self):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Unblocking restart timeline")
        if not self.restart_timeline.get_is_playing():
            await self.restart_timeline.start(-self.config.restart_unblock_min_seconds, 0)
        elif (
            -(self.restart_timeline.get_current_time())
            <= self.config.restart_unblock_min_seconds
        ):
            await self.restart_timeline.seek(-self.config.restart_unblock_min_seconds)
        await self.restart_timeline.resume()
        channel = self.global_ctx.require_service(RavenfallChannelService)
        formatted_time = format_seconds(
            -(self.restart_timeline.get_current_time()),
            TimeSize.LONG,
            2,
            include_zero=False,
        )
        self._block_next_restart_countdown_message_until = monotonic() + 5
        if not self._restart_reason_announced and self.restart_reason:
            message_key = "announcements.time_until_restart.reason"
            self._restart_reason_announced = True
            await channel.send_global_message(
                f"Resuming restart. Restarting in {formatted_time}! "
                f"Reason: {self.restart_reason}",
                message_key,
                self.ravenfall.channel_name,
            )
            return
        await channel.send_global_message(
            f"Resuming restart. Restarting in {formatted_time}!",
            "announcements.time_until_restart",
            self.ravenfall.channel_name,
        )

    async def _start_restart_blocker(self, _event_ctx: EventInfo):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Starting restart blocker")
        self._restart_blocker_collector.set_alert_callback(self._block_restart)
        self._restart_blocker_collector.set_recovery_callback(self._unblock_restart)
        self._restart_blocker_collector.start()

    async def _stop_restart_blocker(self, _event_ctx: EventInfo | None):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Stopping restart blocker")
        self._restart_blocker_collector.set_alert_callback(None)
        self._restart_blocker_collector.set_recovery_callback(None)
        self._restart_blocker_collector.stop()

    async def _announce_restart_countdown(self, event_ctx: EventInfo):
        if self._block_next_restart_countdown_message_until > monotonic():
            self._block_next_restart_countdown_message_until = 0
            LOGGER.info(
                f"[{self.ravenfall.channel_name}] Blocking restart countdown message"
            )
            return
        LOGGER.info(f"[{self.ravenfall.channel_name}] Announcing restart countdown")
        await self._restart_blocker_collector.run_process_now()
        if self._restart_blocker_collector.get_is_alerting():
            return
        channel = self.global_ctx.require_service(RavenfallChannelService)
        formatted_time = format_seconds(
            -event_ctx.current_time, TimeSize.LONG, 2, include_zero=False
        )
        if not self._restart_reason_announced and self.restart_reason:
            message_key = "announcements.time_until_restart.reason"
            self._restart_reason_announced = True
            await channel.send_global_message(
                f"Restarting in {formatted_time}! Reason: {self.restart_reason}",
                message_key,
                self.ravenfall.channel_name,
            )
            return
        await channel.send_global_message(
            f"Restarting Ravenfall in {formatted_time}!",
            "announcements.time_until_restart",
            self.ravenfall.channel_name,
        )

    async def _execute_restart(self, _event_ctx: EventInfo):
        await self.restart_ravenfall()

    async def _pre_restart(self, event_ctx: EventInfo):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Pre-restart called")
        if event_ctx.event_progress - event_ctx.event_end < 1:
            return
        multichat_serv = self.global_ctx.get_service(RavenfallMultichatService)
        if not multichat_serv:
            return
        multichat = multichat_serv.get_client()
        await multichat.send_multichat_command(
            "?randleave",
            self.ravenfall.channel_id,
            self.ravenfall.channel_name,
            self.ravenfall.channel_id,
            self.ravenfall.channel_name,
        )

    async def _post_restart(self):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Post-restart called")
        multichat_serv = self.global_ctx.get_service(RavenfallMultichatService)
        if not multichat_serv:
            return
        multichat = multichat_serv.get_client()
        await multichat.send_multichat_command(
            "?undorandleave",
            self.ravenfall.channel_id,
            self.ravenfall.channel_name,
            self.ravenfall.channel_id,
            self.ravenfall.channel_name,
        )

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

    def clear_alerts(self):
        """Clear all alerts for this watcher."""
        for collector in self.collectors:
            collector.clear_alert()
        for collector in self.group_collectors:
            collector.clear_alert(self.ravenfall)

    async def restart_ravenfall(self, *, announce: bool = True, reason: str = ""):
        """Restarts ravenfall.

        Prefer to use `queue_restart` instead of calling this directly.
        """
        if self._restart_blocker_collector.get_is_started():
            await self._restart_blocker_collector.run_process_now()
        if self._restart_blocker_collector.get_is_alerting():
            LOGGER.info(
                f"[{self.ravenfall.channel_name}] Restart blocked, not restarting."
            )
            return

        LOGGER.info(f"[{self.ravenfall.channel_name}] Requesting restart.")

        __ = await self.restart_lock.acquire()
        try:
            channel_service = self.global_ctx.get_service(RavenfallChannelService)
            if self.watcher_cog.restart_lock.locked() and announce and channel_service:
                await channel_service.send_global_message(
                    "Waiting for other restart tasks to finish...",
                    "announcements.waiting_for_other_restart",
                    self.ravenfall.channel_name,
                )

            await self._stop_restart_blocker(None)
            await self.auto_restart_timer.stop()
            await self.restart_timeline.stop()

            async with self.watcher_cog.restart_lock:
                LOGGER.info(f"[{self.ravenfall.channel_name}] Restarting Ravenfall.")

                if announce and channel_service:
                    if not self._restart_reason_announced and (
                        self.restart_reason or reason
                    ):
                        message_key = "announcements.restarting.reason"
                        await channel_service.send_global_message(
                            f"Restarting Ravenfall! "
                            f"Reason: {self.restart_reason or reason}",
                            message_key,
                            self.ravenfall.channel_name,
                        )
                    else:
                        await channel_service.send_global_message(
                            "Restarting Ravenfall...",
                            "announcements.restarting",
                            self.ravenfall.channel_name,
                        )
                config = self.config
                __ = await self.process_service.kill_process(
                    "Ravenfall.exe", config.sandboxie_box_name
                )
                while True:
                    try:
                        async with asyncio.timeout(10):
                            # Sometimes sandboxie shows a popup that pauses this process
                            result = await self.process_service.spawn_process(
                                config.start_command,
                                config.sandboxie_box_name,
                                self.watcher_cog.config.ravenfall_folder,
                            )
                    except TimeoutError:
                        LOGGER.warning(
                            f"[{self.ravenfall.channel_name}] "
                            "Spawn process timed out, retrying..."
                        )
                        continue
                    if result.code == 0:
                        break
                    await asyncio.sleep(10)

                self.clear_alerts()

                def predicate(x: BaseEvent):
                    if not isinstance(x, RavenfallEvent):
                        return False
                    return x.ravenfall == self.ravenfall

                event_waiter = self.global_ctx.require_service(EventWaiterService)
                LOGGER.info(
                    f"[{self.ravenfall.channel_name}] "
                    "Waiting for Ravenfall to come back online..."
                )
                try:
                    async with asyncio.timeout(self.config.restart_timeout_seconds):
                        __ = await event_waiter.wait_for(
                            RavenfallOnlineEvent, predicate=predicate
                        )
                except TimeoutError:
                    LOGGER.info(
                        f"[{self.ravenfall.channel_name}] "
                        "Ravenfall did not come back online in time."
                    )
                    self.restart_lock.release()
                    await self.queue_restart(1, "Restart failed")
                    return
                LOGGER.info(
                    f"[{self.ravenfall.channel_name}] Ravenfall is back online, "
                    "waiting for it to be ready..."
                )

            try:
                async with asyncio.timeout(self.config.restart_timeout_seconds):
                    result = await event_waiter.wait_for_multiple(
                        [
                            EventTypePredicate(
                                RavenfallReadyEvent, predicate, seconds_before=5
                            ),
                            EventTypePredicate(RavenfallOfflineEvent, predicate),
                        ],
                    )
                    if isinstance(result, RavenfallReadyEvent):
                        LOGGER.info(
                            f"[{self.ravenfall.channel_name}] Ravenfall is ready!"
                        )
                    else:
                        LOGGER.info(
                            f"[{self.ravenfall.channel_name}] "
                            "Ravenfall went offline again during restart!"
                        )
                        return
            except TimeoutError:
                LOGGER.info(f"[{self.ravenfall.channel_name}] Restart may have failed.")
                self.restart_lock.release()
                await self.queue_restart(1, "Restart failed")
                return
        except Exception:
            LOGGER.exception(f"[{self.ravenfall.channel_name}] Error during restart")
            if self.restart_lock.locked():
                self.restart_lock.release()
            await self.queue_restart(1, "Restart failed")
            return

        if self.restart_lock.locked():
            self.restart_lock.release()

        await self._post_restart()

    @on_match(RavenfallOfflineEvent)
    async def on_offline(
        self, _g_ctx: GlobalContext, event: RavenfallOfflineEvent, _match: object
    ):
        """Runs when Ravenfall goes offline."""
        if self.restart_lock.locked():
            return
        if event.ravenfall == self.ravenfall:
            await self.queue_restart(1, "Ravenfall is offline")

    @on_match(RavenfallOnlineEvent)
    async def on_online(
        self,
        _g_ctx: GlobalContext | None = None,
        _event: RavenfallOnlineEvent | None = None,
        _match: object | None = None,
    ):
        """Runs when Ravenfall goes online."""
        if self.config.auto_restart_period_seconds:
            uptime = None
            try:
                uptime = await self.ravenfall.get_session()
            except Exception as e:  # noqa: BLE001
                LOGGER.warning(
                    f"[{self.ravenfall.channel_name}] Failed to fetch uptime: {e}"
                )
            if not uptime:
                LOGGER.warning(f"[{self.ravenfall.channel_name}] Failed to fetch uptime.")
            if uptime is not None:
                time_remaining = (
                    self.config.auto_restart_period_seconds - uptime.seconds_since_start
                )
            else:
                time_remaining = 0
            await self.auto_restart_timer.stop()
            if time_remaining <= 0:
                # await self.queue_restart(10, "Scheduled auto-restart")
                # fire_and_forget(self.restart_ravenfall())
                if not uptime:
                    await self.queue_restart(1, "Ravenfall is offline")
                else:
                    await self.queue_restart(1, "Scheduled auto-restart")
                return
            LOGGER.info(
                f"[{self.ravenfall.channel_name}] "
                f"Restarting in {format_seconds(time_remaining)} "
                "for scheduled auto-restart."
            )
            await self.auto_restart_timer.start(time_remaining)

    def add_event_to_restart_timeline(
        self,
        time_from_restart: float,
        event_callback: Callable[[EventInfo], Coroutine[None, None, None]],
        *,
        event_end_time_from_restart: float | None = None,
    ) -> TimelineEvent:
        """Add an event to the restart timeline."""
        if event_end_time_from_restart is not None:
            return self.restart_timeline.add_event(
                time_from_restart,
                event_end_time_from_restart,
                event_callback,
                None,
            )
        return self.restart_timeline.add_event(
            time_from_restart,
            time_from_restart,
            event_callback,
            None,
        )

    def remove_event_from_restart_timeline(self, event: TimelineEvent) -> None:
        """Remove an event from the restart timeline."""
        self.restart_timeline.remove_event(event)

    # @on_match(MessageEvent, lambda x: x.text == "!unblockrestart")
    # async def on_unblock_restart_command(
    #     self,
    #     _g_ctx: GlobalContext,
    #     _event: MessageEvent,
    #     _match: object,
    # ):
    #     """Command to unblock the restart timeline if it's currently blocked."""
    #     LOGGER.info(
    #         f"[{self.ravenfall.channel_name}] Unblocking restart timeline via command"
    #     )
    #     self._restart_blocker_collector.force_alerting = False

    # @on_match(MessageEvent, lambda x: x.text == "!blockrestart")
    # async def on_block_restart_command(
    #     self,
    #     _g_ctx: GlobalContext,
    #     _event: MessageEvent,
    #     _match: object,
    # ):
    #     """Command to block the restart timeline if it's currently blocked."""
    #     LOGGER.info(
    #         f"[{self.ravenfall.channel_name}] Blocking restart timeline via command"
    #     )
    #     self._restart_blocker_collector.force_alerting = True
