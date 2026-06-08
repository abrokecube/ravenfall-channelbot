from __future__ import annotations

import time
import asyncio
from utils.format_time import format_seconds, TimeSize
from enum import Enum
from typing import TYPE_CHECKING
from .models_old import RFChannelEvent, RFChannelSubEvent
import logging

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .ravenfallchannel_old import RFChannel
    from .ravenfallmanager_old import RFChannelManager


class RestartReason(Enum):
    AUTO = "auto_restart"
    USER = "user_restart"
    UNRESPONSIVE = "unresponsive"
    MULTIPLIER_DESYNC = "multiplier_desync"
    ITEM_DESYNC = "item_desync"
    MEMORY_USE = "memory_use"


class PreRestartEvent(Enum):
    WARNING = "warning"
    PRE_RESTART = "pre_restart"


class RestartStatus(Enum):
    IDLE = "idle"
    WAITING = "waiting"
    RESTARTING = "restarting"
    FINISHED = "finished"
    PAUSED = "paused"


WARNING_MSG_TIMES: tuple[tuple[int, PreRestartEvent], ...] = (
    (120, PreRestartEvent.WARNING),
    (30, PreRestartEvent.WARNING),
    (20, PreRestartEvent.PRE_RESTART),
)


class RFRestartTask:
    def __init__(
        self,
        channel: RFChannel,
        manager: RFChannelManager,
        time_to_restart: float | None = 0,
        mute_countdown: bool = False,
        label: str = "",
        reason: RestartReason | None = None,
    ):
        self.channel: RFChannel = channel
        self.manager: RFChannelManager = manager
        if time_to_restart is None:
            self.time_to_restart = WARNING_MSG_TIMES[0][0]
        else:
            self.time_to_restart: float = time_to_restart
        self.start_t: float = 0
        self.waiting_task: asyncio.Task[None] | None = None
        self.event_watch_task: asyncio.Task[None] | None = None
        self.done: bool = False
        self._paused: bool = False
        self._pause_time: float = 0
        self._pause_start: float = 0
        self.pause_event_name: str = ""
        self.future_pause_reason: str = ""
        self.mute_countdown: bool = mute_countdown
        self.label: str = label
        self.reason: RestartReason | None = reason
        self._status: RestartStatus = RestartStatus.IDLE
        self.event_watch_lock = asyncio.Lock()
        self.sent_initial_announcement: bool = False
        self.sent_reason: bool = False

    def start(self):
        if not self.done:
            if self.waiting_task and not self.waiting_task.done():
                _ = self.waiting_task.cancel()
            if self.event_watch_task and not self.event_watch_task.done():
                _ = self.event_watch_task.cancel()
        self.start_t = time.time()
        self.waiting_task = asyncio.create_task(self._waiting())
        self.event_watch_task = asyncio.create_task(self._event_watcher())

    def cancel(self):
        if self.waiting_task:
            _ = self.waiting_task.cancel()
        if self.event_watch_task:
            _ = self.event_watch_task.cancel()
        self.done = True

    async def wait(self):
        """Wait until the restart task is finished."""
        if self.waiting_task:
            try:
                await self.waiting_task
            except asyncio.CancelledError:
                pass

    async def _waiting(self):
        warning_idx = -1
        self._status = RestartStatus.WAITING
        while True:
            await asyncio.sleep(1)
            if self._paused:
                continue
            time_left = self.get_time_left()
            if time_left <= 0:
                break
            new_warning_idx = -1
            for i, (x, _) in enumerate(WARNING_MSG_TIMES):
                if time_left < x:
                    new_warning_idx = i
            if new_warning_idx != warning_idx:
                if new_warning_idx >= 0 and new_warning_idx > warning_idx:
                    for i in range(warning_idx + 1, new_warning_idx + 1):
                        if WARNING_MSG_TIMES[i][1] == PreRestartEvent.PRE_RESTART:
                            try:
                                await self.channel._ravenfall_pre_restart()  # pyright: ignore[reportPrivateUsage]
                            except Exception as e:
                                logger.error(
                                    f"Failed to run pre restart for {self.channel.channel_name}: {e}",
                                    exc_info=True,
                                )
                    if (
                        WARNING_MSG_TIMES[new_warning_idx][1] == PreRestartEvent.WARNING
                        and time_left > 7
                        and not self.mute_countdown
                    ):
                        msg = f"Restarting Ravenfall in {format_seconds(time_left, TimeSize.LONG, 2, False)}!"
                        if (not self.sent_reason) and self.label:
                            msg += f" Reason: {self.label}"

                        if not self.sent_initial_announcement:
                            await self.channel.send_announcement(msg)
                            self.sent_initial_announcement = True
                        else:
                            await self.channel.send_chat_message(msg)

                        if not self.sent_reason:
                            self.sent_reason = True
                warning_idx = new_warning_idx
        async with self.event_watch_lock:  # wait for any pending events to be processed
            pass
        await self._execute()

    async def _event_watcher(self):
        event_type = ""
        messages = {
            "server_down": "Restart postponed due to server being offline.",
            "updater_down": "Restart postponed due to update check failing.",
            "dungeon": "Restart postponed due to dungeon.",
            "dungeon_prep": "Restart postponed due to dungeon being prepared.",
            "raid": "Restart postponed due to raid.",
            "error": "Restart postponed due to an error while checking Ravenfall status.",
        }
        names = {
            "server_down": "server offline",
            "updater_down": "update check failed",
            "dungeon": "dungeon",
            "dungeon_prep": "dungeon being prepared",
            "raid": "raid",
            "error": "error checking status",
        }
        while True:
            old_event_type = event_type
            event_type = ""
            async with self.event_watch_lock:
                await asyncio.sleep(2)
                if self.done:
                    return

                time_left = self.get_time_left()
                try:
                    if self.channel.sub_event == RFChannelSubEvent.DUNGEON_PREPARE:
                        event_type = "dungeon_prep"
                    if (
                        self.channel.event == RFChannelEvent.DUNGEON
                        and self.channel.dungeon
                        and self.channel.dungeon["players"] > 0
                    ):
                        event_type = "dungeon"
                    elif (
                        self.channel.event == RFChannelEvent.RAID
                        and self.channel.raid
                        and self.channel.raid["players"] > 0
                    ):
                        event_type = "raid"
                    if not self.manager.ravennest_is_online:
                        event_type = "server_down"
                    if time_left < 35 and not await self.manager.check_update_endpoint():
                        event_type = "updater_down"

                    if event_type:
                        self.future_pause_reason = names[event_type]
                    else:
                        self.future_pause_reason = ""
                except Exception as e:
                    logger.error(
                        f"Error checking restart pause events for {self.channel.channel_name}: {e}",
                        exc_info=True,
                    )
                    event_type = "error"

                if (time_left > WARNING_MSG_TIMES[0][0]) and not self._paused:
                    continue

                if not event_type:
                    if self._paused:
                        self.unpause()
                        time_left = self.get_time_left()
                        if time_left < 60:
                            self.time_to_restart += 60 - time_left
                            time_left = self.get_time_left()
                        await self.channel.send_announcement(
                            f"Resuming restart. Restarting in {format_seconds(time_left, TimeSize.LONG, 2, False)}.",
                        )
                else:
                    if (not self._paused) or old_event_type != event_type:
                        self.pause(names[event_type])
                        await self.channel.send_chat_message(
                            messages[event_type], ignore_error=True
                        )

    async def _execute(self):
        if self.event_watch_task:
            _ = self.event_watch_task.cancel()
        else:
            logger.warning(f"Event watch task not found for {self.channel.channel_name}")
        self._status = RestartStatus.RESTARTING
        try:
            _ = await self.channel._restart_ravenfall(  # pyright: ignore[reportPrivateUsage]
                run_pre_restart=False, run_post_restart=True, restart_task=self
            )
        except Exception as e:
            logger.error(
                f"Failed to restart Ravenfall for {self.channel.channel_name}: {e}",
                exc_info=True,
            )
        self.done = True
        self._status = RestartStatus.FINISHED

    def finished(self):
        return self.done

    def paused(self):
        return self._paused

    def get_time_left(self) -> float:
        pause_time = self._pause_time
        if self._paused:
            pause_time += time.time() - self._pause_start
        return self.time_to_restart - (time.time() - self.start_t - pause_time)

    def pause(self, event_name: str = ""):
        if not self._paused:
            self._paused = True
            self._pause_start = time.time()
            self.pause_event_name = event_name

    def unpause(self):
        if self._paused:
            self._paused = False
            self._pause_time += time.time() - self._pause_start
            self.pause_event_name = ""

    def get_status(self):
        if self._paused:
            return RestartStatus.PAUSED
        if self.done:
            return RestartStatus.FINISHED
        return self._status

    def postpone(self, seconds: int):
        self.time_to_restart += seconds
