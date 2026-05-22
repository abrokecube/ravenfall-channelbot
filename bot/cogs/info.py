from __future__ import annotations

import asyncio
import contextlib
import re  # noqa: TC003
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import psutil
from msgspec import Struct
from numerize.numerize import (  # pyright: ignore[reportMissingTypeStubs]
    numerize,  # pyright: ignore[reportUnknownVariableType]
)

import ravenpy
from bot.clients.ravenfall_query import Character, Village
from bot.cogs.ravenfall_watcher import RavenfallWatcherService
from bot.core.components import Cog
from bot.core.decorators import cooldown
from bot.core.enums import BucketType
from bot.integrations.commands import (  # noqa: TC001
    Choice,
    CommandError,
    CommandEvent,
    Glob,
    command,
    parameter,
)
from bot.integrations.process_manager import ProcessManagerService
from bot.integrations.ravenfall import (
    RavenfallInstance,  # noqa: TC001
    RavenfallInstanceConverter,
    RavenfallService,
    RavenfallSkillChoice,
)
from bot.integrations.twitch import TwitchUsername
from bot.services.pastebin_service import PastebinService
from bot.services.prometheus_service import PrometheusService
from bot.services.remote_bot import RemoteBotService, RemoteCallableMixin, remote_callable
from ravenpy.enums import Skills
from utils import braille_graphics, strutils, utils
from utils.bytes_to_human_readable import bytes_to_human_readable
from utils.format_time import TimeSize, format_seconds, format_timedelta, seconds_to_dhms
from utils.strings import DIAMOND, EN_DASH, MULT_SIGN

if TYPE_CHECKING:
    from bot.clients.ravenfall_query import Player
    from bot.core.components import EventManager
    from bot.services.prometheus_service import FloatValue


class TownInfo(Struct):
    name: str
    boost: str


class TownInfos(Struct):
    towns: list[TownInfo]


class InfoCog(Cog, RemoteCallableMixin):
    """Epic info cog."""

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)

    @remote_callable(TownInfos)
    async def get_towns(self) -> TownInfos:
        """Get a list of registered towns."""
        ravenfall_srv = self.g_ctx.get_service(RavenfallService)
        town_info = TownInfos([])
        if not ravenfall_srv:
            return town_info
        rf_instances = ravenfall_srv.get_all_ravenfall_instances()
        tasks = [x.get_village() for x in rf_instances]
        town_villages = await asyncio.gather(*tasks, return_exceptions=True)
        for instance, t in zip(rf_instances, town_villages, strict=True):
            if not isinstance(t, Village):
                continue
            if not t.boost:
                boost_str = "No boost"
            else:
                boost_str = ", ".join(
                    f"{x.skill.name}: {x.multiplier:%}" for x in t.boost
                )
            town_info.towns.append(TownInfo(instance.channel_name, boost_str))
        return town_info

    @command()
    async def towns(self, ctx: CommandEvent):
        """Lists my towns."""
        remote_call_srv = self.g_ctx.get_service(RemoteBotService)
        towns: list[TownInfo] = []
        towns.extend((await self.get_towns()).towns)
        if remote_call_srv:
            tasks = [
                self.get_towns.call_remote(x)
                for x in remote_call_srv.remote_bots.values()
            ]
            for x in await asyncio.gather(*tasks, return_exceptions=True):
                if not isinstance(x, TownInfos):
                    continue
                towns.extend(x.towns)
        await ctx.reply(f" {DIAMOND} ".join(f"@{x.name} - {x.boost}" for x in towns))

    async def _event_text(self, instance: RavenfallInstance):
        watcher_service = self.g_ctx.get_service(RavenfallWatcherService)
        watcher = None
        if watcher_service:
            with contextlib.suppress(ValueError):
                watcher = watcher_service.get_watcher(instance.channel_name)
            if watcher and watcher.ravenfall_restart_lock.locked():
                return "Ravenfall is restarting..."

        raid = await instance.get_raid()
        if raid and raid.started:
            return (
                f"RAID {EN_DASH} "
                f"Boss HP: {raid.boss.health:,}/{raid.boss.max_health:,} "
                f"({raid.boss.health / raid.boss.max_health:.1%}) {EN_DASH} "
                f"Players: {raid.players:,} {EN_DASH} "
                f"Time left: {format_seconds(raid.time_left)}"
            )

        event_text = "No active event."
        dungeon = await instance.get_dungeon()
        if dungeon and dungeon.enemies > 0:
            dungeon_name = "DUNGEON"
            if dungeon.name:
                dungeon_name = f"DUNGEON: {dungeon.name}"
            if not dungeon.started:
                time_starting = format_seconds(dungeon.seconds_until_start)
                if dungeon.boss.health > 0:
                    event_text = (
                        f"{dungeon_name} starting in {time_starting} {EN_DASH} "
                        f"Boss HP: {dungeon.boss.health:,} {EN_DASH} "
                        f"Enemies: {dungeon.enemies:,} {EN_DASH} "
                        f"Players: {dungeon.players:,}"
                    )
                else:
                    event_text = (
                        f"{dungeon_name} is being prepared... {EN_DASH} "
                        f"Enemies: {dungeon.enemies:,}/49"
                    )
            else:
                event_text = (
                    f"{dungeon_name} {EN_DASH} "
                    f"Boss HP: {dungeon.boss.health:,}/{dungeon.boss.max_health:,} "
                    f"({dungeon.boss.health / dungeon.boss.max_health:.1%}) {EN_DASH} "
                    f"Enemies: {dungeon.enemies_alive:,}/{dungeon.enemies:,} {EN_DASH} "
                    f"Players: {dungeon.players_alive:,}/{dungeon.players:,} {EN_DASH} "
                    f"Elapsed time: {format_seconds(dungeon.elapsed)}"
                )
                if watcher:
                    time_left = watcher.config.max_dungeon_time_seconds - dungeon.elapsed
                    event_text += f" {EN_DASH} Time limit: {format_seconds(time_left)}"
        return event_text

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @command()
    async def event(self, ctx: CommandEvent, *, instance: RavenfallInstance):
        """Shows the current town event."""
        event_text = await self._event_text(instance)
        if event_text:
            await ctx.reply(event_text)
            return

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @command()
    async def uptime(self, ctx: CommandEvent, *, instance: RavenfallInstance):
        """Ravenfall uptime."""
        session = await instance.get_session()
        if not session:
            await ctx.reply("Ravenfall seems to be offline!")
            return
        await ctx.message.reply(
            f"Ravenfall uptime: {seconds_to_dhms(session.seconds_since_start)}"
        )

    @command()
    async def system(self, ctx: CommandEvent):
        """System diagnostics (CPU, RAM, battery, uptime)."""
        cpu_usage = await asyncio.to_thread(psutil.cpu_percent, 1)
        cpu_freq = psutil.cpu_freq().current
        ram = psutil.virtual_memory()
        ram_usage = ram.used  # pyright: ignore[reportAny]
        ram_total = ram.total  # pyright: ignore[reportAny]
        battery = psutil.sensors_battery()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        battery_text = ""
        if battery:
            battery_percent = battery.percent  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            battery_plugged = "Charging" if battery.power_plugged else "Not charging"  # pyright: ignore[reportUnknownMemberType]
            battery_time_left = format_seconds(battery.secsleft)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            battery_text = (
                f"Battery: {battery_percent}%, {battery_plugged} "
                f"({battery_time_left} left)"
            )
        uptime = time.time() - psutil.boot_time()
        await ctx.message.reply(
            strutils.strjoin(
                f" {EN_DASH} ",
                f"CPU: {cpu_usage / 100:.1%}, {cpu_freq:.0f} MHz",
                f"RAM: {bytes_to_human_readable(ram_usage)}/"  # pyright: ignore[reportAny]
                f"{bytes_to_human_readable(ram_total)}",  # pyright: ignore[reportAny]
                battery_text,
                f"Uptime: {seconds_to_dhms(uptime)}",
            )
        )

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter("all_", display_name="all", aliases=["a"])
    @command()
    async def rfram(
        self, ctx: CommandEvent, *, instance: RavenfallInstance | None, all_: bool = False
    ):
        """Show Ravenfall RAM usage for a channel or all channels."""
        prometheus_srv = self.g_ctx.require_service(PrometheusService)
        watcher_srv = self.g_ctx.require_service(RavenfallWatcherService)
        process_srv = self.g_ctx.require_service(ProcessManagerService)

        watchers = watcher_srv.get_all_watchers()
        proc_stats = await asyncio.gather(
            *[
                process_srv.get_process_statistics(
                    "Ravenfall", i.config.sandboxie_box_name
                )
                for i in watchers
            ]
        )
        watcher_stats = dict(zip(watchers, proc_stats, strict=True))

        working_set = await prometheus_srv.query(
            "windows_process_working_set_private_bytes{process='Ravenfall'}"
        )
        working_set_pids = {
            int(x.metric["process_id"]): x.value.value for x in working_set
        }

        change_over_time = await prometheus_srv.query(
            "deriv(windows_process_working_set_private_bytes{process='Ravenfall'}[3m])"
        )
        change_over_time_pids = {
            int(x.metric["process_id"]): x.value.value for x in change_over_time
        }
        working_set_series = await prometheus_srv.query_range(
            "windows_process_working_set_private_bytes{process='Ravenfall'}", 60 * 10
        )
        working_set_series_pids = {
            int(x.metric["process_id"]): x.values for x in working_set_series
        }
        if all_:
            out_str: list[str] = []
            for w in watchers:
                stats = watcher_stats[w]
                if not stats.pids:
                    continue
                pid = next(iter(stats.pids))
                proc_working_set = working_set_pids[pid]
                proc_change_over_time = change_over_time_pids[pid]

                sign = "+"
                if proc_change_over_time < 0:
                    sign = "-"
                out_str.append(
                    f"{w.ravenfall.channel_name} - "
                    f"{bytes_to_human_readable(int(proc_working_set))} "
                    f"({sign}{bytes_to_human_readable(int(proc_change_over_time))}/s)"
                )
            await ctx.reply(
                f"Ravenfall ram usage: {' • '.join(out_str)} | "
                "Showing change over 3 minutes"
            )
        else:
            if not instance:
                raise CommandError("An instance must be specified.")
            try:
                watcher = watcher_srv.get_watcher(instance.channel_name)
            except ValueError as e:
                raise CommandError("Instance is not monitored.") from e
            stats = watcher_stats[watcher]
            pid = next(iter(stats.pids))
            proc_working_set = working_set_pids[pid]
            proc_change_over_time = change_over_time_pids[pid]
            proc_working_set_series = working_set_series_pids[pid]
            graph = braille_graphics.simple_line_graph(
                proc_working_set_series, max_gap=30, width=26, fill_type=1, hard_min_val=1
            )
            await ctx.reply(
                f"[{graph}] Ravenfall is using "
                f"{bytes_to_human_readable(int(proc_working_set))} of memory; "
                f"changed by {bytes_to_human_readable(int(proc_change_over_time))}/s "
                "over 3 mins. (Graph: 10 minutes)"
            )

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter("target_user", converter=TwitchUsername)
    @command()
    async def exprate(
        self,
        ctx: CommandEvent,
        target_user: str | None = None,
        *,
        instance: RavenfallInstance,
    ):
        """Show a user's experience earn rate (exp/hour)."""
        if not target_user:
            target_user = ctx.message.author_login

        prometheus_srv = self.g_ctx.require_service(PrometheusService)
        query = (
            f"sum(rate(rf_player_stat_experience_total"
            f'{{player_name="{target_user}",session="{instance.channel_name}",stat!="health"}}[30s]))'
        )
        data = await prometheus_srv.query_range(query, 10 * 60)
        if len(data) == 0:
            await ctx.reply(
                "No data recorded. Your character may not be in this town right now."
            )
            return
        graph = braille_graphics.simple_line_graph(
            data[0].values, max_gap=30, width=26, min_val=1, fill_type=1
        )
        rate = data[0].values[-1].value * 60 * 60
        await ctx.reply(f"[{graph}] Earning {rate:,.0f} exp/h (graph: last 10 minutes)")

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter("target_user", converter=TwitchUsername)
    @command(aliases=["char", "show"])
    async def character(
        self,
        ctx: CommandEvent,
        target_user: str | None = None,
        *,
        instance: RavenfallInstance,
    ):
        """Show a player's character information and training status."""
        prometheus_srv = self.g_ctx.require_service(PrometheusService)

        if not target_user:
            target_user = ctx.message.author_login
        players = await instance.get_players()
        if not players:
            raise CommandError("Unable to get player data.")
        player: Player | None = None
        for p in players:
            if p.name == target_user:
                player = p
                break
        if not player:
            if target_user == ctx.message.author_login:
                await ctx.reply("You are not currently playing.")
            else:
                await ctx.reply("This user is currenly not playing.")
            return
        char = Character(player)
        ferry = await instance.get_ferry()
        if not ferry:
            raise CommandError("Unable to get player data.")

        where = ""
        if char.in_raid:
            where = "in a raid"
        if char.in_arena:
            where = "in the arena"
        if char.in_dungeon:
            where = "in a dungeon"
        if char.in_onsen:
            where = "in the onsen"

        index_and_combat_level = f"(Lv{char.combat_level})"

        what = ""
        if char.training in (
            Skills.Attack,
            Skills.Defense,
            Skills.Strength,
            Skills.Health,
            Skills.Magic,
            Skills.Ranged,
        ):
            what = f"training {char.training.name.lower()}"
        elif char.training in ravenpy.resource_skills:
            if char.target_item:
                what = (
                    f"{char.training.name.lower()} {char.target_item.item.name.lower()}"
                )
            else:
                what = f"{char.training.name.lower()}"
        elif char.training == Skills.Alchemy:
            what = "training alchemy"
        elif char.training == Skills.Sailing or char.training is None:
            pass
        else:
            what = f"{char.training.name.lower()}"

        if char.in_onsen:
            what = "resting"

        target_item = ""
        if char.target_item and what and not char.in_onsen:
            target_item = (
                f"{char.target_item.amount}{MULT_SIGN} {char.target_item.item.name}"
            )

        where_island = ""
        if char.island:
            where_island = f"at {char.island.name.capitalize()}"
        elif not (char.in_raid or char.in_dungeon):
            where_island = "sailing the seas"

        rested = ""
        if char.rested_time.total_seconds() > 0:
            s = TimeSize.SMALL_SPACES
            rested = (
                f"with {format_seconds(char.rested_time.total_seconds(), s)} of rest time"
            )

        captain = ""
        if ferry.captain.name == char.user_name:
            captain = "as the ship captain"

        stats: list[str] = []
        if not char.in_onsen:
            for char_stat in char.training_stats:
                skill_name = char_stat.skill.name.capitalize()
                stats.append(
                    f"{skill_name}: {char_stat.level} [+{char_stat.enchant_levels}] "
                    f"({char_stat.level_exp / char_stat.total_exp_for_level:.1%}) "
                    f"{char_stat.level_exp:,.0f}/{char_stat.total_exp_for_level:,.0f} EXP"
                )
        query = (
            f"sum(deriv(rf_player_stat_experience_total"
            f'{{player_name="{target_user}",session="{instance.channel_name}",stat!="health"}}[2m]))'
        )
        player_exp_data = await prometheus_srv.query(query)
        char_exp_per_h = 0.0
        has_exp_data = False
        if player_exp_data:
            char_exp_per_h = player_exp_data[0].value.value
            has_exp_data = True

        training_time_exp = timedelta(weeks=9999)
        if has_exp_data and char_exp_per_h > 0 and char.training:
            closest_stat = char.training_stats[0]
            exp_to_next_level = closest_stat.total_exp_for_level - closest_stat.level_exp
            training_time_exp = timedelta(
                seconds=(exp_to_next_level) / (char_exp_per_h / 60 / 60)
            )

        exp_per_hr = ""
        train_time = ""
        if has_exp_data:
            s = TimeSize.SMALL_SPACES
            train_time_format = format_timedelta(training_time_exp, s)
            if char.island and not char.in_onsen:
                if training_time_exp.total_seconds() > 60 * 60 * 24 * 100:  # 99 days
                    train_time = "Level in ∞"
                else:
                    train_time = f"Level in {train_time_format}"
            if char.island and not char.in_onsen:
                exp_per_hr = f"{char_exp_per_h:,.0f} exp/hr"

        coins = f"{strutils.pl(char.coins, 'coin')}"

        summary = strutils.strjoin(
            " ", index_and_combat_level, "is", what, where, where_island, captain, rested
        )
        out_str = strutils.strjoin(
            f" {EN_DASH} ",
            summary,
            target_item,
            strutils.strjoin(", ", *stats),
            exp_per_hr,
            train_time,
            coins,
        )
        user_name = f"{utils.unping(char.user_name)}"
        out_msgs = strutils.strjoin(" ", user_name, out_str)
        if train_time:
            out_msgs = strutils.strjoin("", out_msgs, " | Training time is estimated")
        await ctx.message.reply(out_msgs)

    @parameter("skill", converter=RavenfallSkillChoice())
    @parameter(
        "name_glob", aliases=["g", "f", "filter", "glob"], converter=Glob(), default="*"
    )
    @parameter(
        "invert_glob",
        aliases=["invert_filter", "if", "ig"],
        description="Invert the name filter",
    )
    @parameter("enchanted", aliases=["e"], description="Display enchanted stats")
    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @command(aliases=["h", "top_", "t"])
    async def highest_(
        self,
        ctx: CommandEvent,
        skill: str,
        *,
        name_glob: re.Pattern[str],
        invert_glob: bool = False,
        enchanted: bool = False,
        instance: RavenfallInstance,
    ):
        """Show the top player(s) for a given skill."""
        skill = skill.lower()
        players = await instance.get_players()
        if not players:
            raise CommandError("Ravenfall seems to be offline!")
        if not invert_glob:
            players = list(filter(lambda x: name_glob.match(x.name), players))
        else:
            players = list(filter(lambda x: not bool(name_glob.match(x.name)), players))

        if not players:
            await ctx.message.reply("No players!")
            return

        if enchanted:
            player_levels = [(x, x.stats.get_stat(skill).max_level) for x in players]
        else:
            player_levels = [(x, x.stats.get_stat(skill).level) for x in players]

        player_levels.sort(key=lambda x: x[1], reverse=True)
        top_level = player_levels[0][1]

        top_players: list[str] = []
        for player, level in player_levels:
            if level == top_level:
                top_players.append(utils.unping(player.name))
            else:
                break

        if len(top_players) == 0 or top_level == 0:
            await ctx.message.reply(f"Nobody has trained {skill}!")
        elif len(top_players) == 1:
            await ctx.message.reply(f"{top_players[0]} has level {top_level} {skill}!")
        else:
            top_players.sort()
            joined = strutils.strjoin(", ", *top_players, before_end=" and ")
            await ctx.message.reply(f"{joined} have level {top_level} {skill}!")

    @parameter(
        "sort_by", aliases=["s"], converter=Choice(["name", "combatlevel", "none"])
    )
    @parameter(
        "group_by", aliases=["g"], converter=Choice(["training", "island", "none"])
    )
    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter(
        "name_glob", aliases=["filter", "f", "glob"], converter=Glob(), default="*"
    )
    @parameter(
        "invert_glob",
        aliases=["invert_filter", "if", "ig"],
        description="Invert the name filter",
    )
    @cooldown(1, 10, BucketType.CHANNEL)
    @command(aliases=["player list", "players"])
    async def playerlist(
        self,
        ctx: CommandEvent,
        *,
        sort_by: str = "name",
        group_by: str = "none",
        name_glob: re.Pattern[str],
        invert_glob: bool = False,
        instance: RavenfallInstance,
    ):
        """List players in the channel with optional sorting and grouping."""
        prometheus_srv = self.g_ctx.require_service(PrometheusService)
        pastebin_srv = self.g_ctx.require_service(PastebinService)
        players = await instance.get_players()
        multiplier = await instance.get_multiplier()
        village = await instance.get_village()
        if players is None or multiplier is None or village is None:
            await ctx.reply("Ravenfall seems to be offline!")
            return

        total_player_count = len(players)
        if not invert_glob:
            players = list(filter(lambda x: name_glob.match(x.name), players))
        else:
            players = list(filter(lambda x: not bool(name_glob.match(x.name)), players))
        filtered_player_count = len(players)
        if not players:
            await ctx.message.reply("No players!")
            return
        players_parsed = [Character(x) for x in players]

        username_to_id: dict[str, str] = {}
        id_to_username: dict[str, str] = {}
        char_ids: list[str] = []
        for p in players_parsed:
            username_to_id[p.user_name] = p.id
            id_to_username[p.id] = p.user_name
            char_ids.append(p.id)
        query = (
            "sum by (player_id) (rate(rf_player_stat_experience_total"
            '{{player_id=~"{}",session="{}",stat!="health"}}[30s]))'.format(
                "|".join(char_ids), instance.channel_name
            )
        )
        char_exprate_series = await prometheus_srv.query_range(query, 10 * 60)

        char_exprates: dict[str, list[FloatValue]] = {}
        for series in char_exprate_series:
            char_exprates[id_to_username[series.metric["player_id"]]] = series.values

        match sort_by:
            case "name":
                players_parsed.sort(key=lambda x: x.user_name)
            case "combatlevel":
                players_parsed.sort(key=lambda x: x.combat_level, reverse=True)
            case _:
                pass

        players_grouped: dict[str, list[Character]] = defaultdict(list)

        match group_by:
            case "training":
                for a in [
                    "Attack",
                    "Defense",
                    "Strength",
                    "Health",
                    "Magic",
                    "Ranged",
                    "Woodcutting",
                    "Fishing",
                    "Mining",
                    "Crafting",
                    "Cooking",
                    "Farming",
                    "Slayer",
                    "Sailing",
                    "Healing",
                    "Gathering",
                    "Alchemy",
                    "Not training",
                ]:
                    players_grouped[a] = []

                for p in players_parsed:
                    if p.training:
                        players_grouped[p.training.name].append(p)
                    else:
                        players_grouped["Not training"].append(p)
            case "island":
                for a in [
                    "Home",
                    "Away",
                    "Ironhill",
                    "Kyo",
                    "Heim",
                    "Atria",
                    "Eldara",
                    "Unknown",
                ]:
                    players_grouped[a] = []
                for p in players_parsed:
                    if p.island:
                        players_grouped[p.island.name].append(p)
                    else:
                        players_grouped["Unknown"].append(p)
            case _:
                players_grouped[""] = players_parsed

        out_str: list[str] = []

        top_line: list[str] = []
        top_line.append(f"Player info for {instance.channel_name} ")
        if total_player_count != filtered_player_count:
            top_line.append("(filtered) ")
        top_line.append(f"(as of {datetime.now(UTC).strftime('%d %b %Y %H:%M:%S UTC')})")

        out_str.append("".join(top_line))
        out_str.append("")
        mult = int(multiplier.multiplier)
        if mult <= 1:
            out_str.append(f"Global multiplier: {mult}x.")
        else:
            out_str.append(
                f"Global multiplier: {mult} - "
                f"Ends in: {format_seconds(multiplier.time_left, TimeSize.LONG)} - "
                f"Event: {multiplier.event_name}"
            )
        if village.boost:
            out_str.append(
                f"Boosts: "
                f"{', '.join(f'{x.skill}: {x.multiplier:.4f}x' for x in village.boost)}"
            )
        else:
            out_str.append("Boosts: No active boosts.")
        out_str.append(f"Current event: {await self._event_text(instance)}")
        out_str.append("")

        char_actions: dict[str, tuple[str, str]] = {}
        for char in players_parsed:
            action_symbol = " "
            training_skill_is_maxed = False
            if char.training:
                if char.training in (Skills.All, Skills.Health):
                    t_skill = min(
                        char.attack, char.defense, char.strength, key=lambda x: x.level
                    )
                else:
                    t_skill = char.get_skill(char.training)
                _max_lvl = 999
                training_skill_is_maxed = t_skill.level == _max_lvl
            if training_skill_is_maxed:
                action_symbol = "-"

            rec_island = ""
            if (
                char.training
                and char.training != Skills.Sailing
                and not (char.in_raid or char.in_dungeon)
            ):
                if char.training in (Skills.All, Skills.Health):
                    skill = max(
                        char.attack,
                        char.defense,
                        char.strength,
                        key=lambda x: x.level,
                    )
                else:
                    skill = char.get_skill(char.training)

                is_training_combat = skill.skill in ravenpy.fighting_skills
                recommended_island_min = ravenpy.get_island_for_level(skill.level)
                recommended_island_max = recommended_island_min
                if is_training_combat and skill.level < char.combat_level:
                    recommended_island_max = max(
                        ravenpy.get_island_for_level(char.combat_level),
                        recommended_island_min,
                        key=lambda x: x.value,
                    )

                if (not training_skill_is_maxed) and (
                    (not char.island or char.island.value > recommended_island_max.value)
                    or char.island.value < recommended_island_min.value
                ):
                    rec_island = f"Sail to {recommended_island_max.name.capitalize()}"
                    action_symbol = "*"

            not_earning = ""
            if (
                char.user_name in char_exprates
                and (not char.is_resting)
                and (not any(y != 0 for _, y in char_exprates[char.user_name][-15:]))
                and not training_skill_is_maxed
            ):
                not_earning = "Not earning exp"
                action_symbol = "x"

            char_actions[char.user_name] = (
                action_symbol,
                strutils.strjoin(", ", not_earning, rec_island),
            )

        out_str.append(
            utils.fill_whitespace(
                f"A "
                f"{'USER NAME'.ljust(24)}  "
                f"{'C.LEVEL'.ljust(7)}  "
                f"{'STATUS'.ljust(7)}  "
                f"{'ISLAND'.ljust(8)}  "
                f"{'RstTIME'.ljust(7)}  "
                f"{'XP RATE'.rjust(13)} "
                f"GRAPH (10min) -- "
                f"TRAINING SKILL  ",
                "-",
            )
        )

        first = True
        for group_name, items in players_grouped.items():
            if not items:
                continue
            if group_name:
                if first:
                    out_str.append("")
                out_str.append(f"{group_name} ({len(items)}) --- -- -- - -")
            first = False
            for char in items:
                what = ""

                stats: list[str] = []
                for char_stat in char.training_stats:
                    skill_name = char_stat.skill.name
                    enchant_levels = ""
                    if char_stat.enchant_levels > 0:
                        enchant_levels = f"[+{char_stat.enchant_levels}]"
                    stat_text = strutils.strjoin(
                        " ",
                        f"{skill_name} {char_stat.level}",
                        enchant_levels,
                        f"({char_stat.level_exp / char_stat.total_exp_for_level:.1%})",
                    )
                    stats.append(stat_text)
                what = strutils.strjoin(", ", *stats)

                where = ""
                if char.in_raid:
                    where = "raid"
                if char.in_arena:
                    where = "arena"
                if char.in_dungeon:
                    where = "dungeon"
                if char.in_onsen:
                    where = "resting"

                where_island = ""
                if char.island:
                    where_island = f"{char.island.name.capitalize()}"

                rest_time = "0s"
                if char.rested_time.total_seconds() > 0:
                    s = TimeSize.SMALL
                    rest_time = format_seconds(char.rested_time.total_seconds(), s, 2)
                    if char.in_onsen:
                        rest_time += "+"
                    else:
                        rest_time += "-"

                if char.user_name not in char_exprates:
                    graph = " " * (26 - 10)
                    exp_h = ""
                else:
                    series = char_exprates[char.user_name]
                    graph = braille_graphics.simple_line_graph(
                        series,
                        max_gap=30,
                        width=26,
                        fill_type=1,
                        hard_min_val=1,
                        monospace=True,
                    )
                    exp_h = numerize(series[-1][1]) + " exp/h"

                char_action = " "
                if char_actions[char.user_name]:
                    char_action, _ = char_actions[char.user_name]

                out_str.append(
                    utils.fill_whitespace(
                        f"{char_action} "
                        f"{char.user_name.ljust(24)}  "
                        f"Lv.{str(char.combat_level).ljust(4)}  "
                        f"{where.ljust(7)}  "
                        f"{where_island.ljust(8)}  "
                        f"{rest_time.ljust(7)}  "
                        f"{exp_h.rjust(13)} "
                        f"{graph} "
                        f"{what}  ",
                        ".",
                    )
                )
            out_str.append("")

        has_required_actions = False
        for a in char_actions.values():
            if a and a[1]:
                has_required_actions = True
                break
        if has_required_actions:
            out_str.append("Required actions for characters:")
            for char_name, (_, action) in char_actions.items():
                if not action:
                    continue
                out_str.append(
                    utils.fill_whitespace(f"{char_name.ljust(24)}  {action}", ".")
                )
            out_str.append("")

        if total_player_count == filtered_player_count:
            player_count_text = (
                f"{strutils.pl2(total_player_count, 'player', 'players')} "
                f"in {instance.channel_name}"
            )
            out_str.append(
                f"{strutils.pl2(total_player_count, 'player', 'players')} total"
            )
        else:
            player_count_text = (
                f"{filtered_player_count}/{total_player_count} "
                f"{strutils.pl2(filtered_player_count, 'player', 'players', False)} "
                f"in {instance.channel_name}"
            )
            out_str.append(
                f"{filtered_player_count}/{total_player_count} "
                f"{strutils.pl2(filtered_player_count, 'player', 'players', False)} total"
            )
        out_str.append("")
        upload_result = await pastebin_srv.upload_text("\n".join(out_str))
        await ctx.message.reply(f"{player_count_text}: {upload_result.url}")
