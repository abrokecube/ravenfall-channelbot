from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Collection
from datetime import timedelta
from functools import partial
from time import monotonic
from typing import TYPE_CHECKING, NamedTuple

from bot.cogs.ravenfall_watcher.base_classes import BaseGroupCollector
from bot.core.decorators import on_match
from bot.integrations.ravenfall import (
    DungeonEndedEvent,
    DungeonPreparedEvent,
    DungeonSpawnedEvent,
    RaidEndedEvent,
    RaidStartedEvent,
    RavenBotMessageEvent,
    RavenfallEvent,
    RavenfallOfflineEvent,
    RavenfallOnlineEvent,
    RavenfallReadyEvent,
)
from bot.integrations.ravenfall.event_sources import RavenfallInstance
from bot.integrations.ravenfall.events import DungeonReachedBossEvent
from bot.integrations.twitch import TwitchService
from bot.mixins.event_receiver import EventReceiverMixin
from bot.services.event_waiter import EventTypePredicate, EventWaiterService
from bot.services.ravenfall_channels import RavenfallChannelService
from bot.services.ravenfall_multichat import RavenfallMultichatService
from utils.format_time import TimeSize, format_seconds
from utils.routines import routine

from . import collectors
from .base_classes import BaseCollector, RestartTarget
from .timeline import SeekMode, Timeline
from .timer import Timer

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Coroutine

    from bot.core.components import BaseEvent, EventManager, GlobalContext
    from bot.integrations.chat_messages import MessageEvent
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


class RestartCancelFailureError(Exception):
    """Failed to cancel the active restart task."""


class PausedError(Exception):
    """Watcher is paused."""


class NoRestartTaskError(Exception):
    """There is no active restart task."""


class RestartTaskData(NamedTuple):
    """Data about the current restart task."""

    is_scheduled: bool
    """Whether a restart is currently scheduled."""
    seconds_remaining: float | None
    """Seconds until restart, or None if no restart is scheduled."""
    reason: str
    """The reason for the restart."""
    is_announced: bool
    """Whether the restart reason has been announced to chat."""
    is_auto_restart_paused: bool
    """Whether automatic restarts are currently paused."""
    is_restart_in_progress: bool
    """Whether a restart operation is currently executing."""


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
        self.ravenfall_restart_lock: asyncio.Lock = asyncio.Lock()
        self.ravenbot_restart_lock: asyncio.Lock = asyncio.Lock()

        self.config.restart_warning_times.sort(reverse=True)

        self.group_collectors: Collection[BaseGroupCollector[RavenfallInstance]] = (
            group_collectors
        )
        self._block_next_restart_countdown_message_until: float = 0
        self._restart_reason_announced: bool = False
        self._auto_restart_paused: bool = False

        self._last_dungeon_spawn_time: float = 0
        self._last_dungeon_prepare_duration: float = 0

    async def start(self):
        """Start the watcher, including setting up the restart timeline if configured."""
        if (
            self.watcher_cog.config.ravenfall_executable_name.lower()
            not in self.config.start_command.lower()
        ):
            LOGGER.warning(
                f"[{self.ravenfall.channel_name}] "
                "The start command does not contain "
                "the configured ravenfall executable name, "
                "this may cause issues with auto-restart and memory monitoring. "
                "Check your config and make sure 'start_command' "
                "contains the executable name."
            )

        self.collectors = [collectors.BuggedRaidCheck(self.ravenfall)]
        self._hook_collectors()
        self._start_watcher_collectors()

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
                self._stop_restart_blocker,
            )
        else:
            __ = self.restart_timeline.add_event(
                -self.config.restart_unblock_min_seconds,
                0,
                self._start_restart_blocker,
                self._stop_restart_blocker,
            )

        self.inject_event_manager(self.event_manager)

        channel_serv = self.global_ctx.get_service(RavenfallChannelService)
        if not channel_serv:
            LOGGER.warning("Ravenfall channel service not available")
        else:
            channel_serv.register_message_event_callback(
                self._on_ravenfall_chat_message, self.ravenfall.twitch_login
            )

        if self.ravenfall.is_online:
            await self._on_online()
        else:
            await self.restart_ravenfall()

    async def stop(self):
        """Stop the watcher and all its collectors."""
        self._unhook_collectors()
        self._stop_watcher_collectors()
        await self.auto_restart_timer.stop()
        await self.restart_timeline.stop()
        channel_serv = self.global_ctx.get_service(RavenfallChannelService)
        if channel_serv:
            channel_serv.unregister_message_event_callback(
                self._on_ravenfall_chat_message
            )

    def _start_watcher_collectors(self):
        for c in self.collectors:
            c.start()

    def _stop_watcher_collectors(self):
        for c in self.collectors:
            c.stop()

    def _hook_collectors(self):
        for c in self.collectors:
            c.set_alert_callback(partial(self._collector_alerting, c))
        for c in self.group_collectors:
            c.set_alert_callback(
                self.ravenfall,
                partial(self._collector_alerting, c),
            )

    def _unhook_collectors(self):
        for c in self.collectors:
            c.remove_alert_callback()
        for c in self.group_collectors:
            c.remove_alert_callback(self.ravenfall)

    async def _collector_alerting(
        self,
        collector: BaseCollector[RavenfallInstance]
        | BaseGroupCollector[RavenfallInstance],
    ):
        if self._auto_restart_paused:
            return

        if collector.restart_target is RestartTarget.RAVENBOT:
            await self.restart_ravenbot()
            return

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
        if self._auto_restart_paused:
            return
        countdown_time = self.config.restart_unblock_min_seconds + 10
        if self.config.restart_warning_times:
            countdown_time = max(
                self.config.restart_warning_times[0],
                countdown_time,
            )

        await self.queue_restart(countdown_time, "Scheduled auto-restart")

    async def queue_restart(self, countdown_seconds: float, reason: str = ""):
        """Queue a ravenfall restart with the specified countdown."""
        if countdown_seconds < 0:
            msg = "Countdown must be positive."
            raise ValueError(msg)
        if self.ravenfall_restart_lock.locked():
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
            current_time = self.restart_timeline.get_current_time()
            if -countdown_seconds > current_time:
                await self.advance_restart((-current_time) - countdown_seconds)
        else:
            self._restart_reason_announced = False
            await self.restart_timeline.start(-countdown_seconds, 0)
        self.restart_reason = reason

    async def postpone_restart(self, seconds: float):
        """Postpone a running restart task."""
        if seconds < 0:
            msg = "Seconds must be positive."
            raise ValueError(msg)
        if self.ravenfall_restart_lock.locked():
            raise RestartCancelFailureError
        if not self.restart_timeline.get_is_playing():
            raise NoRestartTaskError

        current_time_left = -self.restart_timeline.get_current_time()
        if (
            self.config.restart_warning_times
            and current_time_left + seconds > self.config.restart_warning_times[0]
        ):
            self._restart_reason_announced = False
        await self.restart_timeline.seek(-current_time_left - seconds)

    async def advance_restart(self, seconds: float):
        """Advance a running restart task."""
        if seconds < 0:
            msg = "Seconds must be positive."
            raise ValueError(msg)
        if self.ravenfall_restart_lock.locked():
            raise RestartCancelFailureError
        if not self.restart_timeline.get_is_playing():
            raise NoRestartTaskError
        current_time = self.restart_timeline.get_current_time()
        await self.restart_timeline.seek(current_time + seconds)

    async def cancel_restart(self):
        """Stops an active restart."""
        if self.ravenfall_restart_lock.locked():
            raise RestartCancelFailureError

        await self._stop_restart_blocker(None)
        await self.restart_timeline.stop()
        self.clear_alerts()

    async def pause_auto_restarts(self):
        """Stop the watcher from auto-restarting."""
        if self.ravenfall_restart_lock.locked():
            raise RestartCancelFailureError
        self._auto_restart_paused = True
        self._unhook_collectors()
        self.clear_alerts()

        # Stop both the timeline and the timer regardless of state
        with contextlib.suppress(NoRestartTaskError):
            await self.cancel_restart()
        await self.auto_restart_timer.stop()
        LOGGER.info(f"[{self.ravenfall.channel_name}] Auto-restarts paused")

    async def resume_auto_restarts(self):
        """Enable auto-restarts again."""
        self._auto_restart_paused = False
        self._hook_collectors()
        # self._start_watcher_collectors()
        await self._refresh_auto_restart_timer()
        LOGGER.info(f"[{self.ravenfall.channel_name}] Auto-restarts resumed")

    def get_restarts_are_paused(self):
        """Auto-restarts are paused."""
        return self._auto_restart_paused

    def get_restart_task_info(self) -> RestartTaskData:
        """Get data about the current restart task.

        Returns:
            RestartTaskData: Information about the current restart task,
                including whether it's scheduled, time remaining, reason,
                and whether the restart has been announced.
        """
        is_scheduled = self.restart_timeline.get_is_playing()
        seconds_remaining: float | None = None

        if is_scheduled:
            seconds_remaining = -self.restart_timeline.get_current_time()

        return RestartTaskData(
            is_scheduled=is_scheduled,
            seconds_remaining=seconds_remaining,
            reason=self.restart_reason,
            is_announced=self._restart_reason_announced,
            is_auto_restart_paused=self._auto_restart_paused,
            is_restart_in_progress=self.ravenfall_restart_lock.locked(),
        )

    async def _block_restart(self):
        LOGGER.info(f"[{self.ravenfall.channel_name}] Blocking restart timeline")
        if not self.restart_timeline.get_is_playing():
            return
        await self.restart_timeline.pause()
        channel = self.global_ctx.require_service(RavenfallChannelService)
        if self._restart_reason_announced:
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
        LOGGER.debug(f"[{self.ravenfall.channel_name}] Pre-restart called")
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
        LOGGER.debug(f"[msg={self.ravenfall.channel_name}] Post-restart called")
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

        __ = await self.ravenfall_restart_lock.acquire()
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
                    self.watcher_cog.config.ravenfall_executable_name,
                    config.sandboxie_box_name,
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
                    self.ravenfall_restart_lock.release()
                    await self.queue_restart(1, "Restart failed")
                    return
                LOGGER.info(
                    f"[{self.ravenfall.channel_name}] Ravenfall is back online, "
                    "waiting for it to be ready..."
                )
                if (
                    self.ravenfall.get_is_linked_to_middleman()
                    and self.ravenfall.event_source.get_has_configured_middleman()
                ):
                    try:
                        await self.ravenfall.reconnect_middleman_to_ravenfall(60)
                    except Exception:
                        LOGGER.exception("Failed to communicate with middleman.")
                self._start_keep_middleman_connected_routine()

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
                        LOGGER.debug(
                            f"[{self.ravenfall.channel_name}] Ravenfall is ready!"
                        )
                        self._stop_keep_middleman_connected_routine(force=True)
                    else:
                        LOGGER.debug(
                            f"[{self.ravenfall.channel_name}] "
                            "Ravenfall went offline again during restart!"
                        )
                        self.ravenfall_restart_lock.release()
                        self._stop_keep_middleman_connected_routine(force=True)
                        return
            except TimeoutError:
                LOGGER.info(f"[{self.ravenfall.channel_name}] Restart may have failed.")
                self.ravenfall_restart_lock.release()
                await self.queue_restart(5, "Restart failed")
                return
        except Exception:
            LOGGER.exception(f"[{self.ravenfall.channel_name}] Error during restart")
            if self.ravenfall_restart_lock.locked():
                self.ravenfall_restart_lock.release()
            await self.queue_restart(5, "Restart failed")
            return

        if self.ravenfall_restart_lock.locked():
            self.ravenfall_restart_lock.release()

        await self._post_restart()

    async def _on_ravenfall_chat_message(self, event: MessageEvent, _: RavenfallInstance):
        await self._check_ravenbot(event)

    async def _check_ravenbot(self, event: MessageEvent):
        rf_event_src = self.ravenfall_service.event_source
        if (
            rf_event_src.middleman_message_processor is None
            and rf_event_src.middleman_client is None
        ):
            return
        if (
            rf_event_src.middleman_message_processor is not None
            and rf_event_src.middleman_message_processor.connected_client_count == 0
        ):
            return
        if (
            rf_event_src.middleman_client is not None
            and not rf_event_src.middleman_client.is_websocket_connected
        ):
            return
        if self.config.ravenbot_channel_id is not None:
            if self.config.ravenbot_channel_id != event.room_id:
                return
        elif event.room_id != self.ravenfall.channel_id:
            return

        waiter_service = self.global_ctx.get_service(EventWaiterService)
        if not waiter_service:
            return

        split = event.text.lower().split(" ", maxsplit=1)
        if not split:
            return
        first_word = split[0]
        prefix = self.config.ravenbot_prefix
        if not first_word.startswith(prefix):
            return
        the_rest = first_word[len(prefix) :]
        if the_rest not in self.watcher_cog.config.commands_to_watch:
            return

        def predicate(event: BaseEvent):
            if not isinstance(event, RavenBotMessageEvent):
                return False
            return event.ravenfall == self.ravenfall

        try:
            __ = await waiter_service.wait_for(
                RavenBotMessageEvent, predicate=predicate, timeout=1, seconds_before=1
            )
        except TimeoutError:
            LOGGER.info("RavenBot did not respond, restarting.")
            await self.restart_ravenbot()
            await event.reply("RavenBot wasn't responding. Try this command again.")

    async def restart_ravenbot(self, *, announce: bool = True):
        """Restarts ravenbot."""
        if self.ravenbot_restart_lock.locked():
            LOGGER.debug("restart_ravenbot was called during a restart.")
            return

        async with self.ravenbot_restart_lock:
            if announce:
                twitch = self.global_ctx.get_service(TwitchService)
                if twitch:
                    __ = await twitch.send_message(
                        "Restarting RavenBot...", self.ravenfall.channel_id
                    )
            config = self.config
            __ = await self.process_service.kill_process(
                self.watcher_cog.config.ravenbot_executable_name,
                config.sandboxie_box_name,
            )
            while True:
                try:
                    async with asyncio.timeout(10):
                        # Sometimes sandboxie shows a popup that pauses this process
                        result = await self.process_service.spawn_process(
                            "RavenBot.exe",
                            config.sandboxie_box_name,
                            self.watcher_cog.config.ravenbot_folder,
                            wait=False,
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

    async def _refresh_auto_restart_timer(self):
        if self._auto_restart_paused:
            return
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

    @routine(delta=timedelta(seconds=30))
    async def _keep_middleman_connected_routine(self):
        """Keep the linked middleman connected to Ravenfall."""
        ev_src = self.ravenfall.event_source
        if not (
            ev_src.get_has_configured_middleman()
            and self.ravenfall.get_is_linked_to_middleman()
        ):
            return
        __ = await self.ravenfall.ensure_middleman_connection(45)

    def _start_keep_middleman_connected_routine(self):
        with contextlib.suppress(RuntimeError):
            __ = self._keep_middleman_connected_routine.start()

    def _stop_keep_middleman_connected_routine(self, *, force: bool = False):
        if not force and self.ravenfall_restart_lock.locked():
            return
        self._keep_middleman_connected_routine.stop()

    @on_match(RavenfallOfflineEvent)
    async def _on_offline(
        self, _g_ctx: GlobalContext, event: RavenfallOfflineEvent, _match: object
    ):
        """Runs when Ravenfall goes offline."""
        if event.ravenfall != self.ravenfall:
            return
        if self._auto_restart_paused:
            return
        if self.ravenfall_restart_lock.locked():
            return
        if event.ravenfall == self.ravenfall:
            await self.queue_restart(1, "Ravenfall is offline")

    @on_match(RavenfallOnlineEvent)
    async def _on_online(
        self,
        _g_ctx: GlobalContext | None = None,
        event: RavenfallOnlineEvent | None = None,
        _match: object | None = None,
    ):
        """Runs when Ravenfall goes online."""
        if event and event.ravenfall != self.ravenfall:
            return
        if self._auto_restart_paused:
            return
        await self._refresh_auto_restart_timer()

    @on_match(DungeonSpawnedEvent)
    async def _on_dungeon_spawned(
        self,
        _g_ctx: GlobalContext | None,
        event: DungeonSpawnedEvent,
        _match: object | None,
    ):
        """Runs when the dungeon boss is reached."""
        if event.ravenfall != self.ravenfall:
            return
        self._last_dungeon_spawn_time = monotonic()

    @on_match(DungeonPreparedEvent)
    async def _on_dungeon_prepared(
        self,
        _g_ctx: GlobalContext | None,
        event: DungeonPreparedEvent,
        _match: object | None,
    ):
        """Runs when the dungeon boss is reached."""
        if event.ravenfall != self.ravenfall:
            return
        self._last_dungeon_prepare_duration = monotonic() - self._last_dungeon_spawn_time
        if (
            0
            > self._last_dungeon_prepare_duration
            > self.config.max_dungeon_prepare_time_seconds
        ):
            LOGGER.warning(
                f"[{self.ravenfall.channel_name}] "
                f"Dungeon prepare time exceeded: {self._last_dungeon_prepare_duration}s"
            )

    @on_match(DungeonReachedBossEvent)
    async def _on_reached_dungeon_boss(
        self,
        _g_ctx: GlobalContext | None,
        event: DungeonReachedBossEvent,
        _match: object | None,
    ):
        """Runs when the dungeon boss is reached."""
        if event.ravenfall != self.ravenfall:
            return
        self._start_keep_middleman_connected_routine()

    @on_match(DungeonEndedEvent)
    async def _on_dungeon_ended(
        self,
        _g_ctx: GlobalContext | None,
        event: DungeonEndedEvent,
        _match: object | None,
    ):
        """Runs when the dungeon boss is reached."""
        if event.ravenfall != self.ravenfall:
            return
        self._stop_keep_middleman_connected_routine()
        if (
            0
            > self._last_dungeon_prepare_duration
            > self.config.max_dungeon_prepare_time_seconds
        ):
            countdown_time = self.config.restart_unblock_min_seconds + 10
            if self.config.restart_warning_times:
                countdown_time = max(
                    self.config.restart_warning_times[0],
                    countdown_time,
                )
            await self.queue_restart(countdown_time, "Dungeon took too long to prepare")

    @on_match(RaidStartedEvent)
    async def _on_raid_started(
        self,
        _g_ctx: GlobalContext,
        event: RaidStartedEvent,
        _match: object | None,
    ):
        """Runs when the dungeon boss is reached."""
        if event.ravenfall != self.ravenfall:
            return
        self._start_keep_middleman_connected_routine()

    @on_match(RaidEndedEvent)
    async def _on_raid_ended(
        self,
        _g_ctx: GlobalContext | None,
        event: RaidEndedEvent,
        _match: object | None,
    ):
        """Runs when the dungeon boss is reached."""
        if event.ravenfall != self.ravenfall:
            return
        self._stop_keep_middleman_connected_routine()

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
