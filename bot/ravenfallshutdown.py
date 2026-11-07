"""Shuts towns down, and brings them back up, on command.

Each town goes down through its normal restart task (countdown, pre restart, and
waiting for dungeons and raids), except that the game is not started again. From
the moment a shutdown is asked for until the town is started again, its restarts
are held, otherwise the bot would see it as crashed and bring it right back.

The hold only lives in memory, so restarting the bot brings the towns back too.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable, NamedTuple

from .ravenfallrestarttask import RestartReason, RestartStatus, RFRestartTask, WARNING_MSG_TIMES

if TYPE_CHECKING:
    from .ravenfallchannel import RFChannel
    from .ravenfallmanager import RFChannelManager

logger = logging.getLogger(__name__)

# A shorter countdown would end before the pre restart runs and before events are checked
MIN_COUNTDOWN = WARNING_MSG_TIMES[-1][0] + 5

Report = Callable[[str], Awaitable[None]]


class StartResult(NamedTuple):
    # Towns whose shutdown was called off before anything happened to them
    cancelled: list[RFChannel]
    starting: list[RFChannel]


class RFTownShutdown:
    def __init__(self, manager: RFChannelManager):
        self.manager = manager
        # Towns going down or being kept down
        self.held: set[RFChannel] = set()
        self._tasks: dict[RFChannel, RFRestartTask] = {}
        self._run_tasks: set[asyncio.Task] = set()

    @property
    def active(self) -> bool:
        return bool(self.held)

    def stop(self, channels: list[RFChannel], seconds: int, force: bool, report: Report) -> list[RFChannel]:
        """Shuts down towns in the background. Returns those not already shut down."""
        towns = [channel for channel in channels if channel not in self.held]
        if towns:
            self.held.update(towns)
            run_task = asyncio.create_task(self._run(towns, seconds, force, report))
            self._run_tasks.add(run_task)
            run_task.add_done_callback(self._run_tasks.discard)
        return towns

    def pending(self, channels: list[RFChannel]) -> list[RFChannel]:
        """The towns among these that are held but have not gone down yet."""
        return [
            channel for channel in channels
            if channel in self.held and (channel not in self._tasks or not self._tasks[channel].finished())
        ]

    async def start(self, channels: list[RFChannel]) -> StartResult:
        """Cancels any shutdown still in progress for these towns, and starts those that need it."""
        tasks = {channel: self._tasks.pop(channel) for channel in channels if channel in self._tasks}

        stopping = [
            task.waiting_task for task in tasks.values()
            if not task.finished() and task.get_status() == RestartStatus.RESTARTING
        ]
        if stopping:
            await asyncio.wait(stopping)

        called_off = [
            channel for channel in channels
            if channel in self.held and (channel not in tasks or not tasks[channel].finished())
        ]
        # These are still up, but their players were already sent away
        restart_anyway = [channel for channel, task in tasks.items() if task.pre_restart_done and not task.finished()]
        for task in tasks.values():
            if not task.finished():
                task.cancel()

        versions = await self.manager.game_updater.installed_versions(channels)
        self.held.difference_update(channels)
        starting = [
            channel for channel in channels
            if (channel in restart_anyway or not versions[channel.channel_name])
            and not channel.is_restarting()
            and channel.queue_restart(
                1,
                mute_countdown=True,
                label="Starting up",
                reason=RestartReason.USER,
                overwrite_same_reason=True,
                force=True,
            )
        ]
        return StartResult([channel for channel in called_off if channel not in starting], starting)

    async def _run(self, towns: list[RFChannel], seconds: int, force: bool, report: Report) -> None:
        try:
            await self.manager.game_updater.finish_running_restarts(towns)
            queued = {
                channel: channel.queue_restart(
                    seconds,
                    label="Shutting down",
                    reason=RestartReason.SHUTDOWN,
                    overwrite_same_reason=True,
                    force=force,
                )
                for channel in towns
                if channel in self.held
            }
            if not queued:
                return
            self._tasks.update(queued)
            await asyncio.wait([task.waiting_task for task in queued.values()])

            # A town that was started again has been reported on already
            remaining = [channel for channel, task in queued.items() if self._tasks.get(channel) is task]
            if not remaining:
                return
            versions = await self.manager.game_updater.installed_versions(remaining)
            still_up = [name for name, version in versions.items() if version]
            if still_up:
                await report(f"These towns are still running: {', '.join(still_up)}")
            elif len(remaining) == len(self.manager.channels):
                await report("All towns have been shut down.")
            else:
                await report(f"Shut down {', '.join(channel.channel_name for channel in remaining)}.")
        except Exception as e:
            logger.error(f"Town shutdown failed: {e}", exc_info=True)
            await report(f"Shutdown failed: {e}")
