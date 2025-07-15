from __future__ import annotations

import time
import asyncio
from utils.format_time import format_seconds, TimeSize
from enum import Enum
from typing import List, Tuple, TYPE_CHECKING
from .models import RFChannelEvent, RFChannelSubEvent

if TYPE_CHECKING:
    from .ravenfallchannel import RFChannel
    from .ravenfallmanager import RFChannelManager

class RestartReason(Enum):
    AUTO = "auto_restart"
    USER = "user_restart"
    UNRESPONSIVE = "unresponsive"
    MULTIPLIER_DESYNC = "multiplier_desync"
    

class PreRestartEvent(Enum):
    WARNING = "warning"
    PRE_RESTART = "pre_restart"

WARNING_MSG_TIMES: List[Tuple[int, PreRestartEvent]] = (
    (120, PreRestartEvent.WARNING), 
    (30, PreRestartEvent.WARNING),
    (20, PreRestartEvent.PRE_RESTART)
)
class RFRestartTask:
    def __init__(
        self,
        channel: RFChannel,
        manager: RFChannelManager,
        time_to_restart: int | None = 0,
        mute_countdown: bool = False,
        label: str = "",
        reason: RestartReason | None = None
    ):
        self.channel = channel
        self.manager = manager
        self.time_to_restart = time_to_restart
        if self.time_to_restart is None:
            self.time_to_restart = WARNING_MSG_TIMES[0][0]
        self.start_t = 0
        self.waiting_task: asyncio.Task = None
        self.event_watch_task: asyncio.Task = None
        self.done = False
        self._paused = False
        self._pause_time = 0
        self._pause_start = 0
        self.pause_event_name = ""
        self.future_pause_reason = ""
        self.mute_countdown: bool = mute_countdown
        self.label: str = label
        self.reason: RestartReason | None = reason

    def start(self):
        if not self.done:
            if self.waiting_task and not self.waiting_task.done():
                self.waiting_task.cancel()
            if self.event_watch_task and not self.event_watch_task.done():
                self.event_watch_task.cancel()
        self.start_t = time.time()
        self.waiting_task = asyncio.create_task(self._waiting())
        self.event_watch_task = asyncio.create_task(self._event_watcher())

    def cancel(self):
        self.waiting_task.cancel()
        self.event_watch_task.cancel()
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
                            await self.channel._ravenfall_pre_restart()
                    if WARNING_MSG_TIMES[new_warning_idx][1] == PreRestartEvent.WARNING and time_left > 7 and not self.mute_countdown:
                        await self.channel.send_chat_message(
                            f"Restarting Ravenfall in {format_seconds(time_left, TimeSize.LONG, 2, False)}!"
                        )
                warning_idx = new_warning_idx
        await self._execute()

    async def _event_watcher(self):
        event_type = ""
        messages = {
            "server_down": "Restart postponed due to server being offline.",
            "dungeon": "Restart postponed due to dungeon.",
            "dungeon_prep": "Restart postponed due to dungeon being prepared.",
            "raid": "Restart postponed due to raid.",
        }
        names = {
            "server_down": "server offline",
            "dungeon": "dungeon",
            "dungeon_prep": "dungeon being prepared",
            "raid": "raid",
        }
        while True:
            old_event_type = event_type
            event_type = ""
            await asyncio.sleep(2)
            if self.done:
                return
            time_left = self.get_time_left()
            if self.channel.sub_event == RFChannelSubEvent.DUNGEON_PREPARE:
                event_type = "dungeon_prep"
            if self.channel.event == RFChannelEvent.DUNGEON and self.channel.dungeon['players'] > 0:
                event_type = "dungeon"
            elif self.channel.event == RFChannelEvent.RAID and self.channel.raid["players"] > 0:
                event_type = "raid"
            if not self.manager.ravennest_is_online:
                event_type = "server_down"
            
            if event_type:
                self.future_pause_reason = names[event_type]
            else:
                self.future_pause_reason = ""

            if (time_left > WARNING_MSG_TIMES[0][0] + 5) and not self._paused:
                continue

            if not event_type:
                if self._paused:
                    self.unpause()
                    time_left = self.get_time_left()
                    if time_left < 60:
                        self.time_to_restart += 60 - time_left
                        time_left = self.get_time_left()
                    await self.channel.send_chat_message(
                        f"Resuming restart. Restarting in {format_seconds(time_left, TimeSize.LONG, 2, False)}."
                    )
            else:
                if (not self._paused) or old_event_type != event_type:
                    self.pause(names[event_type])
                    await self.channel.send_chat_message(
                        messages[event_type]
                    )

    async def _execute(self):
        self.event_watch_task.cancel()
        await self.channel._restart_ravenfall(
            run_pre_restart=False,
            run_post_restart=True
        )
        self.done = True

    def finished(self):
        return self.done

    def paused(self):
        return self._paused
    
    def get_time_left(self):
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

    def postpone(self, seconds: int):
        self.time_to_restart += seconds
