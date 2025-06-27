from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from twitchAPI.chat import Chat, ChatMessage
from ravenpy import RavenNest
import aiohttp

from datetime import timedelta

from .models import Player, Village, Dungeon, Raid, GameMultiplier, GameSession
from .messagewaiter import MessageWaiter
from .ravenfallmanager import RFChannelManager
from .ravenfallrestarttask import RFRestartTask

from utils.routines import routine
from utils.format_time import format_seconds, TimeSize
from utils.backup_file_with_date import backup_file_with_date
from utils.runshell import restart_process, runshell, runshell_detached

import asyncio
import time
from enum import Enum
import os

class RFChannelEvent(Enum):
    NONE = 0
    DUNGEON = 1
    RAID = 2

class RFChannelSubEvent(Enum):
    NONE = 0
    DUNGEON_PREPARE = 1
    DUNGEON_READY = 2
    DUNGEON_STARTED = 3
    DUNGEON_BOSS = 4
    RAID = 5

class RFChannel:
    """
    Represents a Ravenfall channel with its configuration and state.
    
    Args:
        config: Dictionary containing channel configuration with the following keys:
            - channel_id (str): Unique channel identifier
            - channel_name (str): Name of the channel
            - rf_query_url (str): Base URL for Ravenfall API
            - ravenbot_prefix (str, optional): Command prefix. Defaults to '!'
            - custom_town_msg (str, optional): Custom town message. Defaults to ''
            - welcome_message (str, optional): Welcome message. Defaults to ''
            - auto_restart (bool, optional): Whether to auto-restart. Defaults to False
            - event_notifications (bool, optional): Whether to send event notifications. Defaults to False
            - restart_period (int, optional): Restart period in seconds. Defaults to 3600
            - sandboxie_box (str, optional): Sandboxie box name. Defaults to ''
            - ravenfall_start_script (str, optional): Path to Ravenfall start script. Defaults to ''
    """
    
    def __init__(
        self,
        config: Dict[str, Any],
        manager: RFChannelManager,
    ):
        self.chat: Chat = manager.chat
        self.ravennest: RavenNest = manager.rfapi
        self.manager: RFChannelManager = manager

        # Required fields
        self.channel_id: str = str(config['channel_id'])
        self.channel_name: str = config['channel_name'].lower()
        self.rf_query_url: str = config['rf_query_url'].rstrip('/')
        
        # Optional fields with defaults
        self.ravenbot_prefix: str = config.get('ravenbot_prefix', '!')
        self.ravenbot_is_muted: bool = bool(config.get('ravenbot_is_muted', False))
        self.custom_town_msg: str = config.get('custom_town_msg', '')
        self.welcome_message: str = config.get('welcome_message', '')
        self.auto_restart: bool = bool(config.get('auto_restart', False))
        self.event_notifications: bool = bool(config.get('event_notifications', False))
        self.restart_period: int = int(config.get('restart_period', 3600))
        self.sandboxie_box: str = config.get('sandboxie_box', '')
        self.ravenfall_start_script: str = config.get('ravenfall_start_script', '')

        self.raid: Raid | None = None
        self.dungeon: Dungeon | None = None
        self.multiplier: GameMultiplier | None = None
        self.event_text: str = "No active event."
        self.event: RFChannelEvent = RFChannelEvent.NONE
        self.sub_event: RFChannelSubEvent = RFChannelSubEvent.NONE

        self.twitch_message_waiter: MessageWaiter = MessageWaiter()
        self.max_dungeon_hp: int = 0
        self.current_mult: float | None = None

        self.global_restart_lock: asyncio.Lock = manager.global_restart_lock

        self.channel_restart_lock: asyncio.Lock = asyncio.Lock()
        self.channel_restart_future: asyncio.Future = asyncio.Future()
        self.channel_post_restart_lock: asyncio.Lock = asyncio.Lock()

        self.restart_task: RFRestartTask | None = None
        
    async def start(self):
        await self.chat.join_room(self.channel_name)
        self.update_boosts_routine.start()
        self.update_mult_routine.start()
        self.update_events_routine.start()
        self.backup_state_data_routine.start()

    async def send_chat_message(self, message: str):
        await self.chat.send_message(self.channel_name, message)

    async def event_twitch_message(self, message: ChatMessage):
        await self.twitch_message_waiter.process_message(message)
    
    async def event_ravenbot_message(self, message: dict):
        ...

    async def event_ravenfall_message(self, message: dict):
        ...

    async def get_query(self, query: str, timeout: int = 5) -> Any:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            try:
                r = await session.get(f"{self.rf_query_url}/{query}")
            except Exception as e:
                print(f"Error fetching Ravenfall query from {self.rf_query_url}: {e}")
                return None
            data = await r.json()
        return data

    @routine(delta=timedelta(hours=3), wait_first=True)
    async def update_boosts_routine(self):
        if self.channel_restart_lock.locked():
            with self.channel_restart_lock:
                return
        village: Village = await self.get_query("select * from village")
        if len(village['boost'].strip()) <= 0:
            return
        split = village['boost'].split()
        boost_stat = split[0]
        boost_value = float(split[1].rstrip("%"))
        msg = f"{self.ravenbot_prefix}town {boost_stat.lower()}"
        await self.send_chat_message(msg)

    @routine(delta=timedelta(seconds=3))
    async def update_mult_routine(self):
        if self.channel_restart_lock.locked():
            with self.channel_restart_lock:
                return
        multiplier: GameMultiplier = await self.get_query("select * from multiplier")
        if not multiplier:
            return
        self.multiplier = multiplier
        if not self.current_mult:
            self.current_mult = multiplier['multiplier']
        if multiplier['multiplier'] > self.current_mult:
            msg = f"{multiplier['eventname']} increased the multiplier to {int(self.current_mult)}x, ending in {format_seconds(multiplier['timeleft'], TimeSize.MEDIUM_SPACES)}!"
            await self.send_chat_message(msg)
        elif self.ravenbot_is_muted and multiplier['multiplier'] < self.current_mult and multiplier['multiplier'] == 1:
            msg = f"The exp multiplier has expired."
            await self.send_chat_message(msg)
        
        self.current_mult = multiplier['multiplier']

    @routine(delta=timedelta(seconds=1), wait_first=True)
    async def update_events_routine(self):
        if self.channel_restart_lock.locked():
            self.event_text = "Ravenfall is restarting..."
            self.dungeon = None
            self.raid = None
            with self.channel_restart_lock:
                return
        dungeon: Dungeon = await self.get_query("select * from dungeon")
        raid: Raid = await self.get_query("select * from raid")
        self.dungeon = dungeon
        self.raid = raid

        old_sub_event = self.sub_event

        event_text = "No active event."
        event = RFChannelEvent.NONE
        sub_event = RFChannelSubEvent.NONE
        if dungeon and dungeon.get('enemies'):
            if not dungeon['started']:
                self.max_dungeon_hp = dungeon["boss"]["health"]
                time_starting = format_seconds(dungeon['secondsuntilstart'])
                if dungeon['boss']['health'] > 0:
                    event_text = (
                        f"DUNGEON starting in {time_starting} – "
                        f"Boss HP: {dungeon['boss']['health']:,} – "
                        f"Enemies: {dungeon['enemies']:,} – "
                        f"Players: {dungeon['players']:,}"
                    )
                    event = RFChannelEvent.DUNGEON
                    sub_event = RFChannelSubEvent.DUNGEON_READY
                else:
                    event_text = (
                        f"DUNGEON is being prepared... – "
                        f"Enemies: {dungeon['enemies']:,}"
                    )
                    event = RFChannelEvent.DUNGEON
                    sub_event = RFChannelSubEvent.DUNGEON_PREPARE
            else:
                if dungeon['enemiesalive'] > 0:
                    self.max_dungeon_hp = dungeon["boss"]["health"]
                boss_max_hp = self.max_dungeon_hp
                if old_sub_event == RFChannelSubEvent.DUNGEON_PREPARE and self.event_notifications:
                    msg = (
                        f"DUNGEON – "
                        f"Boss HP: {boss_max_hp:,} – "
                        f"Enemies: {dungeon['enemies']:,}"
                    )
                    await self.send_chat_message(msg)
                event_text = (
                    f"DUNGEON – "
                    f"Boss HP: {dungeon['boss']['health']:,}/{boss_max_hp:,} "
                    f"({dungeon['boss']['health']/boss_max_hp:.1%}) – "
                    f"Enemies: {dungeon['enemiesalive']:,}/{dungeon['enemies']:,} – "
                    f"Players: {dungeon['playersalive']:,}/{dungeon['players']:,} – "
                    f"Elapsed time: {format_seconds(dungeon['elapsed'])}"
                )
                event = RFChannelEvent.DUNGEON
                if dungeon['enemiesalive'] > 0:
                    sub_event = RFChannelSubEvent.DUNGEON_STARTED
                else:
                    sub_event = RFChannelSubEvent.DUNGEON_BOSS
        elif raid and raid['started'] and raid['boss']['maxhealth'] > 0:
            if old_sub_event != RFChannelSubEvent.RAID and self.event_notifications:
                msg = (
                    f"RAID – "
                    f"Boss HP: {raid['boss']['health']:,} "
                )
                await self.send_chat_message(msg)
            event_text = (
                "RAID – "
                f"Boss HP: {raid['boss']['health']:,}/{raid['boss']['maxhealth']:,} "
                f"({raid['boss']['health']/raid['boss']['maxhealth']:.1%}) – "
                f"Players: {raid['players']:,} – "
                f"Time left: {format_seconds(raid['timeleft'])}"
            )
            event = RFChannelEvent.RAID
            sub_event = RFChannelSubEvent.RAID
        self.event_text = event_text
        self.event = event
        self.sub_event = sub_event

    @routine(delta=timedelta(hours=5), wait_first=True)
    async def backup_state_data_routine(self):
        async with self.channel_restart_lock:
            async with self.channel_post_restart_lock:
                await self.send_chat_message("?resync")
                await asyncio.sleep(15)
                backup_file_with_date(
                    f"{os.getenv('RAVENFALL_SANDBOXED_FOLDER').replace('{box}', self.sandboxie_box).rstrip('\\/')}\\state-data.json",
                    int(os.getenv('BACKUP_RETENTION_COUNT'))
                )
    
    async def ravenfall_pre_restart(self):
        await self.send_chat_message("?randleave")
        await asyncio.sleep(15)

    async def ravenfall_restart(
        self, 
        run_pre_restart: bool = True, 
        run_post_restart: bool = True
    ):
        if not self.channel_restart_future.done():
            return await self.channel_restart_future
        self.channel_restart_future = asyncio.Future()

        if run_pre_restart:
            await self.ravenfall_pre_restart()
            
        self.channel_restart_lock.acquire()
        self.global_restart_lock.acquire()
        print(f"Restarting Ravenfall for {self.channel_name}")
        await restart_process(
            self.sandboxie_box, 
            "Ravenfall.exe", 
            f"cd {os.getenv('RAVENFALL_FOLDER')} & {self.ravenfall_start_script}"
        )

        start_time = time.time()
        auth_timeout = 120
        authenticated = False
        
        while time.time() - start_time < auth_timeout:
            session: GameSession = await self.get_query("select * from session", 1)
            if session and session.get('authenticated', False):
                authenticated = True
                break
            await asyncio.sleep(1)
        if not authenticated:
            self.channel_restart_future.set_result(False)
            self.channel_restart_lock.release()
            self.global_restart_lock.release()
            return False

        if run_post_restart:
            self.channel_post_restart_lock.acquire()
            self.channel_restart_lock.release()
            self.global_restart_lock.release()
            await self.ravenfall_post_restart()
            self.channel_post_restart_lock.release()
        else:
            self.channel_restart_lock.release()
            self.global_restart_lock.release()

        self.channel_restart_future.set_result(True)
        return True

    async def ravenfall_post_restart(self):
        # Wait for the game to start rejoining players
        while True:
            await asyncio.sleep(1)
            session: GameSession = await self.get_query("select * from session", 1)
            if session['players'] > 0:
                break
        await self.send_chat_message("?undorandleave")
        # Wait for the game to finish rejoining players
        player_count = 0
        while True:
            await asyncio.sleep(2)
            session: GameSession = await self.get_query("select * from session", 1)
            new_player_count = session['players']
            if player_count > 0 and new_player_count == player_count:
                break
            player_count = new_player_count
        await self.send_chat_message("?sailall")
    
    async def ravenbot_restart(self):
        if self.channel_name != "abrokecube":
            await restart_process(
                self.sandboxie_box, "RavenBot.exe", f"cd {os.getenv('RAVENBOT_FOLDER')} & start RavenBot.exe"
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

    def queue_restart(self, time_to_restart: int | None = None, mute_countdown: bool = False, label: str = ""):
        if self.restart_task:
            self.restart_task.cancel()
        self.restart_task = RFRestartTask(self, self.chat, time_to_restart, mute_countdown, label)
        self.restart_task.start()

    def postpone_restart(self, seconds: int):
        if self.restart_task:
            self.restart_task.postpone(seconds)
        