from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, override

from bot.integrations.process_manager import ProcessStatistics
from bot.integrations.ravenfall import DungeonStage, RavenfallInstance

from .base_classes import BaseCollector, BaseGroupCollector

if TYPE_CHECKING:
    from bot.integrations.process_manager import ProcessManagerService
    from bot.integrations.ravenfall import RavenfallService
    from bot.services.ravenfall_multichat import RavenfallMultichatService

    from .cog import RavenfallWatcherCog
    from .watcher import RavenfallWatcher

LOGGER = logging.getLogger(__name__)


class RestartBlocker(BaseCollector[RavenfallInstance]):
    """Collector that blocks restarts."""

    def __init__(
        self,
        instance: RavenfallInstance,
        ravenfall_service: RavenfallService,
        watcher_cog: RavenfallWatcherCog,
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
        self.force_alerting: bool = False
        self.watcher_cog: RavenfallWatcherCog = watcher_cog

    @override
    async def process(self) -> None:
        if self.force_alerting:
            self.set_status(failing=True, reason="Test restart blocking.")
            return
        dungeon = await self.instance.get_dungeon()
        raid = await self.instance.get_raid()
        if not dungeon or not raid:
            # self.set_status(failing=True, reason="Could not check Ravenfall's status.")
            self.set_status(failing=False)
            return

        if not self.ravenfall_service.ravennest_is_online.is_set():
            self.set_status(failing=True, reason="Ravenfall servers are offline.")
        elif not self.ravenfall_service.ravennest_updater_is_online.is_set():
            self.set_status(failing=True, reason="Ravenfall's update checker is offline.")
        elif dungeon.stage != DungeonStage.NONE:
            self.set_status(failing=True, reason="A dungeon is active.")
        elif raid.started:
            self.set_status(failing=True, reason="A raid is active.")
        elif self.watcher_cog.restart_lock.locked():
            self.set_status(
                failing=True,
                reason="Waiting for another Ravenfall instance to finish restarting.",
            )
        else:
            self.set_status(failing=False)


class MultiplierCheck(BaseGroupCollector[RavenfallInstance]):
    """Checks if an instance's multiplier is in sync with the global multiplier."""

    def __init__(
        self,
        instances: list[RavenfallInstance],
        ravenfall_service: RavenfallService,
        *,
        loop_interval: float = 30,
        fail_duration: float = 120,
        is_urgent_failure: bool = False,
    ) -> None:
        super().__init__(
            instances,
            loop_interval=loop_interval,
            fail_duration=fail_duration,
            is_urgent_failure=is_urgent_failure,
        )
        self.ravenfall_service: RavenfallService = ravenfall_service

    @override
    async def process(self) -> None:
        global_mult = await self.ravenfall_service.get_multiplier()
        if not global_mult:
            return
        if global_mult.multiplier == 1:
            for instance in self.instances:
                self.set_status(instance, failing=False)
            return
        for instance in self.instances:
            instance_mult = await instance.get_multiplier()
            if not instance_mult:
                self.set_status(instance, failing=False)
                continue
            if global_mult.multiplier > instance_mult.multiplier:
                reason = (
                    f"Town is desynced; global multiplier {global_mult.multiplier:.0f}x "
                    f"is higher than instance multiplier {instance_mult.multiplier:.0f}x."
                )
                self.set_status(instance, failing=True, reason=reason)
            else:
                self.set_status(instance, failing=False)


class ItemCountCheck(BaseGroupCollector[RavenfallInstance]):
    """Checks if an instance's total item count is changing."""

    def __init__(
        self,
        instances: list[RavenfallInstance],
        multichat_service: RavenfallMultichatService,
        *,
        loop_interval: float = 30,
        fail_duration: float = 3 * 60,
        is_urgent_failure: bool = False,
    ) -> None:
        super().__init__(
            instances,
            loop_interval=loop_interval,
            fail_duration=fail_duration,
            is_urgent_failure=is_urgent_failure,
        )
        self.multichat_service: RavenfallMultichatService = multichat_service
        self.last_item_counts: dict[str, int] = defaultdict(int)

    @override
    async def process(self) -> None:
        item_counts_response = (
            await self.multichat_service.get_client().get_total_item_count()
        )
        item_counts = dict(item_counts_response.towns)
        for instance in self.instances:
            if instance.channel_id not in item_counts:
                self.set_status(instance, failing=False)
            else:
                last_count = self.last_item_counts[instance.channel_id]
                current_count = item_counts[instance.channel_id]
                if current_count != last_count:
                    self.set_status(instance, failing=False)
                else:
                    self.set_status(
                        instance,
                        failing=True,
                        reason="Town is desynced; items stopped getting rewarded.",
                    )
                self.last_item_counts[instance.channel_id] = current_count


class RamUsageCheck(BaseGroupCollector[RavenfallInstance]):
    """Checks if an instance's RAM usage is too high."""

    def __init__(
        self,
        instances: list[RavenfallInstance],
        process_manager_service: ProcessManagerService,
        watcher_cog: RavenfallWatcherCog,
        instance_watchers: list[RavenfallWatcher],
        *,
        loop_interval: float = 60,
        fail_duration: float = 10 * 60,
        is_urgent_failure: bool = False,
    ) -> None:
        super().__init__(
            instances,
            loop_interval=loop_interval,
            fail_duration=fail_duration,
            is_urgent_failure=is_urgent_failure,
        )
        self.process_manager_service: ProcessManagerService = process_manager_service
        self.watchers: list[RavenfallWatcher] = instance_watchers
        self.watcher_cog: RavenfallWatcherCog = watcher_cog

    @override
    async def process(self) -> None:
        tasks = [
            self.process_manager_service.get_process_statistics(
                "Ravenfall", i.config.sandboxie_box_name
            )
            for i in self.watchers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        instance_result_watcher = tuple(
            filter(
                lambda x: (
                    not (isinstance(x[1], BaseException) or x[1].uptime_seconds is None)
                ),
                zip(self.instances, results, self.watchers, strict=True),
            )
        )
        total_ram_usage_bytes = 0
        for instance, result, watcher in instance_result_watcher:
            if isinstance(result, BaseException):
                LOGGER.exception(
                    f"Error getting process statistics for {instance.channel_name}"
                )
                self.set_status(instance, failing=False)
                continue
            if result.uptime_seconds is None:
                self.set_status(instance, failing=False)
                continue
            LOGGER.debug(
                f"Instance {instance.channel_name} "
                f"memory usage: {result.memory_rss_bytes / (1024**3):.2f} GB."
            )
            if result.memory_rss_bytes > (
                watcher.config.max_memory_usage_gb
                or self.watcher_cog.config.default_max_instance_memory_usage_gb
            ):
                reason = "High memory usage by this instance."
                self.set_status(instance, failing=True, reason=reason)
            total_ram_usage_bytes += result.memory_rss_bytes

        if self.watcher_cog.config.max_total_memory_use_gb is None:
            return
        if total_ram_usage_bytes < self.watcher_cog.config.max_total_memory_use_gb * (
            1024**3
        ):
            return
        reason = "High memory usage."

        def sort_key_a(
            x: tuple[
                RavenfallInstance, ProcessStatistics | BaseException, RavenfallWatcher
            ],
        ) -> float:
            stats = x[1]
            if isinstance(stats, BaseException) or stats.uptime_seconds is None:
                return 0
            return stats.uptime_seconds

        instance_result_watcher = tuple(
            sorted(instance_result_watcher, key=sort_key_a, reverse=True)
        )
        min_threshold_bytes = self.watcher_cog.config.memory_kill_min_threshold_gb * (
            1024**3
        )
        instance_result_watcher_filtered = tuple(
            filter(
                lambda x: (
                    isinstance(x[1], ProcessStatistics)
                    and x[1].memory_rss_bytes > min_threshold_bytes
                ),
                instance_result_watcher,
            )
        )
        if len(instance_result_watcher_filtered) == 0:
            instance_result_watcher_filtered = instance_result_watcher

        self.set_status(
            instance_result_watcher_filtered[0][0],
            failing=True,
            reason=reason,
        )


class BuggedRaidCheck(BaseCollector[RavenfallInstance]):
    """Checks if an instance is in a bugged raid state."""

    def __init__(
        self,
        instance: RavenfallInstance,
        *,
        loop_interval: float = 10,
        fail_duration: float = 25,
        is_urgent_failure: bool = False,
    ) -> None:
        super().__init__(
            instance,
            loop_interval=loop_interval,
            fail_duration=fail_duration,
            is_urgent_failure=is_urgent_failure,
        )

    @override
    async def process(self) -> None:
        players = await self.instance.get_players()
        if not players:
            self.set_status(failing=False)
            return
        raid = await self.instance.get_raid()
        if not raid:
            self.set_status(failing=False)
            return
        if raid.started:
            self.set_status(failing=False)
            return
        if any(p.in_raid for p in players):
            self.set_status(
                failing=True,
                reason="The current raid is bugged.",
            )
            return
        self.set_status(failing=False)
