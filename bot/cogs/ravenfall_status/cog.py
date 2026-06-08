from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar, override

from bot.core.components import Cog
from bot.core.decorators import on_match, priority
from bot.integrations.ravenfall import (
    DungeonStartedEvent,
    MultiplierChangedEvent,
    MultiplierChangeType,
    RaidStartedEvent,
    RavenfallMessageEvent,
    TownLevelUpEvent,
)
from bot.integrations.ravenfall.models import RavenfallFormattedMessage
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigModel, ConfigService
from bot.services.pastebin_service import PastebinService
from bot.services.ravenfall_channels import RavenfallChannelService
from utils.format_time import TimeSize, format_seconds
from utils.routines import routine
from utils.strings import DIAMOND, EN_DASH
from utils.strutils import strjoin

if TYPE_CHECKING:
    from bot.core.components import EventManager, GlobalContext

LOGGER = logging.getLogger(__name__)


class RavenfallStatusConfig(ConfigModel):
    config_table_name: ClassVar[str | None] = "cogs.ravenfall_status"
    enable_town_level_notifications: bool = True
    enable_multiplier_notifications: bool = True
    enable_island_arrivals: bool = True
    enable_event_notifications: bool = True
    enable_loot_messages: bool = True
    enable_loot_summary: bool = True


class RavenfallStatusMessagesCog(Cog, ConfigSubscriberMixin):
    """Handles Ravenfall status messages."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)
        self.config: RavenfallStatusConfig = RavenfallStatusConfig()
        self._island_arrivals: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._island_last_arrival_time: dict[tuple[str, str], float] = {}
        self._loot_summary_items: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self._loot_summary_last_time: dict[str, float] = {}

    @override
    async def setup(self) -> None:
        config_srv = await self.global_context.wait_for_service(ConfigService)
        self.inject_config_service(config_srv)
        self.config = self.subscribe_config(RavenfallStatusConfig)
        __ = self._island_arrival_routine.start()
        __ = self._loot_summary_routine.start()

    @override
    async def teardown(self) -> None:
        self._island_arrival_routine.stop()
        self._loot_summary_routine.stop()

    @override
    async def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ) -> None:
        if isinstance(config, RavenfallStatusConfig):
            self.config = config

    @on_match(TownLevelUpEvent)
    async def _on_town_level_up(
        self, _g_ctx: GlobalContext, event: TownLevelUpEvent, _match: object
    ):
        if not self.config.enable_town_level_notifications:
            return
        channel_srv = self.global_context.require_service(RavenfallChannelService)
        await channel_srv.send_global_message(
            f"🎉 Town level is now {event.data.level}!",
            "ravenfall.status.town_level_up",
            event.ravenfall.channel_name,
        )

    @on_match(MultiplierChangedEvent)
    async def _on_multiplier_changed(
        self, _g_ctx: GlobalContext, event: MultiplierChangedEvent, _match: object
    ):
        if not self.config.enable_multiplier_notifications:
            return
        channel_srv = self.global_context.require_service(RavenfallChannelService)
        match event.change_type:
            case MultiplierChangeType.INCREASED:
                text = (
                    f"{event.data.event_name} increased the multiplier to "
                    f"{int(event.data.multiplier)}x, ending in "
                    f"{format_seconds(event.data.time_left, TimeSize.MEDIUM_SPACES)}!"
                )
                await channel_srv.send_global_message(
                    text,
                    "ravenfall.status.multiplier_increase",
                    event.ravenfall.channel_name,
                )
            case MultiplierChangeType.EXPIRED:
                await channel_srv.send_global_message(
                    "The EXP multiplier has expired.",
                    "ravenfall.status.multiplier_expired",
                    event.ravenfall.channel_name,
                )

    @on_match(DungeonStartedEvent)
    async def _on_dungeon_started(
        self, _g_ctx: GlobalContext, event: DungeonStartedEvent, _match: object
    ):
        if not self.config.enable_event_notifications:
            return
        channel_srv = self.global_context.require_service(RavenfallChannelService)
        text = (
            f"DUNGEON {EN_DASH} "
            f"Boss HP: {event.data.boss.max_health:,} {EN_DASH} "
            f"Enemies: {event.data.enemies:,} {EN_DASH} "
            f"Players: {event.data.players:,}"
        )
        await channel_srv.send_global_message(
            text,
            "ravenfall.status.dungeon_started",
            event.ravenfall.channel_name,
        )

    @on_match(RaidStartedEvent)
    async def _on_raid_started(
        self, _g_ctx: GlobalContext, event: RaidStartedEvent, _match: object
    ):
        if not self.config.enable_event_notifications:
            return
        channel_srv = self.global_context.require_service(RavenfallChannelService)
        text = f"RAID {EN_DASH} Boss HP: {event.data.boss.health:,}"
        await channel_srv.send_global_message(
            text,
            "ravenfall.status.raid_started",
            event.ravenfall.channel_name,
        )

    @priority(10)
    @on_match(
        RavenfallMessageEvent,
        lambda e: e.message_match is not None and e.message_match.identifier == "loot",
    )
    async def _on_loot(
        self, _g_ctx: GlobalContext, event: RavenfallMessageEvent, _match: object
    ):
        if not self.config.enable_loot_messages:
            return
        event.block()
        msg = event.orig_message
        text = msg.format
        loots = [x.strip() for x in text.split(". ")]
        if len(loots) <= 3:
            await self._send_loot(
                ", ".join(loots),
                event.ravenfall.channel_name,
            )
            return
        paste_srv = self.global_context.require_service(PastebinService)
        pasted = await paste_srv.upload_text(
            f"Loot gained by "
            f"{event.message.recipient.platform_user_name} "
            f"({time.strftime('%d %B %Y %H:%M:%S UTC', time.gmtime())})\n"
            f"\n"
            f"{chr(10).join(loots)}"
        )
        url = pasted.url or "unknown"
        first_three = ", ".join(loots[:3])
        await self._send_loot(
            f"{first_three} {DIAMOND} More: {url}",
            event.ravenfall.channel_name,
        )

    async def _send_loot(self, text: str, channel_name: str) -> None:
        channel_srv = self.global_context.require_service(RavenfallChannelService)
        await channel_srv.send_global_message(
            text,
            "ravenfall.status.loot",
            channel_name,
        )

    @priority(10)
    @on_match(
        RavenfallMessageEvent,
        lambda e: (
            e.message_match is not None and e.message_match.identifier == "loot_summary"
        ),
    )
    async def _on_loot_summary(
        self, _g_ctx: GlobalContext, event: RavenfallMessageEvent, _match: object
    ):
        if not self.config.enable_loot_summary:
            return
        event.block()
        text = event.orig_message.format
        channel_name = event.ravenfall.channel_name
        for sentence in text.split(". "):
            sentence = sentence.strip()  # noqa: PLW2901
            if not sentence:
                continue
            parts = sentence.split(" was found by ")
            if len(parts) != 2:
                continue
            item = parts[0].strip()
            players_str = parts[1].strip()
            for player in re.split(r" and |, ", players_str):
                player = player.strip()  # noqa: PLW2901
                if player:
                    self._loot_summary_items[channel_name].append((player, item))
        self._loot_summary_last_time[channel_name] = time.monotonic()

    @priority(10)
    @on_match(
        RavenfallMessageEvent,
        lambda e: (
            e.message_match is not None and e.message_match.identifier == "ferry_arrived"
        ),
    )
    async def _on_ferry_arrived(
        self, _g_ctx: GlobalContext, event: RavenfallMessageEvent, _match: object
    ):
        if not self.config.enable_island_arrivals:
            return
        event.block()
        user = event.message.recipient.platform_user_name
        msg = event.message
        if isinstance(msg, RavenfallFormattedMessage):
            args = msg.format_args_as_array()
        else:
            args = msg.args
        destination = str(args[0]) if args else "unknown"
        key = (event.ravenfall.channel_name, destination)
        t = time.monotonic()
        self._island_arrivals[key].append(user)
        self._island_last_arrival_time[key] = t

    @routine(delta=timedelta(seconds=0.5), max_attempts=99999)
    async def _island_arrival_routine(self):
        t = time.monotonic()
        for key, timestamp in list(self._island_last_arrival_time.items()):
            if timestamp > 0 and t - timestamp >= 0.25:
                channel_name, island = key
                players = self._island_arrivals[key]
                if len(players) > 1:
                    player_names = strjoin(
                        ", ", *[f"@{a}" for a in players], before_end=" and "
                    )
                    msg = f"{player_names} have arrived at {island}."
                else:
                    msg = f"@{players[0]} has arrived at {island}."
                channel_srv = self.global_context.require_service(RavenfallChannelService)
                await channel_srv.send_global_message(
                    msg,
                    "ravenfall.status.ferry_arrivals",
                    channel_name,
                )
                self._island_arrivals[key] = []
                self._island_last_arrival_time[key] = 0

    @routine(delta=timedelta(seconds=0.5), max_attempts=99999)
    async def _loot_summary_routine(self):
        t = time.monotonic()
        for channel_name, last_time in list(self._loot_summary_last_time.items()):
            if last_time <= 0 or t - last_time < 0.25:
                continue
            items = self._loot_summary_items[channel_name]
            if not items:
                self._loot_summary_last_time[channel_name] = 0
                continue
            items.sort(key=lambda x: x[0].lower())
            lines = [f"{p} - {i}" for p, i in items]
            header = (
                f"Loot summary for {channel_name}"
                f" ({time.strftime('%d %B %Y %H:%M:%S UTC', time.gmtime())})"
            )
            paste_srv = self.global_context.require_service(PastebinService)
            pasted = await paste_srv.upload_text(f"{header}\n\n" + "\n".join(lines))
            url = pasted.url or "unknown"
            channel_srv = self.global_context.require_service(RavenfallChannelService)
            await channel_srv.send_global_message(
                f"Loot: {url}",
                "ravenfall.status.loot_summary",
                channel_name,
            )
            self._loot_summary_items[channel_name] = []
            self._loot_summary_last_time[channel_name] = 0
