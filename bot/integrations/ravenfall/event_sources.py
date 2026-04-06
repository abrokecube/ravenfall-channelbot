from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import TYPE_CHECKING, Literal, override

from bot.clients import ravenfall_query as rq
from bot.core.components import BaseEventSource

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from bot.core.components import EventManager
    from bot.integrations.ravenfall import RavenfallInstanceEventHook
    from bot.integrations.ravenfall.events import RavenfallEvent

    from . import RavenfallConfig

import logging

LOGGER = logging.getLogger(__name__)
RETURNED_NONE = -1


class RavenfallInstance:
    """A Ravenfall instance."""

    def __init__(self, config: RavenfallConfig) -> None:
        self.config: RavenfallConfig = config
        self.twitch_id: str = config.twitch_id
        self.twitch_login: str = config.twitch_login
        self.query_client: rq.RavenfallClient = rq.RavenfallClient(
            self.config.query_server_base_url
        )
        self.query_client.default_request_timeout = 1
        self._event_hook: RavenfallInstanceEventHook = self._dummy_event_hook
        self.is_online: asyncio.Event = asyncio.Event()
        self.is_ready: asyncio.Event = asyncio.Event()

        self._session_loop_task: asyncio.Task[None] | None = None
        self._ferry_loop_task: asyncio.Task[None] | None = None
        self._players_loop_task: asyncio.Task[None] | None = None
        self._village_loop_task: asyncio.Task[None] | None = None
        self._multiplier_loop_task: asyncio.Task[None] | None = None
        self._dungeon_loop_task: asyncio.Task[None] | None = None
        self._raid_loop_task: asyncio.Task[None] | None = None
        self._observed_loop_task: asyncio.Task[None] | None = None

        self.channel_name: str = config.twitch_login
        self.channel_id: str = config.twitch_id
        self._fail_counter: int = 0
        self._max_conn_failures: int = 5

    async def _dummy_event_hook(self, event: RavenfallEvent):  # pyright: ignore[reportUnusedParameter]
        pass

    async def start(self):
        """Start the RavenfallInstance."""
        self._session_loop_task = asyncio.create_task(self._session_loop())
        self._ferry_loop_task = asyncio.create_task(self._ferry_loop())
        self._players_loop_task = asyncio.create_task(self._players_loop())
        self._village_loop_task = asyncio.create_task(self._village_loop())
        self._multiplier_loop_task = asyncio.create_task(self._multiplier_loop())
        self._dungeon_loop_task = asyncio.create_task(self._dungeon_loop())
        self._raid_loop_task = asyncio.create_task(self._raid_loop())
        self._observed_loop_task = asyncio.create_task(self._observed_loop())

    async def stop(self):
        """Stop the RavenfallInstance."""
        if self._session_loop_task:
            __ = self._session_loop_task.cancel()
        if self._ferry_loop_task:
            __ = self._ferry_loop_task.cancel()
        if self._players_loop_task:
            __ = self._players_loop_task.cancel()
        if self._village_loop_task:
            __ = self._village_loop_task.cancel()
        if self._multiplier_loop_task:
            __ = self._multiplier_loop_task.cancel()
        if self._dungeon_loop_task:
            __ = self._dungeon_loop_task.cancel()
        if self._raid_loop_task:
            __ = self._raid_loop_task.cancel()
        if self._observed_loop_task:
            __ = self._observed_loop_task.cancel()

    async def _query_request[T](self, query_call: Awaitable[T]) -> T | Literal[-1] | None:
        try:
            result = await query_call
        except (rq.RavenfallTimeoutError, rq.RavenfallConnectionError):
            self._fail_counter += 1
            if self.is_online.is_set() and self._fail_counter > self._max_conn_failures:
                LOGGER.info(f"Ravenfall ({self.channel_name}) went offline.")
                self.is_online.clear()
                self.is_ready.clear()
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

    async def _session_loop(self):
        session: rq.GameSession | None = None
        warned_user: str | None = None
        while True:
            if session is not None:
                await asyncio.sleep(0.5)

            new_session = await self._query_request(self.query_client.get_session())
            if new_session is None or new_session == RETURNED_NONE:
                continue

            if (
                not (self.is_online.is_set())
                and new_session.authenticated
                and new_session.session_started
            ):
                LOGGER.info(f"Ravenfall ({self.channel_name}) is online.")
                self.is_online.set()
            if new_session.twitch_username != self.twitch_login:
                if warned_user != new_session.twitch_username:
                    LOGGER.warning(
                        f"Ravenfall ({self.channel_name}): "
                        "Received username does not match given twitch channel name "
                        f"(got {new_session.twitch_username})"
                    )
                warned_user = new_session.twitch_username

            if not session:
                session = new_session
                continue

            session = new_session

    async def _players_loop(self):
        players: list[rq.Player] | None = None
        player_count_history: deque[tuple[float, int]] = deque(maxlen=10)
        while True:
            __ = await self.is_online.wait()
            if players is not None:
                await asyncio.sleep(0.5)

            new_players = await self._query_request(self.query_client.get_players())
            if new_players is None or new_players == RETURNED_NONE:
                continue
            t = time.monotonic()
            player_count_history.append((t, len(new_players)))
            min_items = 3
            if (
                not self.is_ready.is_set()
                and len(new_players) > 0
                and len(player_count_history) > min_items
            ):
                current_value = len(new_players)
                is_ready = True
                cutoff_t = t - 2
                for t0, count in reversed(player_count_history):
                    if t0 < cutoff_t:
                        break
                    if count != current_value:
                        is_ready = False
                        break
                if is_ready:
                    LOGGER.info(f"Ravenfall ({self.channel_name}) is ready.")
                    self.is_ready.set()

            if not players:
                players = new_players
                continue

            players = new_players

    async def _ferry_loop(self):
        ferry: rq.Ferry | None = None
        while True:
            __ = await self.is_online.wait()
            if ferry is not None:
                await asyncio.sleep(1)

            new_ferry = await self._query_request(self.query_client.get_ferry())
            if new_ferry is None or new_ferry == RETURNED_NONE:
                continue

            if not ferry:
                ferry = new_ferry
                continue

            ferry = new_ferry

    async def _village_loop(self):
        village: rq.Village | None = None
        while True:
            __ = await self.is_online.wait()
            if village is not None:
                await asyncio.sleep(1)

            new_village = await self._query_request(self.query_client.get_village())
            if new_village is None or new_village == RETURNED_NONE:
                continue

            if not village:
                village = new_village
                continue

            village = new_village

    async def _multiplier_loop(self):
        mult: rq.GameMultiplier | None = None
        while True:
            __ = await self.is_online.wait()
            if mult is not None:
                await asyncio.sleep(5)

            new_mult = await self._query_request(self.query_client.get_multiplier())
            if new_mult is None or new_mult == RETURNED_NONE:
                continue

            if not mult:
                mult = new_mult
                continue

            mult = new_mult

    async def _dungeon_loop(self):
        dungeon: rq.Dungeon | None = None
        while True:
            __ = await self.is_online.wait()
            if dungeon is not None:
                await asyncio.sleep(0.5)

            new_dungeon = await self._query_request(self.query_client.get_dungeon())
            if new_dungeon is None or new_dungeon == RETURNED_NONE:
                continue

            if not dungeon:
                dungeon = new_dungeon
                continue

            dungeon = new_dungeon

    async def _raid_loop(self):
        raid: rq.Raid | None = None
        while True:
            __ = await self.is_online.wait()
            if raid is not None:
                await asyncio.sleep(0.5)

            new_raid = await self._query_request(self.query_client.get_raid())
            if new_raid is None or new_raid == RETURNED_NONE:
                continue

            if not raid:
                raid = new_raid
                continue

            raid = new_raid

    async def _observed_loop(self):
        observed: rq.Player | None = None
        first = True
        while True:
            __ = await self.is_online.wait()
            if not first:
                await asyncio.sleep(0.5)
            else:
                first = False

            new_observed = await self._query_request(self.query_client.get_observed())
            if new_observed is None:
                continue
            if new_observed == RETURNED_NONE:
                new_observed = None

            if not observed:
                observed = new_observed
                continue

            observed = new_observed


class RavenfallEventSource(BaseEventSource):
    """Event source for Ravenfall events.

    Polls from Ravenfall's query server, and gets bot messages from ravenfall-middleman.
    """

    def __init__(
        self,
        ravenfall_config: list[RavenfallConfig],
        middleman_base_url: str | None = None,
    ) -> None:
        super().__init__()
        self.middleman_base_url: str | None = middleman_base_url
        self.ravenfall_config: list[RavenfallConfig] = ravenfall_config
        self.ravenfall_instances: list[RavenfallInstance] = [
            RavenfallInstance(x) for x in ravenfall_config
        ]
        for i in self.ravenfall_instances:
            i._event_hook = self.send_event  # noqa: SLF001

    @override
    async def setup(self, event_manager: EventManager) -> None:
        tasks: list[Awaitable[None]] = []
        tasks.extend([x.start() for x in self.ravenfall_instances])
        __ = await asyncio.gather(*tasks)

    @override
    async def teardown(self) -> None:
        tasks: list[Awaitable[None]] = []
        tasks.extend([x.stop() for x in self.ravenfall_instances])
        __ = await asyncio.gather(*tasks)
