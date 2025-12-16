from __future__ import annotations

from typing import Dict, List

from utils.format_time import seconds_to_dhms, format_seconds, format_timedelta, TimeSize
from utils.is_twitch_username import is_twitch_username
from utils.bytes_to_human_readable import bytes_to_human_readable
from utils.filter_username import filter_username
from utils.runshell import runshell
from utils import (
    strutils, utils
)


from ..prometheus import get_prometheus_instant, get_prometheus_series

from ..commands import Context, Commands, checks, parameter
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager
from ..models import Village, GameSession, GameMultiplier
from .. import braille
from ..ravenfall import Character, Skills

from utils.commands_rf import RFChannelConverter, TwitchUsername, RFSkill, Choice
from ..command_utils import Glob
from ..ravenfallchannel import RFChannel
from ..models import Player
import re

import ravenpy

from datetime import timezone, timedelta, datetime
import psutil
import asyncio
import time
import os
from collections import defaultdict

from numerize.numerize import numerize


class InfoCog(Cog):
    def __init__(self, rf_manager: RFChannelManager, **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager
    
    @Cog.command(name="towns", help="Lists all towns")
    async def towns(self, ctx: Context):
        out_str = []
        for idx, channel in enumerate(self.rf_manager.channels):
            village: Village = await channel.get_query('select * from village')
            if not isinstance(village, dict):
                continue
            if len(village['boost'].strip()) > 0:
                split = village['boost'].split()
                boost_stat = split[0]
                boost_value = float(split[1].rstrip("%"))
                asdf = f"Town #{idx+1}: @{channel.channel_name} - {boost_stat} {int(round(boost_value))}%"
            else:
                asdf = f"Town #{idx+1}: @{channel.channel_name} - No boost"
            if channel.custom_town_msg:
                asdf += f" {channel.custom_town_msg}"
            out_str.append(asdf)
        out_str.append("Other Ravenfall towns - https://www.ravenfall.stream/towns")
        await ctx.reply(' ✦ '.join(out_str))

    @Cog.command(name="event", help="Gets the town's current event")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def event(self, ctx: Context, channel: RFChannel = 'this'):
        await ctx.reply(channel.event_text)

    @Cog.command(name="uptime", help="Gets the town's uptime")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def uptime(self, ctx: Context, channel: RFChannel = 'this'):
        session: GameSession = await channel.get_query('select * from session')
        if not isinstance(session, dict):
            await ctx.reply("Ravenfall seems to be offline!")
            return
        await ctx.reply(f"Ravenfall uptime: {seconds_to_dhms(session['secondssincestart'])}")
    
    @Cog.command(name="system", help="System info of the computer running everything")
    async def system(self, ctx: Context):
        cpu_usage = await asyncio.to_thread(psutil.cpu_percent, 1)
        cpu_freq = psutil.cpu_freq().current
        ram = psutil.virtual_memory()
        ram_usage = ram.used
        ram_total = ram.total
        battery = psutil.sensors_battery()
        battery_text = ""
        if battery:
            battery_percent = battery.percent
            battery_plugged = "Charging" if battery.power_plugged else "Not charging"
            battery_time_left = format_seconds(battery.secsleft)
            battery_text = f"Battery: {battery_percent}%, {battery_plugged} ({battery_time_left} left)"
        uptime = time.time() - psutil.boot_time()
        await ctx.reply(strutils.strjoin(
            " – ", 
            f"CPU: {cpu_usage/100:.1%}, {cpu_freq:.0f} MHz",
            f"RAM: {bytes_to_human_readable(ram_usage)}/{bytes_to_human_readable(ram_total)}",
            battery_text,
            f"Uptime: {seconds_to_dhms(uptime)}"
        ))

    @Cog.command(name="rfram", help="Ravenfall RAM usage")
    @parameter("all_", display_name="all", aliases=["a"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def rfram(self, ctx: Context, channel: RFChannel = 'this', all_: bool = False):
        processes: Dict[str, List[float]] = {}
        working_set = await get_prometheus_instant("windows_process_working_set_private_bytes{process='Ravenfall'}")
        change_over_time = await get_prometheus_instant("deriv(windows_process_working_set_private_bytes{process='Ravenfall'}[3m])")
        working_set_series = await get_prometheus_series("windows_process_working_set_private_bytes{process='Ravenfall'}", 60*10)
        tasks = []
        for ch in self.rf_manager.channels:
            shellcmd = (
                f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{ch.sandboxie_box} /silent /listpids"
            )
            tasks.append(runshell(shellcmd))
        responses: List[str | None] = await asyncio.gather(*tasks)
        if None in responses:
            await ctx.reply("Could not get data")
            return
        pid_lists = [x.splitlines() for code, x in responses]
        box_pids = {}
        for i in range(len(self.rf_manager.channels)):
            box_pids[self.rf_manager.channels[i].channel_name] = pid_lists[i]
        for metric in working_set:
            m = metric['metric']
            name = m['process_id']
            processes[name] = [int(metric['value'][1])]
        for metric in change_over_time:
            m = metric['metric']
            name = m['process_id']
            if name in processes:
                processes[name].append(float(metric['value'][1]))
        for metric in working_set_series:
            m = metric['metric']
            name = m['process_id']
            data_pairs = [(x[0], float(x[1])) for x in metric['values']]
            if name in processes:
                processes[name].append(data_pairs)
        processes_named: Dict[str, List[float]] = {}
        for name, pids in box_pids.items():
            for pid in pids:
                if pid in processes:
                    processes_named[name] = processes[pid]
                    break
        out_str = []
        if all_:
            for name, (bytes_used, change, series) in processes_named.items():
                s = "+"
                if change < 0:
                    s = ''
                out_str.append(
                    f"{name} - {bytes_to_human_readable(bytes_used)} ({s}{bytes_to_human_readable(change)}/s)"
                )
            await ctx.reply(f"Ravenfall ram usage: {' • '.join(out_str)} | Showing change over 3 minutes")
        else:
            bytes_used, change, series = processes_named[channel.channel_name]
            graph = braille.simple_line_graph(
                series, max_gap=30, width=26, fill_type=1, hard_min_val=1
            )
            await ctx.reply(
                f"[{graph}] Ravenfall is using {bytes_to_human_readable(bytes_used)} of memory; "
                f"changed by {bytes_to_human_readable(change)}/s over 3 mins. (Graph: 10 minutes)"
            )

    @Cog.command(
        name="exprate", 
        help="Gets a user's experience earn rate",
        aliases=["expirate"]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def exprate(self, ctx: Context, target_user: TwitchUsername = None, channel: RFChannel = 'this'):
        if not target_user:
            target_user = ctx.author        
        query = "sum(rate(rf_player_stat_experience_total{player_name=\"%s\",session=\"%s\",stat!=\"health\"}[30s]))" % (target_user, channel.channel_name)
        data = await get_prometheus_series(query, 10*60)
        if len(data) == 0:
            await ctx.reply("No data recorded. Your character may not be in this town right now.")
            return
        data_pairs = [(x[0], float(x[1])) for x in data[0]['values']]
        graph = braille.simple_line_graph(
            data_pairs, max_gap=30, width=26, min_val=1, fill_type=1
        )
        await ctx.reply(
            f"[{graph}] Earning {data_pairs[-1][1]*60*60:,.0f} exp/h (graph: last 10 minutes)"
        )

    @Cog.command(
        name="character", 
        help="Gets a user's character info",
        aliases=["char", "show"]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def character(self, ctx: Context, target_user: TwitchUsername = None, channel: RFChannel = 'this'):
        if not target_user:
            target_user = ctx.author        

        player_info = await channel.get_query("select * from players where name = \'%s\'" % target_user)
        if isinstance(player_info, dict) and player_info:
            char = Character(player_info)
        else:
            await ctx.reply("You are not currently playing.")
            return
        ferry_info = await channel.get_query("select * from ferry")

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
        if char.training in (Skills.Attack, Skills.Defense, Skills.Strength, Skills.Health, Skills.Magic, Skills.Ranged):
            what = f"training {char.training.name.lower()}"
        elif char.training in ravenpy.resource_skills:
            if char.target_item:
                what = f"{char.training.name.lower()} {char.target_item.item.name.lower()}"
            else:
                what = f"{char.training.name.lower()}"
        elif char.training == Skills.Alchemy:
            what = f"training alchemy"
        elif char.training == Skills.Sailing:
            pass
        elif char.training is None:
            pass
        else:
            what = f"{char.training.name.lower()}"

        if char.in_onsen:
            what = "resting"

        target_item = ""
        if char.target_item and what and not char.in_onsen:
            target_item = f"{char.target_item.amount}× {char.target_item.item.name}"

        where_island = ""
        if char.island:
            where_island = f"at {char.island.name.capitalize()}"
        elif not (char.in_raid or char.in_dungeon):
            where_island = "sailing the seas"
            
        rested = ""
        if char.rested_time.total_seconds() > 0:
            s = TimeSize.SMALL_SPACES 
            rested = f"with {format_seconds(char.rested_time.total_seconds(),s)} of rest time"

        captain = ""
        if ferry_info['captain']['name'] == char.user_name:
            captain = "as the ship captain"
            
        stats = []
        if not char.in_onsen:
            for char_stat in char.training_stats:
                skill_name = char_stat.skill.name.capitalize()
                stats.append(
                    f"{skill_name}: {char_stat.level} [+{char_stat.enchant_levels}] "\
                    f"({char_stat.level_exp/char_stat.total_exp_for_level:.1%}) "\
                    f"{char_stat.level_exp:,.0f}/{char_stat.total_exp_for_level:,.0f} EXP"
                )
        query = "sum(deriv(rf_player_stat_experience_total{player_name=\"%s\",session=\"%s\",stat!=\"health\"}[2m]))" % (target_user, channel.channel_name)
        data = await get_prometheus_series(query, 1)
        data_pairs = [(x[0], float(x[1])) for x in data[0]['values']]
        char_exp_per_h = data_pairs[-1][1]*60*60
        train_time = ""
        if char_exp_per_h > 0 and char.training:
            closest_stat = char.training_stats[0]
            exp_to_next_level = closest_stat.total_exp_for_level-closest_stat.level_exp
            training_time_exp = timedelta(seconds=(exp_to_next_level) / (char_exp_per_h/60/60))
        else:
            training_time_exp = timedelta(weeks=9999)
        s = TimeSize.SMALL_SPACES
        train_time_format = format_timedelta(training_time_exp, s)
        now = datetime.now(timezone.utc)
        if char.island and not char.in_onsen:
            if training_time_exp.total_seconds() > 60*60*24*100:  # 99 days
                train_time = f"Level in ∞"
            else:
                train_time = f"Level in {train_time_format}"
        exp_per_hr = f""
        if char.island and not char.in_onsen:
            exp_per_hr = f"{char_exp_per_h:,.0f} exp/hr"
            
        coins = f"{utils.pl(char.coins, 'coin')}"

        summary = utils.strjoin(
            " ", index_and_combat_level, "is", what, where, where_island, captain, rested
        )
        out_str = utils.strjoin(
            " – ", summary, target_item, utils.strjoin(', ', *stats), exp_per_hr, train_time, coins
        )
        user_name = f"{utils.unping(char.user_name)}"
        out_msgs = utils.strjoin(" ", user_name, out_str)
        if train_time:
            out_msgs = utils.strjoin('', out_msgs, f" | Training time is estimated")
        await ctx.reply(out_msgs)

    @Cog.command(
        name="mult", 
        help="Gets the town's current multiplier",
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def mult(self, ctx: Context, channel: RFChannel = 'this'):
        multiplier = await channel.get_query("select * from multiplier")
        mult = int(multiplier['multiplier'])
        if not isinstance(multiplier, dict):
            await ctx.reply("Ravenfall seems to be offline!")
            return
        if mult <= 1:
            await ctx.reply(
                f"Current global exp multiplier is {mult}×."
            )
        else:
            await ctx.reply(
                f"Current global exp multiplier is {mult}×, "
                f"ending in {format_seconds(multiplier['timeleft'], TimeSize.LONG)}, "
                f"thanks to {multiplier['eventname']}!"
            )
            
    @Cog.command(help="Get the top player(s) of a skill", aliases=['h', 'top_', 't'])
    @parameter("skill", converter=RFSkill)
    @parameter("name_glob", aliases=['g', 'f', 'filter', 'glob'], help="Filter usernames using a glob expression", converter=Glob)
    @parameter("invert_glob", aliases=['invert_filter', 'if', 'ig'], help="Invert the name filter")
    @parameter("enchanted", aliases=["e"], help="Display enchanted stats")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def highest_(self, ctx: Context, skill: str, *, name_glob: re.Pattern = '*', invert_glob: bool = False, enchanted: bool = False, channel: RFChannel = 'this'):
        skill = skill.lower()
        players: List[Player] = await channel.get_query("select * from players")
        if not isinstance(players, list):
            await ctx.reply("Ravenfall seems to be offline!")
            return
        if not invert_glob:
            players = list(filter(lambda x : name_glob.match(x['name']), players))
        else:
            players = list(filter(lambda x : not bool(name_glob.match(x['name'])), players))
            
        if not players:
            await ctx.reply("No players!")
            return
        a = "level"
        if enchanted:
            a = "maxlevel"
        players.sort(key=lambda x : x["stats"][skill][a], reverse=True)
        
        top_level = players[0]["stats"][skill][a]
        top_players = []
        for player in players:
            if player["stats"][skill][a] == top_level:
                top_players.append(utils.unping(player["name"]))
            else:
                break
        
        if len(top_players) == 0 or top_level == 0:
            await ctx.reply(f"Nobody has trained {skill}!")
        elif len(top_players) == 1:
            await ctx.reply(f"{top_players[0]} has level {top_level} {skill}!")
        else:
            top_players.sort()
            joined = strutils.strjoin(", ", *top_players, before_end=" and ")
            await ctx.reply(f"{joined} have level {top_level} {skill}!")
    
    @Cog.command(name='playerlist', help="Lists players", aliases=['player list', 'players'])
    @parameter("sort_by", aliases=['s'], converter=Choice(['name', 'combatlevel', 'none']))
    @parameter("group_by", aliases=['g'], converter=Choice(['training', 'island', 'none']))
    @parameter("channel", aliases=["c"], converter=RFChannelConverter)
    @parameter("name_glob", aliases=['filter', 'f', "glob"], help="Filter usernames using a glob expression", converter=Glob)
    @parameter("invert_glob", aliases=['invert_filter', 'if', 'ig'], help="Invert the name filter")
    async def player_list(self, ctx: Context, *, sort_by: str = "name", group_by: str = "none", name_glob: re.Pattern = "*", invert_glob: bool = False, channel: RFChannel = 'this'):
        tasks = [
            channel.get_query("select * from players"),
            channel.get_query("select * from multiplier"),
            channel.get_query("select * from village")
        ]
        players: List[Player]
        multiplier: GameMultiplier
        village: Village 
        players, multiplier, village = await asyncio.gather(*tasks)
        if any([x is None for x in [players, multiplier, village]]):
            await ctx.reply("Ravenfall seems to be offline!")
            return
        
        total_player_count = len(players)
        if not invert_glob:
            players = list(filter(lambda x : name_glob.match(x['name']), players))
        else:
            players = list(filter(lambda x : not bool(name_glob.match(x['name'])), players))
        filtered_player_count = len(players)
        if not players:
            await ctx.reply("No players!")
            return
        players_parsed = [Character(x) for x in players]
        
        username_to_id = {}
        id_to_username = {}
        char_ids = []
        for p in players_parsed:
            username_to_id[p.user_name] = p.id
            id_to_username[p.id] = p.user_name
            char_ids.append(p.id)
        query = "sum by (player_id) (rate(rf_player_stat_experience_total{player_id=~\"%s\",session=\"%s\",stat!=\"health\"}[30s]))" \
        % ("|".join(char_ids), channel.channel_name)
        char_exprate_series = await get_prometheus_series(query, 10*60)
        
        char_exprates = {}
        for series in char_exprate_series:
            char_exprates[id_to_username[series['metric']['player_id']]] = series['values']
        
        match sort_by:
            case "name":
                players_parsed.sort(key=lambda x: x.user_name)
            case "combatlevel":
                players_parsed.sort(key=lambda x: x.combat_level, reverse=True)
                
        players_grouped: Dict[str, List[Character]] = defaultdict(list)
        
        match group_by:
            case "training":
                for a in ["Attack", "Defense", "Strength", "Health", "Magic", "Ranged", "Woodcutting", "Fishing", "Mining", "Crafting", "Cooking", "Farming", "Slayer", "Sailing", "Healing", "Gathering", "Alchemy", "Not training"]:
                    players_grouped[a] = []

                for p in players_parsed:
                    if p.training:
                        players_grouped[p.training.name].append(p)
                    else:
                        players_grouped["Not training"].append(p)
            case "island":
                for a in ["Home", "Away", "Ironhill", "Kyo", "Heim", "Atria", "Eldara", "Unknown"]:
                    players_grouped[a] = []
                for p in players_parsed:
                    if p.island:
                        players_grouped[p.island.name].append(p)
                    else:
                        players_grouped["Unknown"].append(p)                
            case _:
                players_grouped[""] = players_parsed
        
        out_str = []
        
        top_line = []
        top_line.append(f"Player info for {channel.channel_name} ")
        if total_player_count != filtered_player_count:
            top_line.append("(filtered) ")
        top_line.append(f"(as of {datetime.now(timezone.utc).strftime("%d %b %Y %H:%M:%S UTC")})")
        
        out_str.append(''.join(top_line))
        out_str.append("")
        mult = int(multiplier['multiplier'])
        if mult <= 1:
            out_str.append(f"Global multiplier: {mult}x.")
        else:
            out_str.append(
                f"Global multiplier: {mult} - "
                f"Ends in: {format_seconds(multiplier['timeleft'], TimeSize.LONG)} - "
                f"Event: {multiplier['eventname']}"
            )
        out_str.append(f"Boosts: {village['boost']}")
        out_str.append("")
        
        out_str.append(utils.fill_whitespace(
            f"{"USER NAME".ljust(24)}  "
            f"{"C.LEVEL".ljust(7)}  "
            f"{"STATUS".ljust(7)}  "
            f"{"ISLAND".ljust(8)}  "
            f"{"RstTIME".ljust(7)}  "
            f"{"XP RATE".rjust(13)} "
            f"GRAPH (10min) -- "
            f"TRAINING SKILL  ", 
            "-"
        ))
        
        for char in players_parsed:
            training_skill_is_maxed = False
            if char.training:
                if char.training in (Skills.All, Skills.Health):
                    t_skill = min(char.attack, char.defense, char.strength, key=lambda x: x.level)
                else:
                    t_skill = char.get_skill(char.training)
                training_skill_is_maxed = t_skill.level == 999 and (t_skill.level_exp / t_skill.total_exp_for_level) > 0.99                   
            
            rec_island = ""
            if char.training and not char.training == Skills.Sailing:
                if not (char.in_raid or char.in_dungeon):
                    if char.training in (Skills.All, Skills.Health):
                        skill = max(char.attack, char.defense, char.strength, key=lambda x: x.level)
                    else:
                        skill = char.get_skill(char.training)

                    is_training_combat = skill.skill in ravenpy.fighting_skills
                    recommended_island_min = ravenpy.get_island_for_level(skill.level)
                    recommended_island_max = recommended_island_min
                    if is_training_combat and skill.level < char.combat_level:
                        recommended_island_max = max(ravenpy.get_island_for_level(char.combat_level), recommended_island_min, key=lambda x: x.value)

                    if (not training_skill_is_maxed) and ((not char.island or char.island.value > recommended_island_max.value) or char.island.value < recommended_island_min.value):
                        rec_island = f"Sail to {recommended_island_max.name.capitalize()}"
            
            not_earning = ""
            
        
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

                stats = []
                for char_stat in char.training_stats:
                    skill_name = char_stat.skill.name
                    enchant_levels = ""
                    if char_stat.enchant_levels > 0:
                        enchant_levels = f"[+{char_stat.enchant_levels}]"
                    stats.append(utils.strjoin(" ",*(
                        f"{skill_name} {char_stat.level}",
                        enchant_levels, 
                        f"({char_stat.level_exp/char_stat.total_exp_for_level:.1%})"
                    )))
                what = utils.strjoin(', ', *stats)

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
                    rest_time = format_seconds(char.rested_time.total_seconds(),s,2)
                    if char.in_onsen:
                        rest_time += "+"
                    else:
                        rest_time += "-"
                        
                series = [[x, float(y)] for x, y in char_exprates[char.user_name]]
                graph = braille.simple_line_graph(
                    series, max_gap=30, width=26, fill_type=1, hard_min_val=1, monospace=True
                )
                
                out_str.append(utils.fill_whitespace(
                    f"{char.user_name.ljust(24)}  "
                    f"Lv.{str(char.combat_level).ljust(4)}  "
                    f"{where.ljust(7)}  "
                    f"{where_island.ljust(8)}  "
                    f"{rest_time.ljust(7)}  "
                    f"{(numerize(series[-1][1]) + " exp/h").rjust(13)} "
                    f"{graph} "
                    f"{what}  ", 
                    "."
                ))
            out_str.append("")    
        
        if total_player_count == filtered_player_count:
            player_count_text = (
                f"{utils.pl2(total_player_count, "player", "players")} in {channel.channel_name}"
            )
            out_str.append(f"{utils.pl2(total_player_count, "player", "players")} total")
        else:
            player_count_text = (
                f"{filtered_player_count}/{total_player_count} "
                f"{utils.pl2(filtered_player_count, "player", "players", False)} "
                f"in {channel.channel_name}"
            )
            out_str.append(
                f"{filtered_player_count}/{total_player_count} "
                f"{utils.pl2(filtered_player_count, "player", "players", False)} total"
            )
        out_str.append("")    
        url = await utils.upload_to_pastes("\n".join(out_str))
        await ctx.reply(
            f"{player_count_text}: {url}"
        )


def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(InfoCog, rf_manager=rf_manager, **kwargs)