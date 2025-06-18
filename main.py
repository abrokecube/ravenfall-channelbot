import time
from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticationStorageHelper
from twitchAPI.type import AuthScope, ChatEvent
from twitchAPI.chat import Chat, EventData, ChatMessage, ChatCommand
from twitchAPI.chat.middleware import *
import asyncio
import subprocess
import os
from dotenv import load_dotenv
import json
import aiohttp
import aiohttp.client_exceptions
from typing import List, Tuple, Callable, Any, TypedDict
from datetime import datetime, timedelta
import psutil
import shutil
import traceback
import logging

from gotify import AsyncGotify
from gotify import gotify

from models import *
from utils.routines import routine
from utils.format_time import *
from utils import strutils
from utils import chatmsg_cd
from utils import langstuff
from utils.is_twitch_username import is_twitch_username
from utils.filter_username import filter_username
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
    # Set default command prefix if not specified
    if 'command_prefix' not in channel:
        channel['command_prefix'] = '!'

class MessageWaiter:
    def __init__(self):
        self.waiting_messages: Dict[str, List[Tuple[Callable[[ChatMessage], bool], asyncio.Future]]] = {}
    
    async def wait_for_message(self, channel_name: str, check: Callable[[ChatMessage], bool], timeout: float = 10.0):
        """Wait for a message that matches the check function in the specified channel.
        
        Args:
            channel_name: Name of the channel to wait in
            check: A function that takes a message and returns True if it matches
            timeout: Maximum time to wait in seconds
            
        Returns:
            The matching message if found, or None if timeout
        """
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        
        if channel_name not in self.waiting_messages:
            self.waiting_messages[channel_name] = []
        self.waiting_messages[channel_name].append((check, future))
        
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            # Remove our future if it's still in the list
            if channel_name in self.waiting_messages:
                for i, (c, f) in enumerate(self.waiting_messages[channel_name]):
                    if f == future:
                        self.waiting_messages[channel_name].pop(i)
                        break
            return None
    
    async def process_message(self, message: ChatMessage):
        """Process an incoming message and complete any matching futures"""
        channel_name = message.room.name
        if channel_name not in self.waiting_messages:
            return
            
        remaining = []
        for check, future in self.waiting_messages[channel_name]:
            if not future.done():
                try:
                    if check(message):
                        future.set_result(message)
                    else:
                        remaining.append((check, future))
                except Exception as e:
                    future.set_exception(e)
            
        self.waiting_messages[channel_name] = remaining

# Global message waiter instance
message_waiter = MessageWaiter()

# Track pending commands and their response status
pending_monitors: Dict[str, Dict[str, Any]] = {}

# Track restart attempts and locks for each channel
restart_attempts: Dict[str, Dict[str, Any]] = {}  # channel_id -> {count: int, last_attempt: float, is_restarting: bool}
# Track Ravenfall restarts to prevent concurrent restarts
ravenfall_restart_futures: Dict[str, asyncio.Future] = {}  # channel_id -> Future

# Track last command time per channel to prevent command spam
last_command_time: Dict[str, float] = {}  # channel_id -> timestamp

# Global flag to pause/resume RavenBot response monitoring
monitoring_paused = False

# Common commands that should trigger a response from RavenBot (without prefix)
MONITORED_COMMANDS = {
    'coins', 'count', 'damage', 'dmg', 'dps', 'eat', 'effects', 'ferry', 'items',
    'multiplier', 'online', 'pubsub', 'res', 'resources', 'rested', 'status', 'stats', 'town',
    'townres', 'training', 'value', 'version', 'village', 'villagers', 'where'
}
# Commands that may take longer to respond to
MONITORED_COMMANDS_LONG = {
    'consume', 'disenchant', 'drink', 'eat', 'enchant',
    'gift', 'join', 'leave', 'scrolls',
}

MAX_RETRIES = 3  # Maximum number of restart attempts before giving up (on the final attempt, restarts Ravenfall)
RETRY_WINDOW = 3*60  # Number of seconds to wait before resetting attempt counter

async def monitor_ravenbot_response(
    chat: Chat, channel_id: str, command: str, timeout: float = 5, resend_text: str = None
):
    """
    Monitor for RavenBot's response to a command.
    If no response, restart RavenBot up to MAX_RETRIES times.
    If still unresponsive after MAX_RETRIES, restart Ravenfall.
    """
    channel = get_channel_data(channel_id)
    if not channel:
        return
    if monitoring_paused:
        return
        
    if pending_monitors.get(channel_id):
        print(f"Already monitoring {channel_id}")
        return

    pending_monitors[channel_id] = {
        'channel': channel_id,
        'task': asyncio.current_task()
    }
    asked_to_retry = False
    try:
        while True:
            if 'DUNGEON is being prepared' in village_events.get(channel_id, ''):
                await asyncio.sleep(5)
            else:
                break
        # Wait for a response from RavenBot
        response = await message_waiter.wait_for_message(
            channel_name=channel['channel_name'],
            check=lambda m: (
                m.user.id == os.getenv("RAVENBOT_USER_ID")
            ),
            timeout=timeout
        )

        if monitoring_paused:
            return
        
        # Initialize or clean up old restart attempts
        if channel_id not in restart_attempts:
            restart_attempts[channel_id] = {'count': 0, 'last_attempt': 0}

        if not response:
            current_time = time.time()
            time_since_last_attempt = current_time - restart_attempts[channel_id]['last_attempt']
            if time_since_last_attempt > RETRY_WINDOW:
                restart_attempts[channel_id]['count'] = 0
            
            restart_attempts[channel_id]['count'] += 1
            
            attempt = restart_attempts[channel_id]['count']
            channel_name = channel['channel_name']

            resp_retry = "Hmm , let me restart RavenBot..."
            resp_retry_2 = "Hmm , let me restart RavenBot again.."
            resp_user_retry = "Okay , try again"
            resp_user_retry_2 = "Okay , try again, surely this time it will work"
            resp_restart_ravenfall = "okie then i will restart Ravenfall, please hold..."
            resp_giveup = "I give up, please try again later (pinging @abrokecube)"

            if resend_text:
                resp_retry = "Hmm"
                resp_retry_2 = "Hmm ..."
                resp_user_retry = resend_text
                resp_user_retry_2 = resend_text
                resp_restart_ravenfall = "Hmm ........."
                resp_giveup = "I give up (pinging @abrokecube)"
            
            if attempt <= MAX_RETRIES:
                print(f"No response to {command} in {channel_name} (Attempt {attempt}/{MAX_RETRIES}), restarting RavenBot...")
                restart_attempts[channel_id]['last_attempt'] = current_time                
                
                # Only send message if this is the first attempt or we've waited long enough
                if attempt == 1:
                    await chat.send_message(channel_name, resp_retry)
                elif attempt > 1 and attempt < MAX_RETRIES:
                    await chat.send_message(channel_name, resp_retry_2)
                
                if attempt < MAX_RETRIES:   
                    await restart_ravenbot(channel)
                    await asyncio.sleep(3)
                    await chat.send_message(channel_name, resp_user_retry)
                    asked_to_retry = True
                elif attempt == MAX_RETRIES:
                    await chat.send_message(channel_name, resp_restart_ravenfall)
                    restart_task = add_restart_task(channel, chat, 20, mute_countdown=True, label="Missing response")
                    await restart_task.wait()
                    await chat.send_message(channel_name, resp_user_retry_2)
                    asked_to_retry = True
                restart_attempts[channel_id]['last_attempt'] = current_time                
            else:
                await chat.send_message(channel_name, resp_giveup)
        else:
            restart_attempts[channel_id]['count'] = 0
    except Exception as e:
        print(f"Error in monitor_ravenbot_response: {e}")
    finally:
        if channel_id in pending_monitors:
            del pending_monitors[channel_id]
    if resend_text and asked_to_retry:
        asyncio.create_task(monitor_ravenbot_response(chat, channel_id, command, resend_text=resend_text))

async def on_message(msg: ChatMessage):
    # Let the message waiter process the message first
    await message_waiter.process_message(msg)
    
    # Get channel data and check if this is a monitored command
    ch_data = get_channel_data(msg.room.room_id)
    if ch_data is None:
        return
        
    content = msg.text.strip()
    prefix = ch_data.get('ravenbot_prefix', '!')
    
    if content.startswith(prefix):
        event_text = village_events.get(msg.room.room_id, '')
        parts = content[len(prefix):].strip().split(maxsplit=1)
        if parts:  # If there's at least a command
            command = parts[0].lower()
        is_monitored_command = (command in MONITORED_COMMANDS) or (command in MONITORED_COMMANDS_LONG)
        if 'DUNGEON is being prepared' in event_text and is_monitored_command:
            await asyncio.sleep(0.5)
            await msg.reply("please wait, the game is currently frozen because it is busy preparing a dungeon...")
        else:
            resend_text = None
            if msg.user.id == os.getenv("BOT_ID"):
                resend_text = msg.text
            if command in MONITORED_COMMANDS:
                asyncio.create_task(monitor_ravenbot_response(msg.chat, msg.room.room_id, command, resend_text=resend_text))
            elif command in MONITORED_COMMANDS_LONG:
                asyncio.create_task(monitor_ravenbot_response(msg.chat, msg.room.room_id, command, timeout=15, resend_text=resend_text))
    
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
    villages: List[Village] = await asyncio.gather(*[get_ravenfall_query(x['rf_query_url'], 'select * from village') for x in channels])
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

async def runshell(cmd) -> str | None:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    stdout, stderr = await proc.communicate()
    out_text = None
    print(f'[{cmd!r} exited with {proc.returncode}]')
    if stdout:
        stdout_text = stdout.decode()
        print(f'[stdout]\n{stdout_text}')
        out_text = stdout_text
    if stderr:
        print(f'[stderr]\n{stderr.decode()}')
    return out_text

def runshell_detached(cmd):
    DETACHED_PROCESS = 0x00000008
    subprocess.Popen(
        cmd,
        shell=True,
        creationflags=DETACHED_PROCESS
    )

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
    message = f"{this_channel['ravenbot_prefix']}town {town_boost[0].skill.lower()}"
    await cmd.send(message)
    asyncio.create_task(monitor_ravenbot_response(cmd.chat, channel['channel_id'], 'town', resend_text=message))
    
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
    negative = ''
    if size_bytes < 0:
        size_bytes = -size_bytes
        negative = '-'
    if size_bytes == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB']
    power = 1024
    unit_index = 0

    while size_bytes >= power and unit_index < len(units) - 1:
        size_bytes /= power
        unit_index += 1

    return f"{negative}{size_bytes:.2f} {units[unit_index]}"
    
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

class PrometheusMetric(TypedDict):
    __name__: str
    job: str
    instance: str

class PromethusInstantResult(TypedDict):
    metric: PrometheusMetric
    value: List[float | str]
    
async def get_prometheus_instant(query: str) -> List[PromethusInstantResult] | None:
    async with aiohttp.ClientSession() as session:
        r = await session.get(
            f"{PROMETHEUS_URL}/api/v1/query?query={query}"
        )
        result = await r.json()
    data = result['data']['result']
    return data

async def get_ravenfall_query(url: str, query: str, timeout: int = 5) -> Any | None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        try:
            r = await session.get(f"{url}/{query}")
        except Exception as e:
            print(f"Error fetching Ravenfall query from {url}: {e}")
            return None
        data = await r.json()
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
    
async def ravenfall_ram_cmd(cmd: ChatCommand):
    processes: Dict[str, List[float]] = {}
    working_set = await get_prometheus_instant("windows_process_working_set_bytes{process='Ravenfall'}")
    change_over_time = await get_prometheus_instant("deriv(windows_process_working_set_bytes{process='Ravenfall'}[3m])")
    working_set_series = await get_prometheus_series("windows_process_working_set_bytes{process='Ravenfall'}", 60*10)
    tasks = []
    for ch in channels:
        shellcmd = (
            f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{ch['sandboxie_box']} /listpids"
        )
        tasks.append(runshell(shellcmd))
    responses: List[str | None] = await asyncio.gather(*tasks)
    pid_lists = [x.splitlines() for x in responses]
    box_pids = {}
    for i in range(len(channels)):
        box_pids[channels[i]['channel_name']] = pid_lists[i]
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
    if "all" in cmd.parameter.lower():
        for name, (bytes_used, change, series) in processes_named.items():
            s = "+"
            if change < 0:
                s = ''
            out_str.append(
                f"{name} - {bytes_to_human_readable(bytes_used)} ({s}{bytes_to_human_readable(change)}/s)"
            )
        await cmd.reply(f"Ravenfall ram usage: {' • '.join(out_str)} | Showing change over 3 minutes")
    else:
        bytes_used, change, series = processes_named[cmd.room.name]
        graph = braille.simple_line_graph(
            series, max_gap=30, width=26, fill_type=1, hard_min_val=1
        )
        await cmd.reply(
            f"[{graph}] Ravenfall is using {bytes_to_human_readable(bytes_used)} of memory; "
            f"changed by {bytes_to_human_readable(change)}/s over 3 mins. (Graph: 10 minutes)"
        )
    

async def exprate_cmd(cmd: ChatCommand):
    args = cmd.parameter.split()
    target_user = cmd.user.name
    if len(cmd.parameter) > 2 and len(args) > 0:
        target_user = filter_username(args[0])
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
        target_user = filter_username(args[0])
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
    else:
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
    await cmd.reply(out_msgs)

async def multiplier_cmd(cmd: ChatCommand):
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            village_url = channel['rf_query_url']
            break
    else:
        await cmd.reply("Town not found :(")
        return
    async with aiohttp.ClientSession() as session:
        try:
            r = await session.get(f"{village_url}/select * from multiplier")
        except aiohttp.client_exceptions.ContentTypeError:
            await cmd.reply("Ravenfall seems to be offline!")
            return
        mult_info: GameMultiplier = await r.json()
    mult = int(mult_info['multiplier'])
    if mult <= 1:
        await cmd.reply(
            f"Current global exp multiplier is {mult}×."
        )
    else:
        await cmd.reply(
            f"Current global exp multiplier is {mult}×, "
            f"ending in {format_seconds(mult_info['timeleft'], TimeSize.LONG)}, "
            f"thanks to {mult_info['eventname']}!"
        )

async def restart_process(box_name, process_name, startup_command: str):
    shellcmd = (
        f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{box_name} /wait "
        f"taskkill /f /im {process_name}"
    )
    await runshell(shellcmd)
    shellcmd = (
        f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{box_name} /wait "
        f"cmd /c \"{startup_command.replace("\"", "\\\"")}\""
    )
    await runshell(shellcmd)

async def restart_ravenfall(
    channel: Channel, chat: Chat, dont_send_message: bool = False,
) -> bool:
    """
    Restart Ravenfall and wait for confirmation.
    
    Args:
        channel: The channel to restart Ravenfall for
        chat: Chat instance to send status messages
        dont_send_message: If True, suppresses status messages
        
    Returns:
        bool: True if restart was successful, False otherwise
    """
    channel_id = channel['channel_id']
    channel_name = channel['channel_name']
    
    future = None
    if channel_id in ravenfall_restart_futures:
        future = ravenfall_restart_futures[channel_id]
        if not future.done():
            if not dont_send_message:
                await chat.send_message(channel_name, "A restart is already in progress.")
            return await future
    
    for other_future in ravenfall_restart_futures.values():
        if not other_future.done():
            await chat.send_message(channel_name, "Waiting for other restarts to finish...")
            break
    
    while True:
        unfinished = False
        for other_future in ravenfall_restart_futures.values():
            if not other_future.done():
                unfinished = True
                break
        if not unfinished:
            break
        await asyncio.sleep(3)
    if not future or future.done():
        future = asyncio.get_event_loop().create_future()
        ravenfall_restart_futures[channel_id] = future

    if not dont_send_message:
        await chat.send_message(channel_name, "Restarting Ravenfall...")
        
    await restart_process(
        channel['sandboxie_box'], 
        "Ravenfall.exe", 
        f"cd {os.getenv('RAVENFALL_FOLDER')} & {channel['ravenfall_start_script']}"
    )
    
    # # Wait for RavenBot to respond after restart
    # response = await message_waiter.wait_for_message(
    #     channel_name=channel_name,
    #     check=lambda m: m.user.id == os.getenv("RAVENBOT_USER_ID"),
    #     timeout=60.0
    # )
    
    start_time = time.time()
    auth_timeout = 120
    authenticated = False
    
    while time.time() - start_time < auth_timeout:
        try:
            session: GameSession = await get_ravenfall_query(channel['rf_query_url'], "select * from session", 1)
            if session and session.get('authenticated', False):
                authenticated = True
                break
        except Exception as e:
            print(f"Error checking authentication status: {e}")
        await asyncio.sleep(1)
    
    if not authenticated:
        error_msg = "Timed out waiting for Ravenfall to start. dinkDonk @abrokecube"
        print(error_msg)
        if not dont_send_message:
            await chat.send_message(channel_name, error_msg)
        else:
            await chat.send_message(channel_name, "@abrokecube dinkDonk")
        future.set_result(False)
        return False
        
    print("Restart successful")
    async def post_restart():
        await chat.send_message(channel_name, "?undorandleave")
        player_count = 0
        while True:
            await asyncio.sleep(2)
            session: GameSession = await get_ravenfall_query(channel['rf_query_url'], "select * from session", 1)
            new_player_count = session['players']
            if player_count > 0 and new_player_count == player_count:
                break
            player_count = new_player_count
        await chat.send_message(channel_name, "?sailall")
    while True:
        await asyncio.sleep(1)
        session: GameSession = await get_ravenfall_query(channel['rf_query_url'], "select * from session", 1)
        if session['players'] > 0:
            break
    asyncio.create_task(post_restart())
    future.set_result(True)
    return True
    
    # if not dont_send_message:
    #     await chat.send_message(channel_name, "Timed out waiting for Ravenfall to respond... maybe RavenBot is down?")
    # future.set_result(False)
    # return False
        

async def ravenfall_restart_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    if cmd.room.room_id in village_events:
        if village_events[cmd.room.room_id].split(maxsplit=1)[0] != 'No':
            await cmd.reply("There is an active event.")
            return
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            thechannel = channel
            break
    else:
        await cmd.reply("Town not found :(")
        return
    add_restart_task(thechannel, cmd.chat, time_to_restart=20, label="User restart")
    await cmd.reply(f"Restart queued. Restarting in 20s.")

async def ravenfall_queue_restart_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    args = cmd.parameter.split()
    seconds = 5*60
    if len(args) > 0 and args[0].isdigit():
        seconds = int(args[0])
    this_channel = None
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            this_channel = channel
            break
    else:
        await cmd.reply("Town not found :(")
        return

    add_restart_task(this_channel, cmd.chat, time_to_restart=seconds, label="User restart")
    await cmd.reply(f"Restart queued. Restarting in {seconds}s.")

async def ravenfall_queue_restart_cancel_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    task = get_restart_task(cmd.room.room_id)
    if (not task) or task.finished():
        await cmd.reply("No task to cancel")
        return
    task.cancel()
    await cmd.reply("Cancelled restart task.")

async def restart_ravenbot(channel: Channel, chat: Chat | None = None):
    """Restart RavenBot for a specific channel. If chat is None, it will not wait for confirmation."""
    if channel['channel_name'] != "abrokecube":
        await restart_process(
            channel['sandboxie_box'], "RavenBot.exe", f"cd {os.getenv('RAVENBOT_FOLDER')} & start RavenBot.exe"
        )
    else:
        ravenbot_path = os.getenv('CUSTOM_RAVENBOT_PATH').rstrip('/\\')
        with open(f'{ravenbot_path}/pid', "r") as f:
            pid = f.read()
        await runshell(
            f"taskkill /f /pid {pid}"
        )
        runshell_detached(
            f"start /d \"{ravenbot_path}\" {os.getenv('CUSTOM_RAVENBOT_START_CMD')}"
        )
        await asyncio.sleep(3) 
    if chat:
        await asyncio.sleep(5)
        for i in range(3):
            await chat.send_message(channel['channel_name'], "!version")
            result = await message_waiter.wait_for_message(
                channel_name=channel['channel_name'],
                check=lambda m: (
                    m.user.id == os.getenv("RAVENBOT_USER_ID")
                ),
                timeout=5
            )
            if result:
                await chat.send_message(channel['channel_name'], "RavenBot has been restarted.")
                return True
        await chat.send_message(channel['channel_name'], "RavenBot failed to restart.")
        return False
    return True

async def ravenbot_restart_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    thischannel = None
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            thischannel = channel
            break
    else:
        await cmd.reply("Town not found :(")
        return
    await restart_ravenbot(thischannel)
    await cmd.reply("Okay")

async def postpone_restart_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    args = cmd.parameter.split()
    seconds = 3*60
    if len(args) > 0 and args[0].isdigit():
        seconds = int(args[0])
    task = postpone_restart_task(cmd.room.room_id, seconds)
    if task is None:
        return
    await cmd.reply(f"Restart postponed by {seconds} seconds.")

async def get_restart_time_left_cmd(cmd: ChatCommand):
    task = get_restart_task(cmd.room.room_id)
    if task is None:
        await cmd.reply("No restart task found.")
        return
    time_left = task.get_time_left()
    if time_left <= 0:
        await cmd.reply("A restart is currently in progress.")
        return
    if task.finished():
        await cmd.reply("No restart task found.")
        return
    task_label = task.label
    out_text = f"Time left until restart: {format_seconds(time_left, TimeSize.LONG, 2, False)}."
    if task_label:
        out_text += f" Reason: {task_label}."
    if task.future_pause_reason and not task.paused():
        out_text += f" Will pause for {task.future_pause_reason}."
    if task.paused():
        if task.pause_event_name:
            out_text += f" (paused: {task.pause_event_name})"
        else:
            out_text += f" (paused)"
    await cmd.reply(out_text)

async def welcome_msg_cmd(cmd: ChatCommand):
    await first_time_joiner(cmd)

async def toggle_auto_restart_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    channel = get_channel_data(cmd.room.room_id)
    if channel is None:
        await cmd.reply("Channel not found.")
        return
    if channel['restart_period'] == 0:
        await cmd.reply("Auto restart period is not configured.")
        return
    old_value = channel['auto_restart']
    channel['auto_restart'] = not channel['auto_restart']
    await cmd.reply(f"Auto restart is now {'enabled' if channel['auto_restart'] else 'disabled'}.")
    if old_value == True:
        restart_task = get_restart_task(cmd.room.room_id)
        if restart_task and restart_task.label == "Scheduled restart":
            restart_task.cancel()

# Command handlers for pausing/resuming monitoring
async def toggle_bot_monitor_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    global monitoring_paused
    monitoring_paused = not monitoring_paused   
    await cmd.reply("RavenBot monitoring is now " + ("PAUSED." if monitoring_paused else "RESUMED."))

async def ping_cmd(cmd: ChatCommand):
    await cmd.reply("Pong!")

async def backup_state_data_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    await backup_state_data_routine(cmd.chat)
    await cmd.reply("Okay")

WARNING_MSG_TIMES = (
    (120, "warning"), 
    (30, "warning"),
    (20, "randleave")
    )
class RestartTask:
    def __init__(self, channel: Channel, chat: Chat, time_to_restart: int | None = 0, mute_countdown: bool = False, label: str = ""):
        self.channel = channel
        self.chat = chat
        self.time_to_restart = time_to_restart
        if self.time_to_restart is None:
            self.time_to_restart = WARNING_MSG_TIMES[0][0]
        self.start_t = 0
        self.waiting_task: asyncio.Task = None
        self.event_watch_task: asyncio.Task = None
        self.done = False
        self._paused = False
        self._pause_time = 0
        self._pause_start = 0
        self.pause_event_name = ""
        self.future_pause_reason = ""
        self.mute_countdown = mute_countdown
        self.label = label

    def start(self):
        if not self.done:
            if self.waiting_task and not self.waiting_task.done():
                self.waiting_task.cancel()
            if self.event_watch_task and not self.event_watch_task.done():
                self.event_watch_task.cancel()
        self.start_t = time.time()
        self.waiting_task = asyncio.create_task(self._waiting())
        self.event_watch_task = asyncio.create_task(self._event_watcher())
        
    def cancel(self):
        self.waiting_task.cancel()
        self.event_watch_task.cancel()
        self.done = True

    async def wait(self):
        """Wait until the restart task is finished."""
        if self.waiting_task:
            try:
                await self.waiting_task
            except asyncio.CancelledError:
                pass

    async def _waiting(self):
        warning_idx = -1
        while True:
            await asyncio.sleep(1)
            if self._paused:
                continue
            time_left = self.get_time_left()
            if time_left <= 0:
                break
            new_warning_idx = -1
            for i, (x, _) in enumerate(WARNING_MSG_TIMES):
                if time_left < x:
                    new_warning_idx = i
            if new_warning_idx != warning_idx:
                if new_warning_idx >= 0 and new_warning_idx > warning_idx:
                    for i in range(warning_idx + 1, new_warning_idx + 1):
                        if WARNING_MSG_TIMES[i][1] == "randleave":
                            await self.chat.send_message(self.channel['channel_name'], "?randleave")
                    if WARNING_MSG_TIMES[new_warning_idx][1] == "warning" and time_left > 7 and not self.mute_countdown:
                        await self.chat.send_message(
                            self.channel['channel_name'],
                            f"Restarting Ravenfall in {format_seconds(time_left, TimeSize.LONG, 2, False)}!"
                        )
                warning_idx = new_warning_idx
        await self._execute()

    async def _event_watcher(self):
        event_type = ""
        messages = {
            "server_down": "Restart postponed due to server down.",
            "dungeon": "Restart postponed due to dungeon.",
            "raid": "Restart postponed due to raid.",
        }
        names = {
            "server_down": "server down",
            "dungeon": "dungeon",
            "raid": "raid",
        }
        while True:
            old_event_type = event_type
            event_type = ""
            await asyncio.sleep(2)
            if self.done:
                return
            time_left = self.get_time_left()
            ch_id = self.channel['channel_id']
            if ch_id in village_events:
                firstword = village_events[ch_id].split(maxsplit=1)[0]
                if firstword == 'DUNGEON':
                    event_type = "dungeon"
                elif firstword == 'RAID':
                    event_type = "raid"
            rf_server_status = await get_prometheus_instant("monitor_status{monitor_name='Ravenfall API'}")
            if not rf_server_status:
                event_type = "server_down"
            else:
                if rf_server_status[0]['value'][1] != "1":
                    event_type = "server_down"
            
            if event_type:
                self.future_pause_reason = names[event_type]
            else:
                self.future_pause_reason = ""

            if time_left > max(WARNING_MSG_TIMES[-1][0], 60):
                continue

            if not event_type:
                if self._paused:
                    self.unpause()
                    time_left = self.get_time_left()
                    if time_left < 60:
                        self.time_to_restart += 60 - time_left
                        time_left = self.get_time_left()
                    await self.chat.send_message(
                        self.channel['channel_name'], 
                        f"Resuming restart. Restarting in {format_seconds(time_left, TimeSize.LONG, 2, False)}."
                    )
            else:
                if (not self._paused) or old_event_type != event_type:
                    self.pause(names[event_type])
                    await self.chat.send_message(
                        self.channel['channel_name'], 
                        messages[event_type]
                    )
    
    async def _execute(self):
        self.event_watch_task.cancel()
        await restart_ravenfall(self.channel, self.chat)
        self.done = True

    def finished(self):
        return self.done

    def paused(self):
        return self._paused
    
    def get_time_left(self):
        pause_time = self._pause_time
        if self._paused:
            pause_time += time.time() - self._pause_start
        return self.time_to_restart - (time.time() - self.start_t - pause_time)
    
    def pause(self, event_name: str = ""):
        if not self._paused:
            self._paused = True
            self._pause_start = time.time()
            self.pause_event_name = event_name

    def unpause(self):
        if self._paused:
            self._paused = False
            self._pause_time += time.time() - self._pause_start
            self.pause_event_name = ""

    def postpone(self, seconds: int):
        # if not self._paused:
        #     self.pause()
        self.time_to_restart += seconds
        # self.start_t = time.time() - self._pause_time


channel_restart_tasks: Dict[str, RestartTask] = {}
def add_restart_task(channel: Channel, chat: Chat, time_to_restart: int | None = None, mute_countdown: bool = False, label: str = ""):
    if channel['channel_id'] in channel_restart_tasks:
        task = channel_restart_tasks[channel['channel_id']]
        if not task.finished():
            task.cancel()
    task = RestartTask(channel, chat, time_to_restart, mute_countdown, label)
    channel_restart_tasks[channel['channel_id']] = task
    task.start()
    return task

def postpone_restart_task(channel_id: str, seconds: int):
    if channel_id in channel_restart_tasks:
        task = channel_restart_tasks[channel_id]
        if not task.finished():
            task.postpone(seconds)
            return task
    return None

def get_restart_task(channel_id: str) -> RestartTask | None:
    return channel_restart_tasks.get(channel_id, None)

@routine(delta=timedelta(hours=5), wait_first=True)
async def backup_state_data_routine(chat: Chat):
    for future in ravenfall_restart_futures.values():
        if not future.done():
            print("Waiting for ongoing restarts to finish before backing up state data...")
            await future  # Wait for any ongoing restarts to finish
            await asyncio.sleep(3)

    async def backup_task(channel: Channel):
        await chat.send_message(channel['channel_name'], "?resync")
        await asyncio.sleep(15)
        backup_file_with_date(
            f"{os.getenv('RAVENFALL_SANDBOXED_FOLDER').replace('{box}', channel['sandboxie_box']).rstrip('\\/')}\\state-data.json",
            int(os.getenv('BACKUP_RETENTION_COUNT'))
        )
    print("Backing up state data...")
    await asyncio.gather(*[backup_task(channel) for channel in channels])
    print("Backed up state data")
        
max_dungeon_hp: Dict[str, int] = {}
@routine(delta=timedelta(seconds=2), wait_remainder=True)
async def update_events_routine(chat: Chat):
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
        old_event_text = village_events.get(channel['channel_id'], "null null null")
        event_text = "No active event."
        if dungeon and dungeon.get('enemies'):
            if not dungeon['started']:
                max_dungeon_hp[channel['channel_id']] = dungeon["boss"]["health"]
                time_starting = format_seconds(dungeon['secondsuntilstart'])
                if dungeon['boss']['health'] > 0:
                    event_text = (
                        f"DUNGEON starting in {time_starting} – "
                        f"Boss HP: {dungeon['boss']['health']:,} – "
                        f"Enemies: {dungeon['enemies']:,} – "
                        f"Players: {dungeon['players']:,}"
                    )
                else:
                    event_text = (
                        f"DUNGEON is being prepared... – "
                        f"Enemies: {dungeon['enemies']:,}"
                    )
            else:
                if dungeon['enemiesalive'] > 0 or not channel['channel_id'] in max_dungeon_hp:
                    max_dungeon_hp[channel['channel_id']] = dungeon["boss"]["health"]
                boss_max_hp = max_dungeon_hp[channel['channel_id']]
                if old_event_text.split()[1] == "starting":
                    msg = (
                        f"DUNGEON – "
                        f"Boss HP: {boss_max_hp:,} – "
                        f"Enemies: {dungeon['enemies']:,}"
                    )
                    if channel['event_notifications']:
                        await chat.send_message(channel['channel_name'], msg)
                event_text = (
                    f"DUNGEON – "
                    f"Boss HP: {dungeon['boss']['health']:,}/{boss_max_hp:,} "
                    f"({dungeon['boss']['health']/boss_max_hp:.1%}) – "
                    f"Enemies: {dungeon['enemiesalive']:,}/{dungeon['enemies']:,} – "
                    f"Players: {dungeon['playersalive']:,}/{dungeon['players']:,} – "
                    f"Elapsed time: {format_seconds(dungeon['elapsed'])}"
                )
        elif raid and raid['started'] and raid['boss']['maxhealth'] > 0:
            if old_event_text.split()[0] != "RAID":
                msg = (
                    f"RAID – "
                    f"Boss HP: {raid['boss']['health']:,} "
                )
                if channel['event_notifications']:
                    await chat.send_message(channel['channel_name'], msg)
            event_text = (
                "RAID – "
                f"Boss HP: {raid['boss']['health']:,}/{raid['boss']['maxhealth']:,} "
                f"({raid['boss']['health']/raid['boss']['maxhealth']:.1%}) – "
                f"Players: {raid['players']:,} – "
                f"Time left: {format_seconds(raid['timeleft'])}"
            )
        village_events[channel['channel_id']] = event_text

current_mult: float = None
@routine(delta=timedelta(seconds=3))
async def update_mult_routine(chat: Chat):
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
        msg = f"{multiplier['eventname']} increased the multiplier to {int(current_mult)}x, ending in {format_seconds(multiplier['timeleft'], TimeSize.MEDIUM_SPACES)}!"
        for channel in channels:
            await chat.send_message(channel['channel_name'], msg)
    current_mult = multiplier['multiplier']
    


@routine(delta=timedelta(hours=3), wait_first=True)
async def update_boosts_routine(chat: Chat):
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
            asyncio.create_task(monitor_ravenbot_response(chat, channel['channel_id'], 'town', resend_text=asdf))
                

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

@routine(delta=timedelta(seconds=30))
async def auto_restart_routine(chat: Chat):
    for channel in channels:
        if not channel['auto_restart']:
            continue
        period = channel.get('restart_period', 0)
        if period and period > 0:
            period = max(20*60,period)
            task = get_restart_task(channel['channel_id'])

            if task and not task.finished():
                continue

            uptime = None
            for i in range(5):
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                    try:
                        r = await session.get(f"{channel['rf_query_url']}/select * from session")
                        game_session: GameSession = await r.json()
                        uptime = game_session['secondssincestart']
                        break
                    except Exception as e:
                        print(f"Uptime check failed: {e}")
                        await asyncio.sleep(10)

            if uptime is None:
                print(f"Uptime check failed for {channel['channel_name']}, restarting Ravenfall")
                add_restart_task(channel, chat, 10, label="Auto restart (could not get uptime)")
                continue

            seconds_to_restart = max(60, period - uptime)
            add_restart_task(channel, chat, seconds_to_restart, label="Scheduled restart")


async def on_ready(ready_event: EventData):
    update_boosts_routine.start(ready_event.chat)
    update_events_routine.start(ready_event.chat)
    update_mult_routine.start(ready_event.chat)
    backup_state_data_routine.start(ready_event.chat)
    auto_restart_routine.start(ready_event.chat)
    print('Bot is ready for work')

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

    # Register event handlers
    chat.register_event(ChatEvent.READY, on_ready)
    chat.register_event(ChatEvent.MESSAGE, on_message)
    chat.register_command('hi', test_cmd)
    chat.register_command('ping', ping_cmd)
    chat.register_command('towns', towns_cmd)
    chat.register_command('update', update_cmd)
    chat.register_command('event', event_cmd)
    chat.register_command('mult', multiplier_cmd)
    chat.register_command('uptime', uptime_cmd)
    chat.register_command('system', system_cmd)
    chat.register_command('rfram', ravenfall_ram_cmd)
    chat.register_command('welcomemsg', welcome_msg_cmd)
    chat.register_command('exprate', exprate_cmd)
    chat.register_command('expirate', exprate_cmd)
    chat.register_command('char', chracter_cmd)
    chat.register_command('show', chracter_cmd)
    chat.register_command('character', chracter_cmd)
    chat.register_command('restartrf', ravenfall_restart_cmd)
    chat.register_command('restartrfbot', ravenbot_restart_cmd)
    chat.register_command('rfrestart', ravenfall_restart_cmd)
    chat.register_command('rfbotrestart', ravenbot_restart_cmd)
    chat.register_command('rfqueuerestart', ravenfall_queue_restart_cmd)
    chat.register_command('rfqrestart', ravenfall_queue_restart_cmd)
    chat.register_command('rfcancelrestart', ravenfall_queue_restart_cancel_cmd)
    chat.register_command('rfcrestart', ravenfall_queue_restart_cancel_cmd)
    chat.register_command('postpone', postpone_restart_cmd)
    chat.register_command('rfrestartstatus', get_restart_time_left_cmd)
    chat.register_command('rfrstatus', get_restart_time_left_cmd)
    chat.register_command('autorestart', toggle_auto_restart_cmd)
    chat.register_command('togglebotmonitor', toggle_bot_monitor_cmd)
    chat.register_command('backup', backup_state_data_cmd)

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

