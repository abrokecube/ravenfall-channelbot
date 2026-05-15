from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING, Literal, override
from uuid import uuid4

from anyio import Path as AsyncPath
from msgspec import convert

import ravenpy
from bot.clients import ravenfall_middleman as rm
from bot.clients import ravenfall_query as rq
from bot.core.components import BaseEventSource
from bot.integrations.ravenfall.services import RavenfallService
from bot.services.config_service import ConfigService
from utils.utils import TimestampedValue, calculate_rate_per_second

from . import enums, models
from . import events as ev
from .enums import DungeonStage
from .matcher import RavenfallMatcher
from .models import RavenfallConfig

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from bot.core.components import EventManager
    from ravenpy.ravenpy import RavenNest

    from .events import RavenfallEvent
    from .matcher import Match
    from .models import RavenfallInstanceConfig
    from .payloads import BaseRavenBotPayload
    from .types import RavenfallInstanceEventHook

import logging

LOGGER = logging.getLogger(__name__)
RETURNED_NONE = -1


class BaseCollector[T]:
    """Collector."""

    def __init__(self, *, interval: float = 1, cache_time: float = 0.05) -> None:
        self.interval: float = interval
        self.cache_time: float = cache_time
        self._loop_task: asyncio.Task[None] | None = None
        self._last_data: T | None = None
        self._last_execution: float = time.monotonic() - self.interval
        self._processing_task: asyncio.Task[None] | None = None
        self._new_data_set: bool = False

    def start(self):
        """Start the processing loop."""
        self._loop_task = asyncio.create_task(self._loop())

    def stop(self):
        """Stop the loop."""
        if self._loop_task is not None:
            __ = self._loop_task.cancel()
            self._loop_task = None
        if self._processing_task:
            __ = self._processing_task.cancel()
            self._processing_task = None

    def set_data(self, data: T | None):
        """Set current data."""
        self._new_data_set = True
        self._last_data = data

    def get_data(self) -> T | None:
        """Get previously fetched data."""
        return self._last_data

    async def get_latest(self) -> T | None:
        """Fetch the latest data and return it."""
        if time.monotonic() - self._last_execution < self.cache_time:
            return self._last_data

        self._new_data_set = False
        if self._processing_task is not None:
            await self._processing_task
        else:
            self._processing_task = asyncio.create_task(self._run_process())
            await self._processing_task
            self._processing_task = None
        if self._new_data_set:
            return self._last_data
        return None

    async def pre_fetch(self):
        """Executes before data is fetched in the loop."""

    async def post_fetch(self):
        """Executes after data is fetched in the loop."""

    async def _loop(self):
        while True:
            try:
                await self.pre_fetch()
                t = time.monotonic()
                await asyncio.sleep(max(0, self.interval - (t - self._last_execution)))
                _ = await self.get_latest()
                await self.post_fetch()
            except Exception:
                LOGGER.exception(f"Error in collector loop {self.__class__.__name__}")

    async def _run_process(self):
        try:
            t = time.monotonic()
            await self.process()
            self._last_execution = t
        except Exception:
            LOGGER.exception(f"Error in collector {self.__class__.__name__}")

    async def process(self) -> None:
        """Code that fetches data.

        Use the set_data and get_data functions to store and retrieve data.
        """
        raise NotImplementedError


class RavenfallCollectorBase[T](BaseCollector[T]):
    """Fetches data from Ravenfall."""

    def __init__(
        self,
        ravenfall: RavenfallInstance,
        interval: float = 1,
        *,
        only_online: bool = True,
        cache_time: float = 0.05,
    ) -> None:
        super().__init__(interval=interval, cache_time=cache_time)
        self.ravenfall: RavenfallInstance = ravenfall
        self.only_online: bool = only_online

    @override
    async def get_latest(self) -> T | None:
        """Fetch the latest data and return it."""
        if self.only_online and not self.ravenfall.is_online.is_set():
            return None
        return await super().get_latest()

    @override
    async def pre_fetch(self):
        if self.only_online:
            _ = await self.ravenfall.is_online.wait()


class SessionCollector(RavenfallCollectorBase[models.GameSession]):
    """Ravenfall Session collector."""

    def __init__(
        self,
        ravenfall: RavenfallInstance,
        interval: float = 1,
        cache_time: float = 0.05,
    ) -> None:
        super().__init__(ravenfall, interval, only_online=False, cache_time=cache_time)
        self.warned_user: str | None = None

    @override
    async def process(self):
        ravenfall = self.ravenfall
        session = self.get_data()
        new_session = await ravenfall._query_request(
            self.ravenfall.query_client.get_session()
        )
        if new_session is None or new_session == RETURNED_NONE:
            return

        if (
            not (ravenfall.is_online.is_set())
            and new_session.authenticated
            and new_session.session_started
        ):
            LOGGER.info(f"Ravenfall ({ravenfall.channel_name}) is online.")
            await ravenfall._set_is_online()
        if new_session.twitch_username != ravenfall.twitch_login:
            if self.warned_user != new_session.twitch_username:
                LOGGER.warning(
                    f"Ravenfall ({ravenfall.channel_name}): "
                    "Received username does not match given twitch channel name "
                    f"(got {new_session.twitch_username})"
                )
            self.warned_user = new_session.twitch_username

        if not session:
            self.set_data(new_session)
            return

        self.set_data(new_session)


class FerryCollector(RavenfallCollectorBase[models.Ferry]):
    """Ravenfall Ferry collector."""

    def __init__(
        self,
        ravenfall: RavenfallInstance,
        interval: float = 1,
        cache_time: float = 0.05,
    ) -> None:
        super().__init__(ravenfall, interval, cache_time=cache_time)

    @override
    async def process(self):
        new_ferry = await self.ravenfall._query_request(
            self.ravenfall.query_client.get_ferry()
        )
        if new_ferry is None or new_ferry == RETURNED_NONE:
            return

        self.set_data(new_ferry)


class VillageCollector(RavenfallCollectorBase[models.Village]):
    """Ravenfall Village collector."""

    def __init__(
        self,
        ravenfall: RavenfallInstance,
        interval: float = 1,
        cache_time: float = 0.05,
    ) -> None:
        super().__init__(ravenfall, interval, cache_time=cache_time)

    @override
    async def process(self):
        new_village = await self.ravenfall._query_request(
            self.ravenfall.query_client.get_village()
        )
        if new_village is None or new_village == RETURNED_NONE:
            return

        self.set_data(new_village)


class MultiplierCollector(RavenfallCollectorBase[models.GameMultiplier]):
    """Ravenfall Multiplier collector."""

    def __init__(
        self,
        ravenfall: RavenfallInstance,
        interval: float = 5,
        cache_time: float = 0.05,
    ) -> None:
        super().__init__(ravenfall, interval, cache_time=cache_time)

    @override
    async def process(self):
        new_mult = await self.ravenfall._query_request(
            self.ravenfall.query_client.get_multiplier()
        )
        if new_mult is None or new_mult == RETURNED_NONE:
            return

        self.set_data(new_mult)


class PlayersCollector(RavenfallCollectorBase[list[models.Player]]):
    """Ravenfall Players collector."""

    def __init__(
        self,
        ravenfall: RavenfallInstance,
        interval: float = 0.5,
        cache_time: float = 0.05,
    ) -> None:
        super().__init__(ravenfall, interval, cache_time=cache_time)
        self.player_count_history: deque[tuple[float, int]] = deque(maxlen=10)

    @override
    async def process(self):
        new_players = await self.ravenfall._query_request(
            self.ravenfall.query_client.get_players()
        )
        if new_players is None or new_players == RETURNED_NONE:
            return

        t = time.monotonic()
        self.player_count_history.append((t, len(new_players)))
        min_items = 3
        if (
            not self.ravenfall.is_ready.is_set()
            and len(new_players) > 0
            and len(self.player_count_history) > min_items
        ):
            current_value = len(new_players)
            is_ready = True
            cutoff_t = t - 2
            for t0, count in reversed(self.player_count_history):
                if t0 < cutoff_t:
                    break
                if count != current_value:
                    is_ready = False
                    break
            if is_ready:
                LOGGER.info(f"Ravenfall ({self.ravenfall.channel_name}) is ready.")
                await self.ravenfall._set_is_ready()

        self.set_data(new_players)


class DungeonCollector(RavenfallCollectorBase[models.Dungeon]):
    """Ravenfall Dungeon collector."""

    def __init__(
        self,
        ravenfall: RavenfallInstance,
        interval: float = 0.5,
        cache_time: float = 0.05,
    ) -> None:
        super().__init__(ravenfall, interval, cache_time=cache_time)
        self.max_boss_hp: TimestampedValue = TimestampedValue(time.monotonic(), 0)
        self.stage: DungeonStage = DungeonStage.NONE

    @override
    async def process(self):
        new_dungeon_data = await self.ravenfall._query_request(
            self.ravenfall.query_client.get_dungeon()
        )
        if new_dungeon_data is None or new_dungeon_data == RETURNED_NONE:
            return

        new_dungeon = convert(new_dungeon_data, models.Dungeon, from_attributes=True)

        old_dungeon = self.get_data()
        old_stage = self.stage

        new_stage = DungeonStage.NONE
        if new_dungeon.enemies > 0:
            if not new_dungeon.started:
                if new_dungeon.boss.health == 0:
                    new_stage = DungeonStage.LOADING
                else:
                    new_stage = DungeonStage.WAITING_FOR_PLAYERS
            elif new_dungeon.enemies_alive > 0:
                new_stage = DungeonStage.FIGHTING_ENEMIES
            else:
                new_stage = DungeonStage.FIGHTING_BOSS
        new_dungeon.stage = new_stage

        if not old_dungeon:
            self.set_data(new_dungeon)
            self.stage = new_stage
            return

        t = time.monotonic()
        if new_dungeon.boss.health > self.max_boss_hp.value:
            self.max_boss_hp = TimestampedValue(t, new_dungeon.boss.health)

        if new_stage in {DungeonStage.FIGHTING_BOSS, DungeonStage.FIGHTING_ENEMIES}:
            boss = new_dungeon.boss
            boss.max_health = int(self.max_boss_hp.value)
            boss.health_percent = boss.health / self.max_boss_hp.value
            # new_dungeon = structs.replace(
            #     new_dungeon,
            #     boss=structs.replace(
            #         boss,
            #         max_health=self.max_boss_hp.value,
            #         health_percent=boss.health / self.max_boss_hp.value,
            #     ),
            # )

        # if new_stage != old_stage and new_stage != (old_stage + 1) % len(DungeonStage):
        stages_match = new_stage != old_stage
        if stages_match and (old_stage + 1) % len(DungeonStage):
            stage_funcs = [
                self._send_dungeon_end_event,
                partial(self._send_dungeon_spawned_event, new_dungeon),
                partial(self._send_dungeon_prepared_event, new_dungeon),
                partial(self._send_dungeon_started_event, new_dungeon),
                partial(self._send_dungeon_reached_boss_event, new_dungeon),
            ]
            idx = old_stage
            while idx != new_stage:
                idx += 1
                idx %= len(DungeonStage)
                func = stage_funcs[idx]
                if func is not None:
                    await func()
        elif stages_match:
            if new_stage == DungeonStage.NONE:
                reason = enums.DungeonEndReason.UNKNOWN
                if self.max_boss_hp.value > 0:
                    total_drop_rate = calculate_rate_per_second(
                        (self.max_boss_hp, TimestampedValue(t, old_dungeon.boss.health))
                    )
                    recent_drop_rate = calculate_rate_per_second(
                        (self.max_boss_hp, TimestampedValue(t, 0))
                    )
                    max_rate_factor = 1.2
                    if total_drop_rate > 0:
                        if (
                            old_dungeon.enemies_alive > 0
                            or recent_drop_rate / total_drop_rate > max_rate_factor
                        ):
                            reason = enums.DungeonEndReason.PLAYERS_DEFEATED
                        else:
                            reason = enums.DungeonEndReason.BOSS_DEFEATED
                await self._send_dungeon_end_event(reason)
            elif new_stage == DungeonStage.LOADING:
                await self._send_dungeon_spawned_event(new_dungeon)
            elif new_stage == DungeonStage.WAITING_FOR_PLAYERS:
                await self._send_dungeon_prepared_event(new_dungeon)
            elif new_stage == DungeonStage.FIGHTING_ENEMIES:
                await self._send_dungeon_started_event(new_dungeon)
            elif new_stage == DungeonStage.FIGHTING_BOSS:
                await self._send_dungeon_reached_boss_event(new_dungeon)

        if new_stage < DungeonStage.FIGHTING_ENEMIES:
            self.max_boss_hp = TimestampedValue(t, 0)

        self.stage = new_stage
        self.set_data(new_dungeon)

    async def _send_dungeon_spawned_event(
        self,
        dungeon: models.Dungeon,
        reason: enums.DungeonStartReason = enums.DungeonStartReason.UNKNOWN,
    ):
        await self.ravenfall._event_hook(
            ev.DungeonSpawnedEvent(
                ravenfall=self.ravenfall,
                reason=reason,
                name=dungeon.name,
            )
        )

    async def _send_dungeon_prepared_event(self, dungeon: models.Dungeon):
        await self.ravenfall._event_hook(
            ev.DungeonPreparedEvent(
                ravenfall=self.ravenfall,
                name=dungeon.name,
                joined_player_count=dungeon.players,
                enemy_count=dungeon.enemies,
            )
        )

    async def _send_dungeon_started_event(self, dungeon: models.Dungeon):
        await self.ravenfall._event_hook(
            ev.DungeonStartedEvent(ravenfall=self.ravenfall, data=dungeon)
        )

    async def _send_dungeon_reached_boss_event(self, dungeon: models.Dungeon):
        await self.ravenfall._event_hook(
            ev.DungeonReachedBossEvent(ravenfall=self.ravenfall, data=dungeon)
        )

    async def _send_dungeon_end_event(
        self, reason: enums.DungeonEndReason = enums.DungeonEndReason.UNKNOWN
    ):
        await self.ravenfall._event_hook(
            ev.DungeonEndedEvent(ravenfall=self.ravenfall, reason=reason)
        )


class RaidCollector(RavenfallCollectorBase[models.Raid]):
    """Ravenfall Raid collector."""

    def __init__(
        self,
        ravenfall: RavenfallInstance,
        interval: float = 0.5,
        cache_time: float = 0.05,
    ) -> None:
        super().__init__(ravenfall, interval, cache_time=cache_time)

    @override
    async def process(self):
        new_raid = await self.ravenfall._query_request(
            self.ravenfall.query_client.get_raid()
        )
        if new_raid is None or new_raid == RETURNED_NONE:
            return

        old_raid = self.get_data()
        if not old_raid:
            self.set_data(new_raid)
            return

        if not old_raid.started and new_raid.started:
            await self._send_raid_started_event(
                new_raid,
                enums.RaidStartReason.UNKNOWN,
            )
        elif not new_raid.started and old_raid.started:
            if old_raid.time_left < 1:
                await self._send_raid_end_event(enums.RaidEndReason.TIME_EXPIRED)
            else:
                await self._send_raid_end_event(enums.RaidEndReason.BOSS_DEFEATED)

        # new raid detections
        elif (
            old_raid.started
            and new_raid.started
            and (
                new_raid.boss.health > old_raid.boss.health
                or new_raid.boss.max_health != old_raid.boss.max_health
            )
        ):
            await self._send_raid_end_event(enums.RaidEndReason.BOSS_DEFEATED)
            await self._send_raid_started_event(
                new_raid,
                enums.RaidStartReason.UNKNOWN,
            )

        self.set_data(new_raid)

    async def _send_raid_started_event(
        self, data: models.Raid, reason: enums.RaidStartReason
    ):
        await self.ravenfall._event_hook(
            ev.RaidStartedEvent(ravenfall=self.ravenfall, data=data, reason=reason)
        )

    async def _send_raid_end_event(self, reason: enums.RaidEndReason):
        await self.ravenfall._event_hook(
            ev.RaidEndedEvent(ravenfall=self.ravenfall, reason=reason)
        )


class FetchError(Exception):
    """Failed to fetch data."""


class RavenfallInstance:
    """A Ravenfall instance."""

    def __init__(
        self, config: RavenfallInstanceConfig, event_src: RavenfallEventSource
    ) -> None:
        self.config: RavenfallInstanceConfig = config
        self.twitch_id: str = config.twitch_id
        self.twitch_login: str = config.twitch_login
        self.middleman_id: str | None = config.middleman_connection_id
        self.query_client: rq.RavenfallClient = rq.RavenfallClient(
            self.config.query_server_base_url
        )
        self.query_client.default_request_timeout = 1
        self._event_hook: RavenfallInstanceEventHook = self._dummy_event_hook
        self.is_online: asyncio.Event = asyncio.Event()
        self.is_ready: asyncio.Event = asyncio.Event()
        self.event_source: RavenfallEventSource = event_src

        self._session_collector: SessionCollector = SessionCollector(self)
        self._ferry_collector: FerryCollector = FerryCollector(self)
        self._players_collector: PlayersCollector = PlayersCollector(self)
        self._village_collector: VillageCollector = VillageCollector(self)
        self._multiplier_collector: MultiplierCollector = MultiplierCollector(self)
        self._dungeon_collector: DungeonCollector = DungeonCollector(self)
        self._raid_collector: RaidCollector = RaidCollector(self)
        self._observed_loop_task: asyncio.Task[None] | None = None

        self.channel_name: str = config.twitch_login
        self.channel_id: str = config.twitch_id
        self._fail_counter: int = 0
        self._max_conn_failures: int = 12
        self._first_off_online_event_sent: bool = False

    async def _dummy_event_hook(self, event: RavenfallEvent):  # pyright: ignore[reportUnusedParameter]
        LOGGER.warning("Dummy event hook was called")

    async def start(self):
        """Start the RavenfallInstance."""
        self._session_collector.start()
        self._ferry_collector.start()
        self._players_collector.start()
        self._village_collector.start()
        self._multiplier_collector.start()
        self._dungeon_collector.start()
        self._raid_collector.start()

    async def stop(self):
        """Stop the RavenfallInstance."""
        self._session_collector.stop()
        self._ferry_collector.stop()
        self._players_collector.stop()
        self._village_collector.stop()
        self._multiplier_collector.stop()
        self._dungeon_collector.stop()
        self._raid_collector.stop()
        if self._observed_loop_task:
            __ = self._observed_loop_task.cancel()

    async def _set_is_offline(self):
        if self.is_online.is_set() or not self._first_off_online_event_sent:
            self.is_online.clear()
            LOGGER.info(f"Ravenfall ({self.channel_name}) went offline.")
            await self._event_hook(ev.RavenfallOfflineEvent(ravenfall=self))
            self._first_off_online_event_sent = True

    async def _set_is_online(self):
        if not self.is_online.is_set():
            self.is_online.set()
            await self._event_hook(ev.RavenfallOnlineEvent(ravenfall=self))
            self._first_off_online_event_sent = True

    async def _set_is_not_ready(self):
        if self.is_ready.is_set():
            self.is_ready.clear()

    async def _set_is_ready(self):
        if not self.is_ready.is_set():
            self.is_ready.set()
            await self._event_hook(ev.RavenfallReadyEvent(ravenfall=self))

    async def _query_request[T](self, query_call: Awaitable[T]) -> T | Literal[-1] | None:
        try:
            result = await query_call
        except (rq.RavenfallTimeoutError, rq.RavenfallConnectionError):
            self._fail_counter += 1
            if not self._first_off_online_event_sent:
                await self._set_is_offline()
            if self._fail_counter >= self._max_conn_failures:
                await self._set_is_offline()
                await self._set_is_not_ready()
        except rq.RavenfallQueryError:
            await asyncio.sleep(0.2)
        except rq.RavenfallBadHostError:
            await self.stop()
            raise
        except Exception:
            LOGGER.exception("Error fetching session")
        else:
            self._fail_counter = 0
            if result is None:
                return RETURNED_NONE
            return result
        return None

    async def get_session(self) -> models.GameSession | None:
        """Get the latest session."""
        return await self._session_collector.get_latest()

    async def get_ferry(self) -> models.Ferry | None:
        """Get the latest ferry."""
        return await self._ferry_collector.get_latest()

    async def get_players(self) -> list[models.Player] | None:
        """Get the latest players."""
        return await self._players_collector.get_latest()

    async def get_player(self, player_name: str) -> models.Player | None:
        """Get the latest players."""
        players = await self._players_collector.get_latest()
        if not players:
            raise FetchError
        player_name = player_name.lower()
        for p in players:
            if p.name == player_name:
                return p
        return None

    async def get_village(self) -> models.Village | None:
        """Get the latest village."""
        return await self._village_collector.get_latest()

    async def get_multiplier(self) -> models.GameMultiplier | None:
        """Get the latest multiplier."""
        return await self._multiplier_collector.get_latest()

    async def get_dungeon(self) -> models.Dungeon | None:
        """Get the latest dungeon."""
        return await self._dungeon_collector.get_latest()

    def get_dungeon_status(self):
        """Get the current stage of the dungeon."""
        return self._dungeon_collector.stage

    async def get_raid(self) -> models.Raid | None:
        """Get the latest raid."""
        return await self._raid_collector.get_latest()

    def get_is_linked_to_middleman(self):
        """Get whether this instance has a configured middleman connection ID."""
        return self.config.middleman_connection_id is not None

    def _get_middleman(self) -> tuple[rm.MiddlemanClient, str]:
        middleman = self.event_source.middleman_client
        if not middleman:
            msg = "Middleman has not been configured."
            raise RuntimeError(msg)
        conn_id = self.config.middleman_connection_id
        if not conn_id:
            msg = (
                "A middleman connection id "
                f"has not been configured for {self.channel_name}."
            )
            raise RuntimeError(msg)
        return middleman, conn_id

    async def ensure_middleman_connection(self, timeout_extension: float = 30):
        """Ensure the middleman's connection to Ravenfall is active
        by extending its timeout.
        """
        middleman, conn_id = self._get_middleman()
        return await middleman.ensure_connection(conn_id, timeout_extension)

    async def reconnect_middleman_to_ravenfall(self, timeout: float = 0):
        """Force the middleman to reconnect to Ravenfall."""
        middleman, conn_id = self._get_middleman()
        return await middleman.force_reconnect(conn_id, timeout)

    async def get_middleman_connection_status(self):
        """Get middleman connection status for this Ravenfall instance."""
        middleman, conn_id = self._get_middleman()
        return await middleman.get_connection_status(conn_id)

    async def send_to_ravenfall(
        self, sender: rm.Sender, payload: BaseRavenBotPayload
    ) -> str:
        """Send a payload to Ravenfall."""
        middleman, conn_id = self._get_middleman()
        corr_id = str(uuid4())
        message = rm.RavenBotMessage(
            payload.identifier,
            sender,
            content=payload.get_content_json_string(),
            correlation_id=corr_id,
        )
        await middleman.send_to_ravenfall(conn_id, message)
        return corr_id

    # Ravenfall's "observed" endpoint does NOT work
    # async def _observed_loop(self):
    #     observed: models.Player | None = None
    #     first = True
    #     while True:
    #         __ = await self.is_online.wait()
    #         if not first:
    #             await asyncio.sleep(0.5)
    #         else:
    #             first = False

    #         new_observed = await self._query_request(self.query_client.get_observed())
    #         if new_observed is None:
    #             continue
    #         if new_observed == RETURNED_NONE:
    #             new_observed = None

    #         if observed is not None and new_observed is None:
    #             await self._send_observed_player_cleared_event()
    #         elif observed is None and new_observed is not None:
    #             await self._send_observed_player_changed_event(new_observed)
    #         elif (
    #             observed is not None
    #             and new_observed is not None
    #             and observed.id != new_observed.id
    #         ):
    #             await self._send_observed_player_changed_event(new_observed)

    #         observed = new_observed

    # async def _send_observed_player_cleared_event(self):
    #     await self._event_hook(
    #         ev.ObservedPlayerChangedEvent(
    #             ravenfall=self,
    #             data=None,
    #             player=None,
    #         )
    #     )

    # async def _send_observed_player_changed_event(self, player: models.Player):
    #     await self._event_hook(
    #         ev.ObservedPlayerChangedEvent(
    #             ravenfall=self,
    #             data=player,
    #             player=player,
    #         )
    #     )


class RavenNestCollectorBase[T](BaseCollector[T]):
    """Fetches data from RavenNest."""

    def __init__(
        self,
        event_src: RavenfallEventSource,
        interval: float = 1,
        *,
        only_online: bool = True,
        cache_time: float = 0.05,
    ):
        super().__init__(interval=interval, cache_time=cache_time)
        self.event_source: RavenfallEventSource = event_src
        self.only_online: bool = only_online

    @override
    async def get_latest(self) -> T | None:
        """Fetch the latest data and return it."""
        if self.only_online and not self.event_source.ravennest_is_online.is_set():
            return None
        return await super().get_latest()

    @override
    async def pre_fetch(self):
        if self.only_online:
            _ = await self.event_source.ravennest_is_online.wait()


class GlobalMultCollector(RavenNestCollectorBase[ravenpy.ExpMult]):
    """Global multiplier."""

    def __init__(
        self,
        event_src: RavenfallEventSource,
        interval: float = 5,
        *,
        only_online: bool = False,
        cache_time: float = 1,
    ):
        super().__init__(
            event_src, interval, only_online=only_online, cache_time=cache_time
        )

    @override
    async def process(self) -> None:
        new = None
        async with self.event_source.ravennest_fetch_context(timeout=4) as r:
            new = await r.get_global_mult()
        if not new:
            return
        self.set_data(new)


class MarketplaceCollector(RavenNestCollectorBase[tuple[ravenpy.MarketplaceItem, ...]]):
    """Marketplace items."""

    def __init__(
        self,
        event_src: RavenfallEventSource,
        interval: float = 60,
        *,
        only_online: bool = True,
        cache_time: float = 5,
    ):
        super().__init__(
            event_src, interval, only_online=only_online, cache_time=cache_time
        )

    @override
    async def process(self) -> None:
        new = None
        async with self.event_source.ravennest_fetch_context(timeout=30) as r:
            new = await r.get_marketplace()
        if not new:
            return
        self.set_data(new)


class GameVersionCollector(RavenNestCollectorBase[ravenpy.GameUpdateCheckResult]):
    """Marketplace items."""

    def __init__(
        self,
        event_src: RavenfallEventSource,
        interval: float = 60,
        *,
        only_online: bool = True,
        cache_time: float = 1,
    ):
        super().__init__(
            event_src, interval, only_online=only_online, cache_time=cache_time
        )

    @override
    async def process(self) -> None:
        try:
            new = await self.event_source.ravennest_api.get_latest_game_version()
        except (TimeoutError, ravenpy.FetchError, ravenpy.UnexpectedStatusCodeError):
            await self.event_source._set_updater_is_offline()
            return
        else:
            await self.event_source._set_updater_is_online()
        if not new:
            return
        self.set_data(new)


class RavenfallEventSource(BaseEventSource):
    """Event source for Ravenfall events.

    Polls from Ravenfall's query server, and gets bot messages from ravenfall-middleman.
    """

    def __init__(
        self,
        middleman_message_processor: rm.MessageProcessorServer | None = None,
    ) -> None:
        super().__init__()
        self.middleman_base_url: str | None = None
        self.ravenfall_config: list[RavenfallInstanceConfig] = []
        self.ravenfall_instances: list[RavenfallInstance] = []
        self.channel_name_to_instance: dict[str, RavenfallInstance] = {}
        self.channel_id_to_instance: dict[str, RavenfallInstance] = {}
        self.middleman_id_to_instance: dict[str, RavenfallInstance] = {}
        self.middleman_client: rm.MiddlemanClient | None = None
        self.middleman_message_processor: rm.MessageProcessorServer | None = (
            middleman_message_processor
        )
        self._matcher: RavenfallMatcher | None = None

        self.ravennest_api: RavenNest = ravenpy.RavenNest()
        self.ravennest_is_online: asyncio.Event = asyncio.Event()
        self.ravennest_updater_is_online: asyncio.Event = asyncio.Event()

        self._multiplier_collector: GlobalMultCollector = GlobalMultCollector(self)
        self._marketplace_collector: MarketplaceCollector = MarketplaceCollector(self)
        self._game_version_collector: GameVersionCollector = GameVersionCollector(self)

        self._ravennest_fail_counter: int = 0
        self._ravennest_max_conn_failures: int = 3
        self._first_ravennest_offline_event_sent: bool = False
        self._first_updater_offline_event_sent: bool = False

    @override
    async def setup(self, event_manager: EventManager) -> None:
        config_service = await self.global_context.wait_for_service(ConfigService)
        config = config_service.get_table(RavenfallConfig)
        self.ravenfall_config = config.instances
        self.ravenfall_instances = [
            RavenfallInstance(x, self) for x in self.ravenfall_config
        ]
        for i in self.ravenfall_instances:
            i._event_hook = self.send_event
        self.channel_name_to_instance = {
            x.channel_name.lower(): x for x in self.ravenfall_instances
        }
        self.channel_id_to_instance = {x.channel_id: x for x in self.ravenfall_instances}
        self.middleman_id_to_instance = {
            x.middleman_id: x
            for x in self.ravenfall_instances
            if x.middleman_id is not None
        }
        if config.middleman_base_url:
            self.middleman_client = rm.MiddlemanClient(config.middleman_base_url)

        if self.middleman_client is not None and self.middleman_message_processor is None:
            self.middleman_client.add_ravenbot_message_hook(self._ravenbot_message)
            self.middleman_client.add_ravenfall_message_hook(self._ravenfall_message)
        elif self.middleman_message_processor is not None:
            self.middleman_message_processor.add_ravenbot_message_hook(
                self._ravenbot_processor_message
            )
            self.middleman_message_processor.add_ravenfall_message_hook(
                self._ravenfall_processor_message
            )

        def_path = AsyncPath(config.ravenfall_message_definitions_path)
        if await def_path.exists():
            f = await def_path.open("r")
            text = await f.read()
            self._matcher = RavenfallMatcher(definitions_text=text)
            await f.aclose()

        tasks: list[Awaitable[None]] = []
        tasks.extend([x.start() for x in self.ravenfall_instances])
        __ = await asyncio.gather(*tasks, return_exceptions=False)
        await self.global_context.register_service(RavenfallService(self))
        if self.middleman_client is not None and self.middleman_message_processor is None:
            await self.middleman_client.connect_websocket()
        elif self.middleman_message_processor is not None:
            LOGGER.info(
                "RavenfallEventSource will listen for message events through "
                "the provided MessageProcessorServer"
            )

        await self.ravennest_api.login(config.username, config.password)

        self._multiplier_collector.start()
        self._marketplace_collector.start()
        self._game_version_collector.start()

    @override
    async def teardown(self) -> None:
        tasks: list[Awaitable[None]] = []
        tasks.extend([x.stop() for x in self.ravenfall_instances])
        __ = await asyncio.gather(*tasks, return_exceptions=False)
        if self.middleman_client:
            await self.middleman_client.disconnect_websocket()

        self._multiplier_collector.stop()
        self._marketplace_collector.stop()
        self._game_version_collector.stop()

    async def _ravenfall_message(self, message: rm.RavenfallStreamMessage):
        ravenfall = self.middleman_id_to_instance.get(message.connection_id)
        if not ravenfall:
            return
        msg_match: Match | None = None
        if self._matcher is not None:
            msg_match = self._matcher.match_string(
                message.message.format, message.message.args
            )
        event = ev.RavenfallMessageEvent(
            data=message,
            message=message.message,
            orig_message=message.message,
            ravenfall=ravenfall,
            is_msg_from_api=message.is_api,
            message_source=ev.MessageOrigin.STREAM,
            message_match=msg_match,
        )
        await self.send_event(event)

    async def _ravenbot_message(self, message: rm.RavenBotStreamMessage):
        ravenfall = self.middleman_id_to_instance.get(message.connection_id)
        if not ravenfall:
            return
        event = ev.RavenBotMessageEvent(
            data=message,
            message=message.message,
            orig_message=message.message,
            ravenfall=ravenfall,
            is_msg_from_api=message.is_api,
            message_source=ev.MessageOrigin.STREAM,
        )
        await self.send_event(event)

    async def _ravenfall_processor_message(self, message: rm.RavenfallProcessorMessage):
        ravenfall = self.middleman_id_to_instance.get(message.connection_id)
        if not ravenfall:
            return
        msg_match: Match | None = None
        if self._matcher is not None:
            msg_match = self._matcher.match_string(
                message.message.format, message.message.args
            )
        event = ev.RavenfallMessageEvent(
            data=message,
            message=message.message,
            orig_message=message.original_message,
            ravenfall=ravenfall,
            is_msg_from_api=message.is_api,
            message_source=ev.MessageOrigin.PROCESSOR,
            message_match=msg_match,
        )
        await self.send_event(event)

    async def _ravenbot_processor_message(self, message: rm.RavenBotProcessorMessage):
        ravenfall = self.middleman_id_to_instance.get(message.connection_id)
        if not ravenfall:
            return
        event = ev.RavenBotMessageEvent(
            data=message,
            message=message.message,
            orig_message=message.original_message,
            ravenfall=ravenfall,
            is_msg_from_api=message.is_api,
            message_source=ev.MessageOrigin.PROCESSOR,
        )
        await self.send_event(event)

    async def _set_updater_is_offline(self):
        if (
            self.ravennest_updater_is_online.is_set()
            or not self._first_updater_offline_event_sent
        ):
            self.ravennest_updater_is_online.clear()
            await self.send_event(ev.RavenNestUpdaterOfflineEvent())
            self._first_updater_offline_event_sent = True

    async def _set_updater_is_online(self):
        if not self.ravennest_updater_is_online.is_set():
            self.ravennest_updater_is_online.set()
            await self.send_event(ev.RavenNestUpdaterOnlineEvent())
            self._first_updater_offline_event_sent = True

    async def _set_is_offline(self):
        await self._set_updater_is_offline()
        if (
            self.ravennest_is_online.is_set()
            or not self._first_ravennest_offline_event_sent
        ):
            self.ravennest_is_online.clear()
            await self.send_event(ev.RavenNestOfflineEvent())
            self._first_ravennest_offline_event_sent = True

    async def _set_is_online(self):
        if not self.ravennest_is_online.is_set():
            self.ravennest_is_online.set()
            await self.send_event(ev.RavenNestOnlineEvent())
            self._first_ravennest_offline_event_sent = True

    def get_has_configured_middleman(self):
        """Check if the Event Source has a configured Ravenfall middleman."""
        return self.middleman_client is not None

    def get_has_middleman_message_processor(self):
        """Check if the Event Source has a configured Ravenfall middleman
        message processor.
        """
        return self.middleman_message_processor is not None

    @asynccontextmanager
    async def ravennest_fetch_context(self, *, timeout: float = 15):
        """Tracks RavenNest fetch errors.

        WARNING: This suppresses TimeoutError, ravenpy.FetchError,
        and ravenpy.UnexpectedStatusCodeError!
        """
        try:
            async with asyncio.timeout(timeout):
                yield self.ravennest_api
        except (TimeoutError, ravenpy.FetchError, ravenpy.UnexpectedStatusCodeError):
            self._ravennest_fail_counter += 1
            if not self._first_ravennest_offline_event_sent:
                await self._set_is_offline()
        else:
            self._ravennest_fail_counter = 0

        if self._ravennest_fail_counter >= self._ravennest_max_conn_failures:
            await self._set_is_offline()
        elif self._ravennest_fail_counter == 0:
            await self._set_is_online()
