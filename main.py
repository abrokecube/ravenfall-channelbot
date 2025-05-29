import time
from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticationStorageHelper
from twitchAPI.type import AuthScope, ChatEvent
from twitchAPI.chat import Chat, EventData, ChatMessage, ChatCommand
from twitchAPI.chat.middleware import *
import asyncio
import os
from dotenv import load_dotenv
import json
import aiohttp
import aiohttp.client_exceptions
from typing import List
from datetime import datetime, timedelta
import psutil
import shutil

from gotify import AsyncGotify
from gotify import gotify

from models import *
from utils.routines import routine
from utils.format_time import *
from utils import strutils
from utils import chatmsg_cd
from utils import langstuff
from utils.is_twitch_username import is_twitch_username
from utils import utils
from dataclasses import dataclass

import braille
import ravenpy
from ravenpy import Skills, Islands
from datetime import timezone, timedelta, datetime
load_dotenv()

USER_SCOPE = [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT]
GOTIFY_URL = os.getenv('GOTIFY_URL').strip("/")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL").strip('/')

class CharacterStat:
    def __init__(self, skill: ravenpy.Skills, data: PlayerStat):
        self.skill = skill
        self.level = data['level']
        self.level_exp = data['experience']
        self.total_exp_for_level = ravenpy.experience_for_level(self.level+1)
        self.enchant_percent = data['maxlevel']/data['level']
        self.enchant_levels = data['maxlevel'] - data['level']

    def _add_enchant(self, percent):
        self.enchant_percent += percent
        self.enchant_levels = round(self.level * self.enchant_percent)

class Character:
    def __init__(self, data: Player):
        self._raw: Dict = data
        self.time_recieved = datetime.now(timezone.utc)
        self.id: str = data["id"]
        self.char_id: str = self.id
        self.user_name: str = data['name']
        self.coins: int = data['coins']
        
        self.attack = CharacterStat(Skills.Attack, data['stats']['attack'])
        self.defense = CharacterStat(Skills.Defense, data['stats']['defense'])
        self.strength = CharacterStat(Skills.Strength, data['stats']['strength'])
        self.health = CharacterStat(Skills.Health, data['stats']['health'])
        self.magic = CharacterStat(Skills.Magic, data['stats']['magic'])
        self.ranged = CharacterStat(Skills.Ranged, data['stats']['ranged'])
        self.woodcutting = CharacterStat(Skills.Woodcutting, data['stats']['woodcutting'])
        self.fishing = CharacterStat(Skills.Fishing, data['stats']['fishing'])
        self.mining = CharacterStat(Skills.Mining, data['stats']['mining'])
        self.crafting = CharacterStat(Skills.Crafting, data['stats']['crafting'])
        self.cooking = CharacterStat(Skills.Cooking, data['stats']['cooking'])
        self.farming = CharacterStat(Skills.Farming, data['stats']['farming'])
        self.slayer = CharacterStat(Skills.Slayer, data['stats']['slayer'])
        self.sailing = CharacterStat(Skills.Sailing, data['stats']['sailing'])
        self.healing = CharacterStat(Skills.Healing, data['stats']['healing'])
        self.gathering = CharacterStat(Skills.Gathering, data['stats']['gathering'])
        self.alchemy = CharacterStat(Skills.Alchemy, data['stats']['alchemy'])
        self.combat_level = int(((self.attack.level + self.defense.level + self.health.level + self.strength.level) / 4) + ((self.ranged.level + self.magic.level + self.healing.level) / 8))
        self.stats = [
            self.attack, self.defense, self.strength, self.health, self.magic,
            self.ranged, self.woodcutting, self.fishing, self.mining, self.crafting,
            self.cooking, self.farming, self.slayer, self.sailing, self.healing,
            self.gathering, self.alchemy
        ]
        self._skill_dict = {
            Skills.Attack: self.attack,
            Skills.Defense: self.defense,
            Skills.Strength: self.strength,
            Skills.Health: self.health,
            Skills.Woodcutting: self.woodcutting,
            Skills.Fishing: self.fishing,
            Skills.Mining: self.mining,
            Skills.Crafting: self.crafting,
            Skills.Cooking: self.cooking,
            Skills.Farming: self.farming,
            Skills.Slayer: self.slayer,
            Skills.Magic: self.magic,
            Skills.Ranged: self.ranged,
            Skills.Sailing: self.sailing,
            Skills.Healing: self.healing,
            Skills.Gathering: self.gathering,
            Skills.Alchemy: self.alchemy
        }
        self.hp: int = data['stats']["health"]["currentvalue"]
        self.in_raid: bool = data['inraid']
        self.in_arena: bool = data['inarena']
        self.in_dungeon: bool = data['indungeon']
        self.in_onsen: bool = data['resting']
        self.is_resting: bool = self.in_onsen
        self.is_sailing: bool = data['sailing']
        
        self.training: Skills | None = None
        self.island: Islands = Islands[data['island']] if data['island'] != "" else None
   
        self.rested_time = timedelta(seconds=int(data['restedtime']))
        self.target_item: ravenpy.CharacterItem | None = None
        if data['training'] == "Fighting":
            task_arg = data['taskargument'].capitalize()
            replace = ravenpy.fighting_replacements.get(task_arg)
            if replace:
                task_arg = replace
            self.training = Skills[task_arg]
        elif (not data['training']) or data['training'].lower() == "none":
            pass
        else:
            self.training = Skills[data['training'].capitalize()]
            result = ravenpy.get_item(data['taskargument'])
            if result:
                target_item = result
                inv_item = ravenpy.CharacterItem(
                    itemId=target_item.id,
                    amount=0,
                    equipped=False,
                    soulbound=False,
                    enchantment=''
                )
                self.target_item = inv_item

        if not self.training:
            if (not self.island) or self.is_sailing:
                self.training = Skills.Sailing
                
        self.training_stats: List[CharacterStat] = []
        if self.training:
            if self.training in (Skills.All, Skills.Health):
                self.training_stats.extend([self.health, self.attack, self.defense, self.strength])
            else:
                self.training_stats.append(self.get_skill(self.training))
                if self.training in ravenpy.combat_skills:
                    self.training_stats.append(self.health)

            if self.in_raid or self.in_dungeon:
                self.training_stats.append(self.slayer)
        self.training_skills: List[Skills] = []
        for char_stat in self.training_stats:
            self.training_skills.append(char_stat.skill)
            
    def get_skill(self, skill: Skills):
        return self._skill_dict[skill]

with open("channels.json", "r") as f:
    channels: List[Channel] = json.load(f)
for channel in channels:
    channel["rf_query_url"] = channel["rf_query_url"].rstrip("/")

async def on_ready(ready_event: EventData):
    update_task.start(ready_event.chat)
    update_events.start()
    update_mult.start(ready_event.chat)
    backup_state_data.start()
    print('Bot is ready for work')
    ...

async def on_message(msg: ChatMessage):
    ch_data = get_channel_data(msg.room.room_id)
    if msg.first and msg.text[:5].lower() == f"{ch_data['ravenbot_prefix']}join":
        await first_time_joiner(msg)

@chatmsg_cd.chat_autoresponse_cd(5, chatmsg_cd.CooldownType.CHANNEL)
async def first_time_joiner(msg: ChatMessage):
    await asyncio.sleep(3)
    ch = get_channel_data(msg.room.room_id) 
    if ch is None:
        return
    boost = await get_town_boost(ch)
    welcome_msg = ch['welcome_message'].format_map({
        "townSkillLower": boost[0].skill.lower(),
        "townSkill": boost[0].skill,
        "userName": msg.user.name,
        "userDisplayName": msg.user.display_name
    })
    await msg.chat.send_message(msg.room.name, welcome_msg)
    

async def test_cmd(cmd: ChatCommand):
    await cmd.reply(f'hello {cmd.user.name}')


async def towns_cmd(cmd: ChatCommand):
    async with aiohttp.ClientSession() as session:
        r = await asyncio.gather(*[
            session.get(f"{x['rf_query_url']}/select * from village") for x in channels
        ], return_exceptions=True)
        tasks = []
        for x in r:
            if isinstance(x, Exception):
                continue
            tasks.append(x.json())
        villages: List[Village] = await asyncio.gather(*tasks)
    out_str = []
    for idx, village in enumerate(villages):
        if not isinstance(village, dict):
            continue
        channel: Channel = channels[idx]
        if len(village['boost'].strip()) > 0:
            split = village['boost'].split()
            boost_stat = split[0]
            boost_value = float(split[1].rstrip("%"))
            asdf = f"Town #{idx+1}: @{channel['channel_name']} - {boost_stat} {int(round(boost_value))}%"
        else:
            asdf = f"Town #{idx+1}: @{channel['channel_name']} - No boost"
        if channel['custom_town_msg']:
            asdf += f" {channel['custom_town_msg']}"
        out_str.append(asdf)
        
    await cmd.reply(' ✦ '.join(out_str))

def get_channel_data(channel_id) -> Channel | None:
    for channel in channels:
        if channel['channel_id'] == channel_id:
            return channel
    return None

async def get_town_boost(channel: Channel) -> List[TownBoost] | None:
    async with aiohttp.ClientSession() as session:
        try:
            r = await session.get(f"{channel['rf_query_url']}/select * from village")
        except aiohttp.client_exceptions.ContentTypeError:
            return None
        village: Village = await r.json()
        split = village['boost'].split()
        if len(split) < 2:
            return []
        boost_stat = split[0]
        boost_value = float(split[1].rstrip("%"))
        return [TownBoost(boost_stat, boost_value/100)]
        
async def update_cmd(cmd: ChatCommand):
    this_channel: Channel = None
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            town_boost = await get_town_boost(channel)
            this_channel = channel
            break
    else:
        await cmd.reply("Town not found :(")
        return
    if town_boost is None:
        await cmd.reply("This channel's town is currently not online!")
        return
    if len(town_boost) == 0:
        await cmd.reply(f"This town has no active boost.")
        return
    await cmd.send(f"{this_channel['ravenbot_prefix']}town {town_boost[0].skill.lower()}")
    
    
village_events: Dict[str, str] = {}
async def event_cmd(cmd: ChatCommand):
    if not cmd.room.room_id in village_events:
        await cmd.reply("No active event.")
        return
    await cmd.reply(f"{village_events[cmd.room.room_id]}")

async def uptime_cmd(cmd: ChatCommand):
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            village_url = channel['rf_query_url']
            break
    else:
        await cmd.reply("Town not found :(")
        return
    async with aiohttp.ClientSession() as session:
        try:
            r = await session.get(f"{village_url}/select * from session")
        except aiohttp.client_exceptions.ContentTypeError:
            await cmd.reply("Ravenfall uptime: Offline!")
            return
        game_session: GameSession = await r.json()
        await cmd.reply(f"Ravenfall uptime: {seconds_to_dhms(game_session['secondssincestart'])}")

def bytes_to_human_readable(size_bytes):
    if size_bytes == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB']
    power = 1024
    unit_index = 0

    while size_bytes >= power and unit_index < len(units) - 1:
        size_bytes /= power
        unit_index += 1

    return f"{size_bytes:.2f} {units[unit_index]}"

async def system_cmd(cmd: ChatCommand):
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
    await cmd.reply(strutils.strjoin(
        " – ", 
        f"CPU: {cpu_usage/100:.1%}, {cpu_freq:.0f} MHz",
        f"RAM: {bytes_to_human_readable(ram_usage)}/{bytes_to_human_readable(ram_total)}",
        battery_text,
        f"Uptime: {seconds_to_dhms(uptime)}"
    ))

async def get_prometheus_series(query: str, duration_s, step_s=20):
    now = time.time()
    start = now - duration_s
    async with aiohttp.ClientSession() as session:
        r = await session.get(
            f"{PROMETHEUS_URL}/api/v1/query_range?query={query}&start={start}&end={now}&step={step_s}"
        )
        result = await r.json()
    data = result['data']['result']
    return data

def backup_file_with_date(filepath, max_backups=5):
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"The file '{filepath}' does not exist.")

    base_dir = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    name, ext = os.path.splitext(filename)

    # Create backup directory
    backup_dir = os.path.join(base_dir, "backup")
    os.makedirs(backup_dir, exist_ok=True)

    # Create new backup filename
    date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    new_filename = f"{name}_{date_str}{ext}"
    new_filepath = os.path.join(backup_dir, new_filename)

    # Copy the file
    shutil.copy2(filepath, new_filepath)

    # Find all existing backups for this file
    backups = []
    for file in os.listdir(backup_dir):
        if file.startswith(name + "_") and file.endswith(ext):
            full_path = os.path.join(backup_dir, file)
            if os.path.isfile(full_path):
                backups.append((full_path, os.path.getctime(full_path)))

    # Sort by creation time (oldest first)
    backups.sort(key=lambda x: x[1])

    # Remove oldest if exceeding max_backups
    while len(backups) > max_backups:
        oldest_file = backups.pop(0)[0]
        os.remove(oldest_file)

    return new_filepath
async def exprate_cmd(cmd: ChatCommand):
    args = cmd.parameter.split()
    target_user = cmd.user.name
    if len(cmd.parameter) > 2 and len(args) > 0:
        target_user = args[0].strip('@')
    if not is_twitch_username(target_user):
        await cmd.reply("Invalid username.")
        return
    
    query = "sum(rate(rf_player_stat_experience_total{player_name=\"%s\",session=\"%s\"}[90s]))" % (target_user, cmd.room.name)
    data = await get_prometheus_series(query, 10*60)
    if len(data) == 0:
        await cmd.reply("No data recorded. Your character may not be in this town right now.")
        return
    data_pairs = [(x[0], float(x[1])) for x in data[0]['values']]
    graph = braille.simple_line_graph(
        data_pairs, max_gap=30, width=26, min_val=1, fill_type=1
    )
    await cmd.reply(
        f"[{graph}] Earning {data_pairs[-1][1]*60*60:,.0f} exp/h (graph: last 10 minutes)"
    )
    ...

async def chracter_cmd(cmd: ChatCommand):
    args = cmd.parameter.split()
    target_user = cmd.user.name
    if len(cmd.parameter) > 2 and len(args) > 0:
        target_user = args[0].strip('@')
    if not is_twitch_username(target_user):
        await cmd.reply("Invalid username.")
        return

    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            village_url = channel['rf_query_url']
            break
    else:
        await cmd.reply("Town not found :(")
        return
    async with aiohttp.ClientSession() as session:
        try:
            r = await session.get(f"{village_url}/select * from players where name = \'{target_user}\'")
        except aiohttp.client_exceptions.ContentTypeError:
            await cmd.reply("Ravenfall seems to be offline!")
            return
        player_info: Player = await r.json()
        if isinstance(player_info, dict) and player_info:
            char = Character(player_info)
        else:
            await cmd.reply("You are not currently playing.")
            return
    async with aiohttp.ClientSession() as session:
        r = await session.get(f"{village_url}/select * from ferry")
        ferry_info: Ferry = await r.json()
            
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
    if char.training in (Skills.Attack, Skills.Defense, Skills.Strength, Skills.Health):
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
    else:
        where_island = "sailing the seas"
        
    rested = ""
    if char.rested_time.total_seconds() > 0:
        s = utils.TimeSize.SMALL_SPACES 
        rested = f"with {utils.format_seconds(char.rested_time.total_seconds(),s)} of rest time"

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
    query = "sum(deriv(rf_player_stat_experience_total{player_name=\"%s\",session=\"%s\"}[2m]))" % (target_user, cmd.room.name)
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
    s = utils.TimeSize.SMALL_SPACES
    train_time_format = utils.format_timedelta(training_time_exp, s)
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
    out_msgs = utils.strjoin(" ✦ ", user_name, out_str)
    out_msgs = utils.strjoin('', out_msgs, f" | Training time is estimated")
    await cmd.reply(out_msgs)

async def run_cmd(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    stdout, stderr = await proc.communicate()

    print(f'[{cmd!r} exited with {proc.returncode}]')
    if stdout:
        print(f'[stdout]\n{stdout.decode()}')
    if stderr:
        print(f'[stderr]\n{stderr.decode()}')

async def ravenfall_restart_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    if cmd.room.room_id in village_events:
        await cmd.reply("There is an active event.")
        return
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            box = channel['sandboxie_box']
            start_script = channel['ravenfall_start_script']
            break
    else:
        await cmd.reply("Town not found :(")
        return
    shellcmd = (
        f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{box} /wait "
        f"taskkill /f /im Ravenfall.exe"
    )
    await run_cmd(shellcmd)
    shellcmd = (
        f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{box} /wait "
        f"cmd /c \"cd {os.getenv('RAVENFALL_FOLDER')} & {start_script}\""
    )
    await run_cmd(shellcmd)
    await cmd.reply("Okay")

async def ravenbot_restart_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            box = channel['sandboxie_box']
            break
    else:
        await cmd.reply("Town not found :(")
        return
    if cmd.room.name == "abrokecube":
        await cmd.reply("Shrug that doesnt work here")
        return
    shellcmd = (
        f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{box} /wait "
        f"taskkill /f /im RavenBot.exe"
    )
    await run_cmd(shellcmd)
    shellcmd = (
        f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{box} /wait "
        f"cmd /c \"cd {os.getenv('RAVENBOT_FOLDER')} & start RavenBot.exe\""
    )
    await run_cmd(shellcmd)
    await cmd.reply("Okay")

async def welcome_msg_cmd(cmd: ChatCommand):
    await first_time_joiner(cmd)

    
@routine(delta=timedelta(hours=6))
async def backup_state_data():
    for channel in channels:
        backup_file_with_date(
            f"{os.getenv('RAVENFALL_SANDBOXED_FOLDER').replace('{box}', channel['sandboxie_box']).rstrip('\\/')}\\state-data.json",
            int(os.getenv('BACKUP_RETENTION_COUNT'))
        )
    print("Backed up state data")
        
max_dungeon_hp: Dict[str, int] = {}
@routine(delta=timedelta(seconds=2))
async def update_events():
    async with aiohttp.ClientSession() as session:
        tasks = []
        tasks.extend([
            session.get(f"{x['rf_query_url']}/select * from dungeon") for x in channels
        ])
        tasks.extend([
            session.get(f"{x['rf_query_url']}/select * from raid") for x in channels
        ])
        r = await asyncio.gather(*tasks, return_exceptions=True)
        tasks = []
        for x in r:
            if isinstance(x, Exception):
                continue
            tasks.append(x.json())
        data = await asyncio.gather(*tasks)
    a = int(len(tasks) / 2)
    dungeons: List[Dungeon] = data[a*0:a*1]
    raids: List[Raid] = data[a*1:a*2]
    for dungeon, raid, channel in zip(dungeons, raids, channels):
        event_text = "No active event."
        if dungeon and dungeon.get('enemies'):
            if not dungeon['started']:
                max_dungeon_hp[channel['channel_id']] = dungeon["boss"]["health"]
                time_starting = format_seconds(dungeon['secondsuntilstart'])
                event_text = (
                    f"DUNGEON starting in {time_starting} – "
                    f"Boss HP: {dungeon['boss']['health']:,} – "
                    f"Enemies: {dungeon['enemies']:,} – "
                    f"Players: {dungeon['players']:,}"
                )
            else:
                if dungeon['enemiesalive'] > 0 or not channel['channel_id'] in max_dungeon_hp:
                    max_dungeon_hp[channel['channel_id']] = dungeon["boss"]["health"]
                boss_max_hp = max_dungeon_hp[channel['channel_id']]
                event_text = (
                    f"DUNGEON – "
                    f"Boss HP: {dungeon['boss']['health']:,}/{boss_max_hp:,} "
                    f"({dungeon['boss']['health']/boss_max_hp:.1%}) – "
                    f"Enemies: {dungeon['enemiesalive']:,}/{dungeon['enemies']:,} – "
                    f"Players: {dungeon['playersalive']:,}/{dungeon['players']:,} – "
                    f"Elapsed time: {format_seconds(dungeon['elapsed'])}"
                )
        elif raid and raid['started'] and raid['boss']['maxhealth'] > 0:
            event_text = (
                "RAID – "
                f"Boss HP: {raid['boss']['health']:,}/{raid['boss']['maxhealth']:,} "
                f"({raid['boss']['health']/raid['boss']['maxhealth']:.1%}) – "
                f"Players: {raid['players']:,} – "
                f"Time left: {format_seconds(raid['timeleft'])}"
            )
        village_events[channel['channel_id']] = event_text


current_mult: float = None
@routine(delta=timedelta(seconds=2))
async def update_mult(chat: Chat):
    global current_mult
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1)) as session:
        tasks = []
        tasks.extend([
            session.get(f"{x['rf_query_url']}/select * from multiplier") for x in channels
        ])
        r = await asyncio.gather(*tasks, return_exceptions=True)
        tasks = []
        for x in r:
            if isinstance(x, Exception):
                continue
            tasks.append(x.json())
        data: List[GameMultiplier] = await asyncio.gather(*tasks)
        multiplier: GameMultiplier = max(*data, key=lambda x: x['multiplier'])
    if current_mult is None:
        current_mult = multiplier['multiplier']
    if multiplier['multiplier'] > current_mult:
        current_mult = multiplier['multiplier']
        msg = f"{multiplier['eventname']} increased the multiplier to {int(current_mult)}x!"
        for channel in channels:
            await chat.send_message(channel['channel_name'], msg)
    current_mult = multiplier['multiplier']
    


@routine(delta=timedelta(hours=6), wait_first=True)
async def update_task(chat: Chat):
    async with aiohttp.ClientSession() as session:
        r = await asyncio.gather(*[
            session.get(f"{x['rf_query_url']}/select * from village") for x in channels
        ], return_exceptions=True)
        villages: List[Village] = await asyncio.gather(*[
            x.json() for x in r
        ])
    for idx, village in enumerate(villages):
        if not isinstance(village, dict):
            continue
        channel: Channel = channels[idx]
        if len(village['boost'].strip()) > 0:
            split = village['boost'].split()
            boost_stat = split[0]
            boost_value = float(split[1].rstrip("%"))
            asdf = f"{channel['ravenbot_prefix']}town {boost_stat.lower()}"
            await chat.send_message(channel['channel_name'], asdf)

async def event_gotify_msg(msg: gotify.Message, chat: Chat):
    split = msg['message'].split("::", 2)
    target = None
    text = None
    if len(split) == 2:
        target, text = split
    else:
        text = msg['message']
        
    print(f"Recieved gotify message: {msg}")
    if target is not None:
        targets = target.split(', ')
        for room in targets:
            if chat.is_in_room(room):
                await chat.send_message(room, text)
            else:
                print(f"Unknown room: {room}")
    else:
        for channel in channels:
            if channel['recieve_global_alerts']:
                await chat.send_message(channel['channel_name'], text)

async def gotify_listener(chat: Chat):
    while True:
        await asyncio.sleep(1)
        try:
            gotify = AsyncGotify(
                base_url=os.getenv("GOTIFY_URL"),
                client_token=os.getenv("GOTIFY_CLIENT_TOKEN")
            )
            print("Connected to Gotify")
            async for msg in gotify.stream():
                if msg['appid'] == int(os.getenv("GOTIFY_APP_ID")):
                    await event_gotify_msg(msg, chat)
        except Exception as e:
            print(f"Gotify listener failed: {e}, retrying...")

# this is where we set up the bot
async def run():
    # set up twitch api instance and add user authentication with some scopes
    twitch = await Twitch(os.getenv("TWITCH_CLIENT"), os.getenv("TWITCH_SECRET"))
    helper = UserAuthenticationStorageHelper(twitch, USER_SCOPE)
    rf = ravenpy.RavenNest(os.getenv("API_USER"), os.getenv("API_PASS"))
    asyncio.create_task(rf.login())
    await helper.bind()

    
    # create chat instance
    chat = await Chat(twitch, initial_channel=[x['channel_name'] for x in channels])
    # for channel in channels:
    #     chat.set_channel_prefix(channel['ravenbot_prefix'], channel['channel_name'])

    # register the handlers for the events you want

    chat.register_event(ChatEvent.READY, on_ready)
    chat.register_event(ChatEvent.MESSAGE, on_message)

    chat.register_command('hi', test_cmd)
    chat.register_command('towns', towns_cmd)
    chat.register_command('update', update_cmd)
    chat.register_command('event', event_cmd)
    chat.register_command('uptime', uptime_cmd)
    chat.register_command('system', system_cmd)
    chat.register_command('welcomemsg', welcome_msg_cmd)
    chat.register_command('exprate', exprate_cmd)
    chat.register_command('expirate', exprate_cmd)
    chat.register_command('char', chracter_cmd)
    chat.register_command('character', chracter_cmd)
    chat.register_command('restartrf', ravenfall_restart_cmd)
    chat.register_command('restartrfbot', ravenbot_restart_cmd)
    chat.register_command('rfrestart', ravenfall_restart_cmd)
    chat.register_command('rfbotrestart', ravenbot_restart_cmd)

    asyncio.create_task(gotify_listener(chat))
    chat.start()

    try:
        while True:
            await asyncio.sleep(9999)
    finally:
        chat.stop()
        await twitch.close()

# lets run our setup
asyncio.run(run())