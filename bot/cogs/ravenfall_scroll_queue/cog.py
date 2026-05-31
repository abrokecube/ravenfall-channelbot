from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from datetime import timedelta
from enum import Enum, auto
from math import inf
from typing import TYPE_CHECKING, ClassVar, Literal, NamedTuple, cast, override

from msgspec import Struct, convert
from pydantic import BaseModel, Field
from twitchAPI.type import CustomRewardRedemptionStatus

from bot.cogs.accounts.service import AccountService
from bot.cogs.currency import CurrencyService
from bot.cogs.ravenfall_scroll_queue.exceptions import (
    InsufficientQueueSpaceError,
    OutOfStockError,
    QueueFullError,
)
from bot.cogs.ravenfall_watcher import RavenfallWatcherService
from bot.core.components import Cog, EventManager, GlobalContext
from bot.core.decorators import on_match
from bot.db.session import get_async_session
from bot.db.utils import KeyValueStore
from bot.integrations.chat_messages import UserRole
from bot.integrations.chat_messages.deco import checks
from bot.integrations.chat_messages.utils import min_permission_level
from bot.integrations.commands import (
    Choice,
    CommandError,
    CommandEvent,
    MinPermissionLevel,
    RangeInt,
    command,
    parameter,
)
from bot.integrations.ravenfall import (
    DungeonEndedEvent,
    DungeonSpawnedEvent,
    DungeonStage,
    RaidEndedEvent,
    RaidStartedEvent,
    RavenfallEvent,
    RavenfallInstance,
    RavenfallInstanceConverter,
    RavenfallMessageEvent,
    RavenfallReadyEvent,
    RavenfallService,
)
from bot.integrations.twitch import TwitchRedemptionEvent, TwitchService, on_twitch_redeem
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigModel, ConfigService
from bot.services.event_waiter import EventTypePredicate, EventWaiterService
from bot.services.ravenfall_channels import RavenfallChannelService
from bot.services.ravenfall_multichat import RavenfallMultichatService
from utils.routines import routine
from utils.strutils import pl2

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from sqlalchemy.ext.asyncio import AsyncSession
LOGGER = logging.getLogger(__name__)


class ScrollType(Enum):
    RAID = auto()
    DUNGEON = auto()


class BaseQueueItem(Struct):
    queue_item_types: ClassVar[dict[str, type[BaseQueueItem]]] = {}
    queue_item_name: ClassVar[str]
    scroll: ScrollType

    def __init_subclass__(
        cls,
        rename: None
        | Literal["lower", "upper", "camel", "pascal", "kebab"]
        | Callable[[str], str | None]
        | Mapping[str, str] = None,
        omit_defaults: bool = False,  # noqa: FBT001, FBT002
        forbid_unknown_fields: bool = False,  # noqa: FBT001, FBT002
        frozen: bool = False,  # noqa: FBT001, FBT002
        eq: bool = True,  # noqa: FBT001, FBT002
        order: bool = False,  # noqa: FBT001, FBT002
        kw_only: bool = False,  # noqa: FBT001, FBT002
        repr_omit_defaults: bool = False,  # noqa: FBT001, FBT002
        array_like: bool = False,  # noqa: FBT001, FBT002
        gc: bool = True,  # noqa: FBT001, FBT002
        weakref: bool = False,  # noqa: FBT001, FBT002
        dict: bool = False,  # noqa: A002, FBT001, FBT002
        cache_hash: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        super().__init_subclass__(
            cls.queue_item_name,
            "type",
            rename,
            omit_defaults,
            forbid_unknown_fields,
            frozen,
            eq,
            order,
            kw_only,
            repr_omit_defaults,
            array_like,
            gc,
            weakref,
            dict,
            cache_hash,
        )
        if cls.queue_item_name in cls.queue_item_types:
            msg = (
                f"Queue item class {cls.__name__} is trying to use the name "
                f"{cls.queue_item_name}, but it has already been taken by "
                f"{cls.queue_item_types[cls.queue_item_name].__name__}"
            )
            raise RuntimeError(msg)
        BaseQueueItem.queue_item_types[cls.queue_item_name] = cls

    async def consume(
        self,
        db_session: AsyncSession,  # pyright: ignore[reportUnusedParameter]
        g_ctx: GlobalContext,  # pyright: ignore[reportUnusedParameter]
        queue: ScrollQueue,  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Called when the scroll gets used."""

    async def refund(
        self,
        db_session: AsyncSession,  # pyright: ignore[reportUnusedParameter]
        g_ctx: GlobalContext,  # pyright: ignore[reportUnusedParameter]
        queue: ScrollQueue,  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Called when the scroll was cleared from the queue
        without being used.
        """


class QueueItem(BaseQueueItem):
    queue_item_name: ClassVar[str] = "base"


class CurrencyQueueItem(BaseQueueItem):
    queue_item_name: ClassVar[str] = "currency"
    account_id: str
    cost: int

    @override
    async def refund(
        self,
        db_session: AsyncSession,
        g_ctx: GlobalContext,
        queue: ScrollQueue,
    ) -> None:
        currency_srv = g_ctx.require_service(CurrencyService)
        __ = await currency_srv.add_currency(
            self.account_id, self.cost, "Refund for unused scroll", db_session
        )


class TwitchRedeemQueueItem(BaseQueueItem):
    queue_item_name: ClassVar[str] = "twitch_redeem"
    reward_id: str
    redemption_id: str

    @override
    async def consume(
        self, db_session: AsyncSession, g_ctx: GlobalContext, queue: ScrollQueue
    ) -> None:
        ravenfall = g_ctx.require_service(RavenfallService).get_ravenfall_instance(
            channel_name=queue.channel_name
        )
        if not ravenfall:
            return
        twitch = g_ctx.require_service(TwitchService).get_twitch_channel(
            ravenfall.channel_id
        )
        if not twitch:
            return
        __ = await twitch.update_redemption_status(
            self.reward_id, self.redemption_id, CustomRewardRedemptionStatus.FULFILLED
        )

    @override
    async def refund(
        self, db_session: AsyncSession, g_ctx: GlobalContext, queue: ScrollQueue
    ) -> None:
        ravenfall = g_ctx.require_service(RavenfallService).get_ravenfall_instance(
            channel_name=queue.channel_name
        )
        if not ravenfall:
            return
        twitch = g_ctx.require_service(TwitchService).get_twitch_channel(
            ravenfall.channel_id
        )
        if not twitch:
            return
        __ = await twitch.update_redemption_status(
            self.reward_id, self.redemption_id, CustomRewardRedemptionStatus.CANCELED
        )


class QueueResult(NamedTuple):
    """Result of attempting to add scrolls to the queue.

    Attributes:
        added: List of queue items that were successfully added.
        failed: List of queue items that could not be added.
        free_scroll: The queue item that was added for free, or None.
        warning: A warning message if not all items could be added.
    """

    added: list[BaseQueueItem]
    failed: list[BaseQueueItem]
    free_scroll: BaseQueueItem | None
    warning: str


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
        queue: list[BaseQueueItem],
        config: SQInstanceConfig,
        global_config: RFScrollQueueConfig,
        g_ctx: GlobalContext,
        kv_store: KeyValueStore,
    ) -> None:
        self.queue: deque[BaseQueueItem] = deque(queue)
        self.config: SQInstanceConfig = config
        self.g_ctx: GlobalContext = g_ctx
        self.kv_store: KeyValueStore = kv_store
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
        self.scroll_queue_routine_lock: asyncio.Lock = asyncio.Lock()

    async def start(self):
        """Start scroll queue operations."""
        __ = await self.g_ctx.wait_for_service(RavenfallService)
        __ = await self.g_ctx.wait_for_service(RavenfallMultichatService)
        __ = await self.g_ctx.wait_for_service(RavenfallChannelService)
        __ = await self.g_ctx.wait_for_service(RavenfallWatcherService)
        __ = await self.g_ctx.wait_for_service(TwitchService)
        __ = await self.g_ctx.wait_for_service(EventWaiterService)
        __ = await self.g_ctx.wait_for_service(AccountService)
        __ = await self.g_ctx.wait_for_service(CurrencyService)
        __ = self.scroll_queue_routine.start()

    async def teardown(self):
        """Tear down scroll queue operations."""
        __ = self.scroll_queue_routine.stop()

    async def save_to_db(self, session: AsyncSession):
        """Save this queue to the database."""
        await self.kv_store.set(
            session, f"{self.config.channel_name}.queue", list(self.queue)
        )

    @routine(delta=timedelta(seconds=30))
    async def scroll_queue_routine(self):
        """Scroll queue main loop."""
        if self.scroll_queue_routine_lock.locked():
            async with self.scroll_queue_routine_lock:
                pass

        async with self.scroll_queue_routine_lock:
            rf_srv = self.g_ctx.require_service(RavenfallService)
            rf_multichat_srv = self.g_ctx.require_service(RavenfallMultichatService)
            rf_channel_srv = self.g_ctx.require_service(RavenfallChannelService)
            waiter_srv = self.g_ctx.require_service(EventWaiterService)
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
                has_channels = (
                    len(rf_channel_srv.get_channels(rf_instance.channel_name)) > 0
                )

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
                command = "?ds"
                success_messages = {"dungeon_scroll_used_joined", "dungeon_start_failed"}
                fail_messages = {"dungeon_scrolls_missing"}
                start_event = DungeonSpawnedEvent
            else:
                stock = scrolls.channel.raid_scroll
                command = "?rs"
                success_messages = {"raid_scroll_used_joined", "raid_start_failed"}
                fail_messages = {"raid_scrolls_missing"}
                start_event = RaidStartedEvent
            if stock <= 0:
                skip_count = 0
                while (
                    len(self.queue) > 0 and self.queue[0].scroll == next_queue_item.scroll
                ):
                    await self.refund_scroll()
                    skip_count += 1
                LOGGER.info(
                    f"[{self.channel_name}] skipped {skip_count} "
                    f"{next_queue_item.scroll.name} scroll(s) due to depleted stock"
                )
                if has_channels:
                    await rf_channel_srv.send_global_message(
                        f"Skipped {skip_count} "
                        f"{next_queue_item.scroll.name.capitalize()} "
                        f"{pl2(skip_count, 'scroll', 'scrolls', False)} "
                        f"due to depleted stock.",
                        "scroll_queue.skip.no_stock",
                        rf_instance.channel_name,
                    )
                __ = await self.scroll_queue_routine()  # pyright: ignore[reportAny]
                return

            all_messages = success_messages.union(fail_messages)
            message_task = asyncio.create_task(
                waiter_srv.wait_for_multiple(
                    [
                        EventTypePredicate[RavenfallMessageEvent](
                            RavenfallMessageEvent,
                            lambda e: (
                                e.ravenfall.channel_id == rf_instance.channel_id
                                and e.message.identifier in all_messages
                            ),
                        ),
                        EventTypePredicate[RavenfallEvent](
                            start_event,
                            lambda e: e.ravenfall.channel_id == e.ravenfall.channel_id,
                        ),
                    ],
                    timeout=15,
                ),
            )
            await rf_channel_srv.send_multichat_command(command, rf_instance.channel_name)
            try:
                response = await message_task
            except TimeoutError:
                __ = await self.scroll_queue_routine()  # pyright: ignore[reportAny]
                return
            if isinstance(response, start_event) or (
                isinstance(response, RavenfallMessageEvent)
                and response.message.identifier in success_messages
            ):
                await self.consume_scroll()
                return
            __ = await self.scroll_queue_routine()  # pyright: ignore[reportAny]

    async def consume_scroll(self, count: int = 1):
        """Called when a scroll was successfully used."""
        async with get_async_session() as session:
            while count > 0 and len(self.queue) > 0:
                scroll = self.queue.popleft()
                try:
                    await scroll.consume(session, self.g_ctx, self)
                except Exception:
                    LOGGER.exception(f"[{self.channel_name}] Failed to consume scroll")
                count -= 1
            await self.save_to_db(session)

    async def refund_scroll(self, count: int = 1):
        """Called when a scroll was unsuccessfully used."""
        async with get_async_session() as session:
            while count > 0 and len(self.queue) > 0:
                scroll = self.queue.popleft()
                try:
                    await scroll.refund(session, self.g_ctx, self)
                except Exception:
                    LOGGER.exception(f"[{self.channel_name}] Failed to refund scroll")
                count -= 1
            await self.save_to_db(session)

    async def refund_scrolls_after_pos(self, start_pos: int = 0):
        """Refund all scrolls after the given queue position."""
        async with get_async_session() as session:
            target_len = min(start_pos, len(self.queue))
            scrolls: list[BaseQueueItem] = []
            while len(self.queue) > target_len:
                scrolls.append(self.queue.pop())
            for scroll in scrolls:
                try:
                    await scroll.refund(session, self.g_ctx, self)
                except Exception:
                    LOGGER.exception(f"[{self.channel_name}] Failed to refund scroll")
            await self.save_to_db(session)

    async def add_scroll(
        self,
        scroll: BaseQueueItem,
        db_session: AsyncSession,
        *,
        ignore_max_length: bool = False,
    ):
        """Add a scroll to the queue."""
        if self.get_queue_size() >= self.config.max_queue_size and not ignore_max_length:
            raise QueueFullError("Queue is full", self)
        self.queue.append(scroll)
        await self.save_to_db(db_session)

    async def queue_scrolls(
        self, instance: RavenfallInstance, queue_items: list[BaseQueueItem]
    ) -> QueueResult:
        """Queue one or more scrolls, validating stock and space constraints.

        Args:
            instance: The Ravenfall instance.
            queue_items: A list of queue items to try to add.

        Returns:
            QueueResult: A NamedTuple containing added items, failed items,
                free scroll (if any), and warning message.

        Raises:
            QueueFullError: If the queue is already full.
            OutOfStockError: If the scroll type is out of stock.
            InsufficientQueueSpaceError: If the queue does not have enough space
                for a single scroll of this type.
        """
        if not queue_items:
            return QueueResult([], [], None, "")

        scroll_obj = queue_items[0].scroll
        scroll_type = "dungeon" if scroll_obj == ScrollType.DUNGEON else "raid"
        scroll_size = (
            self.dungeon_scroll_size
            if scroll_obj == ScrollType.DUNGEON
            else self.raid_scroll_size
        )

        queue_size = self.get_queue_size()
        if queue_size >= self.config.max_queue_size:
            raise QueueFullError("The queue is full. Try again later.", self)

        rf_multichat_srv = self.g_ctx.require_service(RavenfallMultichatService)
        stock = inf
        scroll_stock = None
        try:
            scroll_stock = await rf_multichat_srv.get_client().get_scroll_counts(
                instance.channel_id
            )
        except Exception:
            LOGGER.exception("Failed to get scroll counts")

        if scroll_stock:
            if scroll_obj == ScrollType.DUNGEON:
                stock = scroll_stock.channel.dungeon_scroll
            else:
                stock = scroll_stock.channel.raid_scroll

        stock -= self.get_scroll_count(scroll_obj)
        if stock <= 0:
            msg = (
                f"We are currently out of {scroll_type.capitalize()} scrolls. "
                "Try again later."
            )
            raise OutOfStockError(msg, self)

        available_space = self.config.max_queue_size - queue_size
        if available_space < scroll_size:
            msg = (
                f"The queue does not have enough space "
                f"for a {scroll_type.capitalize()} Scroll."
            )
            raise InsufficientQueueSpaceError(msg, self)

        ravenfall_srv = self.g_ctx.require_service(RavenfallService)
        can_add_one_without_credits = await self.check_for_free_scroll(
            instance, ravenfall_srv
        )

        max_can_add = available_space // scroll_size
        if can_add_one_without_credits:
            max_can_add += 1

        count = len(queue_items)
        to_add = min(count, max_can_add)

        added_items = list(queue_items[:to_add])
        failed_items = list(queue_items[to_add:])

        warning_msg = ""
        if max_can_add < count:
            warning_msg = "Queue is full"

        free_scroll = None
        if can_add_one_without_credits and to_add > 0:
            free_scroll = added_items[0]
            added_items = added_items[1:]

        async with get_async_session() as session:
            for item in added_items:
                self.queue.append(item)
            await self.save_to_db(session)

        self.scroll_queue_routine.restart()

        return QueueResult(
            added=added_items,
            failed=failed_items,
            free_scroll=free_scroll,
            warning=warning_msg,
        )

    def get_queue_size(self):
        """Get size of items currently in the queue."""
        size = 0
        for item in self.queue:
            if item.scroll == ScrollType.DUNGEON:
                size += self.dungeon_scroll_size
            else:
                size += self.raid_scroll_size
        return size

    def get_length(self):
        """Get length of queue."""
        return len(self.queue)

    def get_scroll_count(self, scroll_type: ScrollType | None = None):
        """Count scrolls in queue."""
        if not scroll_type:
            return len(self.queue)
        count = 0
        for item in self.queue:
            if item.scroll == scroll_type:
                count += 1
        return count

    async def check_for_free_scroll(
        self, instance: RavenfallInstance, ravenfall_srv: RavenfallService
    ):
        """Check if we can add a free scroll to the queue."""
        can_add_one_without_credits = False
        queue_size = self.get_queue_size()
        if queue_size <= 0:
            dungeon = await instance.get_dungeon()
            raid = await instance.get_raid()
            if (
                dungeon is not None
                and raid is not None
                and dungeon.stage == DungeonStage.NONE
                and not raid.started
                and instance.is_ready.is_set()
                and ravenfall_srv.ravennest_is_online.is_set()
            ):
                can_add_one_without_credits = True
        return can_add_one_without_credits


class RFScrollQueueCog(Cog, ConfigSubscriberMixin):
    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)
        self.queues: dict[str, ScrollQueue] = {}
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
                    session, f"{instance.channel_name}.queue", list[dict[str, object]]
                )
                if data is None:
                    continue
                queue: list[BaseQueueItem] = []
                for i in data:
                    item = i.copy()
                    scroll_type = cast("str", item["type"])
                    scroll_cls = BaseQueueItem.queue_item_types.get(scroll_type)
                    if not scroll_cls:
                        LOGGER.error(f"Unknown queue type '{scroll_type}': {item}")
                        continue
                    del item["type"]
                    try:
                        convert_result = convert(item, scroll_cls, from_attributes=True)
                    except Exception:
                        LOGGER.exception(
                            f"Failed to convert scroll type '{scroll_type}' "
                            f"into {scroll_cls.__name__}. Data: {item}"
                        )
                        continue
                    queue.append(convert_result)
                new_queue = ScrollQueue(
                    queue, instance, self.config, self.global_context, self.kv_db
                )
                self.queues[instance.channel_name] = new_queue
                await new_queue.start()

    @override
    async def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ) -> None:
        if not isinstance(config, RFScrollQueueConfig):
            return
        self.config = config
        await self.load_queues()

    def _get_queue_from_instance(self, instance: RavenfallInstance):
        if instance.channel_name not in self.queues:
            raise CommandError("Channel has no scroll queue.")
        return self.queues[instance.channel_name]

    @on_match(DungeonEndedEvent)
    async def _on_dungeon_end(self, event: DungeonEndedEvent, _match: object):
        if event.ravenfall.channel_name not in self.queues:
            return
        __ = await self.queues[event.ravenfall.channel_name].scroll_queue_routine()  # pyright: ignore[reportAny]

    @on_match(RaidEndedEvent)
    async def _on_raid_end(self, event: RaidEndedEvent, _match: object):
        if event.ravenfall.channel_name not in self.queues:
            return
        __ = await self.queues[event.ravenfall.channel_name].scroll_queue_routine()  # pyright: ignore[reportAny]

    @on_match(RavenfallReadyEvent)
    async def _on_instance_ready(self, event: RavenfallReadyEvent, _match: object):
        if event.ravenfall.channel_name not in self.queues:
            return
        __ = await self.queues[event.ravenfall.channel_name].scroll_queue_routine()  # pyright: ignore[reportAny]

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @command(name="queue", aliases=["q", "scrollqueue", "sq"])
    async def scroll_queue(
        self,
        ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Lists scrolls in the scroll queue."""
        queue = self._get_queue_from_instance(instance)
        queue_item_count = queue.get_length()
        queue_usage = queue.get_queue_size()
        queue_max = queue.config.max_queue_size

        if queue_usage <= 0:
            await ctx.reply(f"The queue is empty. ({queue_usage}/{queue_max})")
            return

        queue_scroll_counts: list[int] = [0]
        queue_scroll_types: list[ScrollType] = [queue.queue[0].scroll]
        for item in queue.queue:
            if item.scroll != queue_scroll_types[-1]:
                queue_scroll_counts.append(0)
                queue_scroll_types.append(item.scroll)
            queue_scroll_counts[-1] += 1

        queue_string = ", ".join(
            f"{x}x {y.name.capitalize()}"
            for x, y in zip(queue_scroll_counts, queue_scroll_types, strict=True)
        )

        await ctx.reply(
            f"{pl2(queue_item_count, 'scroll is', 'scrolls are')} "
            f"in the queue. ({queue_usage}/{queue_max} units) "
            f"Contents: {queue_string}"
        )

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @command(
        name="queue clear",
        aliases=["q clear", "qc", "clearscrollqueue", "csq", "trimscrollqueue", "tsq"],
    )
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    async def trim_scroll_queue(
        self,
        ctx: CommandEvent,
        start_pos: int = 0,
        *,
        instance: RavenfallInstance,
    ):
        """Lists scrolls in the scroll queue."""
        if instance.channel_id != ctx.message.room_id and not min_permission_level(
            ctx.message, UserRole.BOT_ADMINISTRATOR
        ):
            msg = "You do not have permission to specify an instance."
            raise CommandError(msg)
        queue = self._get_queue_from_instance(instance)
        old_len = queue.get_length()
        await queue.refund_scrolls_after_pos(start_pos)
        new_len = queue.get_length()

        await ctx.reply(
            f"{pl2(old_len - new_len, 'scroll', 'scrolls')} were removed from the queue."
        )

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter(name="scroll_type", converter=Choice(["dungeon", "raid"], title="Scroll"))
    @parameter(name="count", converter=RangeInt(1, None))
    @command(
        name="queue add",
        aliases=["q add", "qa", "queuescroll", "qs", "queuescrolls", "queue_scrolls"],
    )
    async def queue_scroll(
        self,
        ctx: CommandEvent,
        scroll_type: str,
        count: int = 1,
        *,
        instance: RavenfallInstance,
    ):
        """Queue one or more scrolls to be used."""
        ravenfall_srv = self.global_context.require_service(RavenfallService)
        account_srv = self.global_context.require_service(AccountService)
        currency_srv = self.global_context.require_service(CurrencyService)
        queue = self._get_queue_from_instance(instance)

        scroll_obj = ScrollType.DUNGEON if scroll_type == "dungeon" else ScrollType.RAID
        cost = (
            queue.dungeon_scroll_cost
            if scroll_obj == ScrollType.DUNGEON
            else queue.raid_scroll_cost
        )

        async with get_async_session() as session:
            user_account = await account_srv.get_or_create_account(
                session,
                ctx.message.platform,
                ctx.message.author_id,
                ctx.message.author_login,
                ctx.message.author_name,
            )
            balance = await currency_srv.get_balance(user_account.id, session)

        can_afford = balance // cost
        if can_afford == 0:
            msg = (
                f"You do not have enough credits to "
                f"queue a {scroll_type.capitalize()} Scroll. "
                f"You have {pl2(balance, 'credit', 'credits')}. "
                f"You need {pl2(cost, 'credit', 'credits')}."
            )
            raise CommandError(msg)

        can_add_one_without_credits = await queue.check_for_free_scroll(
            instance, ravenfall_srv
        )

        fail_text = ""

        to_add = count
        queue_items: list[BaseQueueItem] = []
        if can_add_one_without_credits:
            queue_items.append(QueueItem(scroll_obj))
            to_add -= 1

        if can_afford < to_add:
            fail_text = f"Not enough credits for {pl2(to_add, 'scroll', 'scrolls')}"

        for _ in range(to_add):
            queue_items.append(CurrencyQueueItem(scroll_obj, user_account.id, cost))  # noqa: PERF401

        try:
            result = await queue.queue_scrolls(instance, queue_items)
        except (QueueFullError, OutOfStockError, InsufficientQueueSpaceError) as e:
            raise CommandError(str(e)) from e

        added_count = len(result.added)
        if result.free_scroll:
            added_count += 1
        if added_count == 0:
            raise CommandError("Could not queue scroll.")

        final_cost = len(result.added) * cost
        if final_cost > 0:
            async with get_async_session() as session:
                scroll_pl = pl2(
                    len(result.added),
                    f"{scroll_type} scroll",
                    f"{scroll_type} scrolls",
                )
                __ = await currency_srv.remove_currency(
                    user_account.id,
                    final_cost,
                    f"Queued {scroll_pl}",
                    session,
                )

        if result.warning:
            fail_text = result.warning

        msg = (
            f"Added {added_count} {scroll_type.capitalize()} "
            f"{pl2(added_count, 'Scroll', 'Scrolls', include_number=False)} to the queue."
        )

        if final_cost > 0:
            msg += f" {final_cost} item credits were deducted."
        else:
            msg += " No item credits were deducted."

        if fail_text:
            msg += f" ({fail_text})"

        await ctx.reply(msg)

        await queue.scroll_queue_routine()

    async def queue_scroll_redeem(self, ctx: TwitchRedemptionEvent, scroll: ScrollType):
        """Handler for scroll queue redeems."""
        instance = self.g_ctx.require_service(RavenfallService).get_ravenfall_instance(
            channel_id=ctx.channel_id
        )
        if not instance:
            await ctx.cancel()
            return

        queue = self._get_queue_from_instance(instance)

        # Check if the first scroll is free (can_add_one_without_credits)
        can_add_one_without_credits = await queue.check_for_free_scroll(
            instance, self.global_context.require_service(RavenfallService)
        )

        if can_add_one_without_credits:
            item = BaseQueueItem(scroll)
        else:
            item = TwitchRedeemQueueItem(
                scroll=scroll,
                reward_id=ctx.data.reward.id,
                redemption_id=ctx.data.id,
            )

        scroll_name = "Dungeon" if scroll == ScrollType.DUNGEON else "Raid"
        try:
            result = await queue.queue_scrolls(instance, [item])
        except (QueueFullError, OutOfStockError, InsufficientQueueSpaceError) as e:
            raise CommandError(e.message) from e

        if result.added or result.free_scroll:
            await ctx.reply(f"Added a {scroll_name} Scroll to the queue.")
        else:
            raise CommandError("Failed to add scroll")

        if result.free_scroll is not None:
            await ctx.cancel()

    @on_twitch_redeem(lambda e: "queue_dungeon" in e.internal_keys)
    async def queue_dungeon(self, ctx: TwitchRedemptionEvent, _result: object):
        """Queue a dungeon scroll."""
        await self.queue_scroll_redeem(ctx, ScrollType.DUNGEON)

    @on_twitch_redeem(lambda e: "queue_raid" in e.internal_keys)
    async def queue_raid(self, ctx: TwitchRedemptionEvent, _result: object):
        """Queue a raid scroll."""
        await self.queue_scroll_redeem(ctx, ScrollType.RAID)
