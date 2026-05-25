from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict, deque
from datetime import timedelta
from enum import Enum, auto
from typing import TYPE_CHECKING, ClassVar, override

from msgspec import Struct
from pydantic import BaseModel, Field

from bot.cogs.ravenfall_watcher import RavenfallWatcherService
from bot.core.components import Cog, EventManager, GlobalContext, fire_and_forget
from bot.db.session import get_async_session
from bot.db.utils import KeyValueStore
from bot.integrations.ravenfall import DungeonStage, RavenfallService
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigModel, ConfigService
from bot.services.ravenfall_channels import RavenfallChannelService
from bot.services.ravenfall_multichat import RavenfallMultichatService
from utils.routines import routine
from utils.strutils import pl2

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
LOGGER = logging.getLogger(__name__)


class ScrollType(Enum):
    RAID = auto()
    DUNGEON = auto()


class QueueItem(Struct, tag_field="type", tag="base"):
    scroll: ScrollType

    async def consume(self, db_session: AsyncSession, g_ctx: GlobalContext) -> None:  # pyright: ignore[reportUnusedParameter]
        """Called when the scroll gets used."""
        raise NotImplementedError

    async def refund(self, db_session: AsyncSession, g_ctx: GlobalContext) -> None:  # pyright: ignore[reportUnusedParameter]
        """Called when the scroll was cleared from the queue
        without being used.
        """
        raise NotImplementedError


type QueueItems = QueueItem


class SQInstanceConfig(BaseModel):
    channel_name: str
    max_queue_size: int = 25
    dungeon_scroll_size: int | None = None
    dungeon_scroll_cost: int | None = None
    raid_scroll_size: int | None = None
    raid_scroll_cost: int | None = None


class RFScrollQueueConfig(ConfigModel):
    config_table_name: ClassVar[str | None] = "cogs.ravenfall_scroll_queue"
    instances: list[SQInstanceConfig] = Field(default_factory=list)
    dungeon_scroll_size: int = 6
    dungeon_scroll_cost: int = 20
    raid_scroll_size: int = 1
    raid_scroll_cost: int = 12


class ScrollQueue:
    def __init__(
        self,
        queue: list[QueueItems],
        config: SQInstanceConfig,
        global_config: RFScrollQueueConfig,
        g_ctx: GlobalContext,
    ) -> None:
        self.queue: deque[QueueItems] = deque(queue)
        self.config: SQInstanceConfig = config
        self.g_ctx: GlobalContext = g_ctx
        self.channel_name: str = self.config.channel_name
        self.dungeon_scroll_size: int = (
            global_config.dungeon_scroll_size
            if config.dungeon_scroll_size is None
            else config.dungeon_scroll_size
        )
        self.dungeon_scroll_cost: int = (
            global_config.dungeon_scroll_cost
            if config.dungeon_scroll_cost is None
            else config.dungeon_scroll_cost
        )
        self.raid_scroll_size: int = (
            global_config.raid_scroll_size
            if config.raid_scroll_size is None
            else config.raid_scroll_size
        )
        self.raid_scroll_cost: int = (
            global_config.raid_scroll_cost
            if config.raid_scroll_cost is None
            else config.raid_scroll_cost
        )

    async def start(self):
        """Start scroll queue operations."""

    async def teardown(self):
        """Tear down scroll queue operations."""

    async def save_to_db(self, kv_store: KeyValueStore):
        """Save this queue to the database."""
        async with get_async_session() as session:
            await kv_store.set(
                session, f"{self.config.channel_name}.queue", list(self.queue)
            )

    @routine(delta=timedelta(seconds=30))
    async def scroll_queue_routine(self):
        """Scroll queue main loop."""
        rf_srv = self.g_ctx.require_service(RavenfallService)
        rf_multichat_srv = self.g_ctx.require_service(RavenfallMultichatService)
        rf_channel_srv = self.g_ctx.require_service(RavenfallChannelService)
        multichat = rf_multichat_srv.get_client()
        rf_instance = rf_srv.get_ravenfall_instance(channel_name=self.channel_name)
        if not rf_instance:
            return
        rf_watcher_srv = self.g_ctx.require_service(RavenfallWatcherService)
        watcher = None
        with contextlib.suppress(ValueError):
            watcher = rf_watcher_srv.get_watcher(self.channel_name)
        has_channels = False
        with contextlib.suppress(ValueError):
            has_channels = len(rf_channel_srv.get_channels(rf_instance.channel_name)) > 0

        if not rf_srv.ravennest_is_online.is_set():
            return
        if not rf_instance.is_online.is_set():
            return
        if not rf_instance.is_ready.is_set():
            return
        if watcher:
            if watcher.ravenfall_restart_lock.locked():
                return
            restart_task = watcher.get_restart_task_info()
            if restart_task.is_announced:
                return
        if len(self.queue) == 0:
            return

        dungeon = await rf_instance.get_dungeon()
        raid = await rf_instance.get_raid()
        if not dungeon or not raid:
            return
        if dungeon.stage != DungeonStage.NONE:
            return
        if raid.started:
            return

        next_queue_item = self.queue[0]
        scrolls = await multichat.get_scroll_counts(rf_instance.channel_id)
        if next_queue_item.scroll == ScrollType.DUNGEON:
            stock = scrolls.channel.dungeon_scroll
        else:
            stock = scrolls.channel.raid_scroll
        if stock <= 0:
            skip_count = 0
            async with get_async_session() as session:
                while (
                    len(self.queue) > 0 and self.queue[0].scroll == next_queue_item.scroll
                ):
                    await self.queue.popleft().refund(session, self.g_ctx)
                    skip_count += 1
            LOGGER.info(
                f"[{self.channel_name}] skipped {skip_count} "
                f"{next_queue_item.scroll.name} scroll(s) due to depleted stock"
            )
            if has_channels:
                await rf_channel_srv.send_global_message(
                    f"Skipped {skip_count} {next_queue_item.scroll.name.lower()} "
                    f"{pl2(skip_count, 'scroll', 'scrolls', False)} "
                    f"due to depleted stock.",
                    "scroll_queue.skip.no_stock",
                    rf_instance.channel_name,
                )
            return


class RFScrollQueueCog(Cog, ConfigSubscriberMixin):
    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)
        self.queues: dict[str, ScrollQueue] = defaultdict()
        self.config: RFScrollQueueConfig = RFScrollQueueConfig()
        self.kv_db: KeyValueStore = KeyValueStore("RFScrollQueueCog")

    @override
    async def setup(self) -> None:
        config_srv = await self.global_context.wait_for_service(ConfigService)
        self.inject_config_service(config_srv)
        self.config = self.subscribe_config(RFScrollQueueConfig)
        await self.load_queues()

    @override
    async def teardown(self) -> None:
        __ = await asyncio.gather(*[x.teardown() for x in self.queues.values()])

    async def load_queues(self):
        """Load saved queues."""
        if self.queues:
            __ = await asyncio.gather(*[x.teardown() for x in self.queues.values()])
        self.queues.clear()
        async with get_async_session() as session:
            for instance in self.config.instances:
                data = await self.kv_db.get(
                    session, f"{instance.channel_name}.queue", list[QueueItem]
                )
                if data is None:
                    continue
                self.queues[instance.channel_name] = ScrollQueue(
                    data, instance, self.config, self.global_context
                )

    @override
    def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ) -> None:
        if not isinstance(config, RFScrollQueueConfig):
            return
        self.config = config
        fire_and_forget(self.load_queues())
