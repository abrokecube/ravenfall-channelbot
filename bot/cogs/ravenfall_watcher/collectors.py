from __future__ import annotations

from typing import TYPE_CHECKING, override

from bot.integrations.ravenfall import DungeonStage, RavenfallInstance

from .base_classes import BaseCollector

if TYPE_CHECKING:
    from bot.integrations.ravenfall import RavenfallService


class RestartBlocker(BaseCollector[RavenfallInstance]):
    """Collector that blocks restarts."""

    def __init__(
        self,
        instance: RavenfallInstance,
        ravenfall_service: RavenfallService,
        *,
        loop_interval: float = 2,
        fail_duration: float = 0,
        is_urgent_failure: bool = False,
    ) -> None:
        super().__init__(
            instance,
            loop_interval=loop_interval,
            fail_duration=fail_duration,
            is_urgent_failure=is_urgent_failure,
        )
        self.ravenfall_service: RavenfallService = ravenfall_service

    @override
    async def process(self) -> None:
        dungeon = await self.instance.get_dungeon()
        raid = await self.instance.get_raid()
        if not dungeon or not raid:
            self.set_status(failing=True, reason="Could not check Ravenfall's status.")
            return

        if not self.ravenfall_service.ravennest_is_online.is_set():
            self.set_status(failing=True, reason="Ravenfall servers are offline.")
        elif not self.ravenfall_service.ravennest_updater_is_online.is_set():
            self.set_status(failing=True, reason="Ravenfall's update checker is offline.")
        elif dungeon.stage != DungeonStage.NONE:
            self.set_status(failing=True, reason="A dungeon is active.")
        elif raid.started:
            self.set_status(failing=True, reason="A raid is active.")
        else:
            self.set_status(failing=False)
