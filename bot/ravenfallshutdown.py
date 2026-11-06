"""Shuts every town down, and brings them back up, on command.

Each town goes down through its normal restart task (countdown, pre restart, and
waiting for dungeons and raids), except that the game is not started again. From
the moment a shutdown is asked for until the towns are started again, restarts are
held, otherwise the bot would see the towns as crashed and bring them right back.

The hold only lives in memory, so restarting the bot brings the towns back too.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from .ravenfallrestarttask import RestartReason, RestartStatus, RFRestartTask, WARNING_MSG_TIMES

if TYPE_CHECKING:
    from .ravenfallchannel import RFChannel
    from .ravenfallmanager import RFChannelManager

logger = logging.getLogger(__name__)

# A shorter countdown would end before the pre restart runs and before events are checked
MIN_COUNTDOWN = WARNING_MSG_TIMES[-1][0] + 5

Report = Callable[[str], Awaitable[None]]


class RFTownShutdown:
    def __init__(self, manager: RFChannelManager):
        self.manager = manager
        self.active = False
        self._tasks: list[RFRestartTask] = []
        self._run_task: asyncio.Task | None = None

    def stop(self, seconds: int, force: bool, report: Report) -> bool:
        """Shuts down every town in the background. Returns False if already shut down."""
        if self.active:
            return False
        self.active = True
        self._run_task = asyncio.create_task(self._run(seconds, force, report))
        return True

    async def start(self) -> list[RFChannel]:
        """Cancels a shutdown still in progress, and starts every town that needs it.

        Returns the towns that are being started.
        """
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()
            await asyncio.wait([self._run_task])

        stopping = [
            task.waiting_task for task in self._tasks
            if not task.finished() and task.get_status() == RestartStatus.RESTARTING
        ]
        if stopping:
            await asyncio.wait(stopping)

        # These are still up, but their players were already sent away
        restart_anyway = [task.channel for task in self._tasks if task.pre_restart_done and not task.finished()]
        for task in self._tasks:
            if not task.finished():
                task.cancel()
        self._tasks = []

        versions = await self.manager.game_updater.installed_versions()
        self.active = False
        return [
            channel for channel in self.manager.channels
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

    async def _run(self, seconds: int, force: bool, report: Report) -> None:
        try:
            await self.manager.game_updater.finish_running_restarts()
            self._tasks = [
                channel.queue_restart(
                    seconds,
                    label="Shutting down",
                    reason=RestartReason.SHUTDOWN,
                    overwrite_same_reason=True,
                    force=force,
                )
                for channel in self.manager.channels
            ]
            await asyncio.wait([task.waiting_task for task in self._tasks])

            versions = await self.manager.game_updater.installed_versions()
            still_up = [name for name, version in versions.items() if version]
            if still_up:
                await report(f"These towns are still running: {', '.join(still_up)}")
            else:
                await report("All towns have been shut down.")
        except asyncio.CancelledError:
            logger.info("Town shutdown cancelled")
        except Exception as e:
            logger.error(f"Town shutdown failed: {e}", exc_info=True)
            await report(f"Shutdown failed: {e}")
