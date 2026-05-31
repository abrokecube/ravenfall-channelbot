from __future__ import annotations

import contextlib
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bot.cogs.ravenfall_scroll_queue import RavenfallScrollQueueService
from bot.cogs.ravenfall_watcher import RavenfallWatcherService
from bot.core.components import Cog
from bot.integrations.chat_messages import MessageEvent, on_message
from bot.integrations.commands import (
    CommandError,
    CommandEvent,
    CommandService,
    command,
    parameter,
)
from bot.integrations.ravenfall import (
    DungeonStage,
    RavenfallInstance,
    RavenfallInstanceConverter,
    RavenfallService,
)
from bot.services.ravenfall_channels import RavenfallChannelService
from bot.services.ravenfall_multichat import RavenfallMultichatService
from utils.format_time import TimeSize, format_seconds
from utils.strutils import pl, pl2

if TYPE_CHECKING:
    from bot.clients.ravenfall_multichat import RavenfallMultichatClient

LOGGER = logging.getLogger(__name__)


class RavenfallCog(Cog):
    """Ravenfall stuff."""

    def _check_online(self, instance: RavenfallInstance):
        if not instance.is_online.is_set():
            raise CommandError("Ravenfall is offline.")
        if not instance.is_ready.is_set():
            raise CommandError("Ravenfall is starting up.")
        ravennest_online = self.g_ctx.require_service(
            RavenfallService
        ).ravennest_is_online.is_set()
        if not ravennest_online:
            raise CommandError("RavenNest is offline.")

    def _check_restart_task(self, instance: RavenfallInstance):
        with contextlib.suppress(ValueError, RuntimeError):
            watcher = self.g_ctx.require_service(RavenfallWatcherService).get_watcher(
                instance.channel_name
            )
            restart_info = watcher.get_restart_task_info()
            if restart_info.is_announced:
                raise CommandError("Ravenfall will restart soon.")

    async def _get_scrolls(self, multichat: RavenfallMultichatClient, channel_id: str):
        try:
            scrolls = await multichat.get_scroll_counts(channel_id)
        except Exception as e:
            LOGGER.exception("Failed to get scroll info")
            raise CommandError("Failed to get scroll stock.") from e
        else:
            return scrolls

    async def _check_dsrs(self, instance: RavenfallInstance):
        queue_srv = self.g_ctx.get_service(RavenfallScrollQueueService)
        if queue_srv:
            queue = queue_srv.get_queue(instance.channel_name)
            if queue:
                queue_length = queue.get_length()
                if queue_length > 0:
                    verb = pl2(queue_length, "is", "are", include_number=False)
                    scroll_pl = pl2(queue_length, "scroll", "scrolls")
                    msg = (
                        f"There {verb} currently {scroll_pl} in the queue. "
                        "Try again when the queue is empty."
                    )
                    raise CommandError(msg)
        self._check_online(instance)
        self._check_restart_task(instance)
        dungeon = await instance.get_dungeon()
        raid = await instance.get_raid()
        if not dungeon or not raid:
            raise CommandError("Could not fetch game event info.")
        if dungeon.stage != DungeonStage.NONE:
            raise CommandError("There is an active dungeon.")
        if raid.started:
            raise CommandError("There is an active raid")

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @command()
    async def ds(
        self,
        _ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Use one of my Dungeon scrolls."""
        multichat = self.g_ctx.require_service(RavenfallMultichatService).get_client()
        scrolls = await self._get_scrolls(multichat, instance.channel_id)
        if scrolls.channel.dungeon_scroll <= 0:
            raise CommandError("Currently out of dungeon scrolls.")
        await self._check_dsrs(instance)
        channel_srv = self.g_ctx.require_service(RavenfallChannelService)
        await channel_srv.send_multichat_command("?ds", instance.channel_name, admin=True)

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @command()
    async def rs(
        self,
        _ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Use one of my Raid scrolls."""
        multichat = self.g_ctx.require_service(RavenfallMultichatService).get_client()
        scrolls = await self._get_scrolls(multichat, instance.channel_id)
        if scrolls.channel.raid_scroll <= 0:
            raise CommandError("Currently out of raid scrolls.")
        await self._check_dsrs(instance)
        channel_srv = self.g_ctx.require_service(RavenfallChannelService)
        await channel_srv.send_multichat_command("?rs", instance.channel_name, admin=True)

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @command()
    async def exps(
        self,
        _ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Use some of my Exp Multiplier scrolls."""
        self._check_online(instance)

        rf_srv = self.global_context.require_service(RavenfallService)
        server_mult = await rf_srv.get_multiplier()
        client_mult = await instance.get_multiplier()
        if not server_mult or not client_mult:
            raise CommandError("Failed to get multiplier info.")

        multichat = self.g_ctx.require_service(RavenfallMultichatService).get_client()
        scrolls = await self._get_scrolls(multichat, instance.channel_id)
        stock = scrolls.total.exp_multiplier_scroll
        if stock <= 0:
            raise CommandError("Currently out of exp multiplier scrolls.")

        if server_mult.multiplier > 1:
            s_duration = (server_mult.end_time - server_mult.start_time).total_seconds()
            s_remaining = (server_mult.end_time - datetime.now(UTC)).total_seconds()
        else:
            s_duration = 0
            s_remaining = 0

        if client_mult.multiplier > 1:
            c_duration = client_mult.duration
            c_remaining = client_mult.time_left
        else:
            c_duration = 0
            c_remaining = 0

        if c_duration >= s_duration:
            duration = c_duration
            remaining = c_remaining
            multiplier_value = client_mult.multiplier
        else:
            duration = s_duration
            remaining = s_remaining
            multiplier_value = server_mult.multiplier

        _max_mult = 100
        if multiplier_value == _max_mult:
            raise CommandError("Multiplier is already maxed.")
        scrolls_needed = _max_mult - multiplier_value
        if stock < scrolls_needed:
            raise CommandError(
                "There are not enough scrolls in stock to reach a 100x multiplier."
            )
        _cutoff = 0.8
        if multiplier_value > 1 and remaining / duration < _cutoff:
            raise CommandError(
                "Wait for the current multiplier to expire "
                "before using this command again."
            )
        channel_srv = self.g_ctx.require_service(RavenfallChannelService)
        await channel_srv.send_multichat_command(
            f"?exps {scrolls_needed}", instance.channel_name, admin=True
        )

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @command()
    async def fs(
        self,
        _ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Use one of my Ferry scrolls."""
        self._check_online(instance)
        self._check_restart_task(instance)
        multichat = self.g_ctx.require_service(RavenfallMultichatService).get_client()
        scrolls = await self._get_scrolls(multichat, instance.channel_id)
        if scrolls.channel.ferry_scroll <= 0:
            raise CommandError("Currently out of ferry scrolls.")

        ferry = await instance.get_ferry()
        if not ferry:
            raise CommandError("Failed to get ferry info.")

        if ferry.boost.is_active:
            remaining_time = format_seconds(
                ferry.boost.remaining_time, TimeSize.LONG, include_zero=False
            )
            msg = f"There is currently an active ferry boost, ending in {remaining_time}."
            raise CommandError(msg)

        channel_srv = self.g_ctx.require_service(RavenfallChannelService)
        await channel_srv.send_multichat_command("?fs", instance.channel_name, admin=True)

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @command("channelscrolls")
    async def scrolls(
        self,
        ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Lists available scroll stock."""
        multichat = self.g_ctx.require_service(RavenfallMultichatService).get_client()
        scrolls = await self._get_scrolls(multichat, instance.channel_id)
        scroll_counts = {
            "Raid Scroll": scrolls.channel.raid_scroll,
            "Dungeon Scroll": scrolls.channel.dungeon_scroll,
            "Exp Multiplier Scroll": scrolls.total.exp_multiplier_scroll,
            "Ferry Scroll": scrolls.channel.ferry_scroll,
        }
        scroll_list = [f"{pl(y, x)}" for x, y in scroll_counts.items()]
        await ctx.reply(f"Available channel scrolls: {', '.join(scroll_list)}")

    @on_message(lambda e: re.match(r"^\?(rs|ds|exps|fs)", e.text, re.IGNORECASE))
    async def scrolls_use_aliases(self, ctx: MessageEvent, _result: re.Match[str]):
        """Aliases for scroll use commands."""
        command_srv = self.global_context.require_service(CommandService)
        __ = await command_srv.execute(ctx.text[1:], ctx)

    @on_message(lambda e: re.match(r"^\?scrolls(?P<args>.*)", e.text, re.IGNORECASE))
    async def scrolls_alias(self, ctx: MessageEvent, result: re.Match[str]):
        """Alias for channelscrolls command."""
        command_srv = self.global_context.require_service(CommandService)
        __ = await command_srv.execute(f"channelscrolls{result.group('args')}", ctx)
