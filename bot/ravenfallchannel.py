from __future__ import annotations

from typing import Optional, Dict, Any, List, Tuple, TYPE_CHECKING
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from twitchAPI.chat import Chat, ChatMessage
from ravenpy import RavenNest
import aiohttp

from datetime import timedelta

from .models import (
    Player, Village, Dungeon, Raid, GameMultiplier, GameSession,
    RavenBotMessage, RavenfallMessage, TownBoost, RFChannelEvent, RFChannelSubEvent
)
from .messagewaiter import MessageWaiter, RavenBotMessageWaiter, RavenfallMessageWaiter
from .middleman import send_to_client, send_to_server_and_wait_response, send_to_server
from .ravenfallrestarttask import RFRestartTask, RestartReason
from .cooldown import Cooldown, CooldownBucket
from .multichat_command import send_multichat_command
from .messageprocessor import RavenMessage, MessageMetadata
from .ravenfallloc import RavenfallLocalization
from .message_templates import RavenBotTemplates
from .message_builders import SenderBuilder
from bot import middleman
from database.session import get_async_session
from database.models import AutoRaidStatus, Character
import database.utils as db_utils
from sqlalchemy import select

if TYPE_CHECKING:
    from .ravenfallmanager import RFChannelManager

from utils.routines import routine
from utils.format_time import format_seconds, TimeSize
from utils.backup_file_with_date import backup_file_with_date_async
from utils.runshell import restart_process, runshell, runshell_detached
from utils.strutils import split_by_utf16_bytes
from utils import utils

import asyncio
import time
import logging
import os
import json
import math

# Configure logger for this module
logger = logging.getLogger(__name__)

MAX_RETRIES = 3  # Maximum number of restart attempts before giving up
RETRY_WINDOW = 3 * 60  # Number of seconds to wait before resetting attempt counter

# Command timeout values in seconds for monitored commands
COMMAND_TIMEOUTS = {
    'coins': 5, 'count': 5, 'damage': 5, 'dmg': 5, 'dps': 5, 'effects': 5, 'ferry': 5, 'items': 5,
    'multiplier': 5, 'online': 5, 'pubsub': 5, 'res': 5, 'resources': 5, 'rested': 5, 'status': 5, 'stats': 5,
    'town': 5, 'townres': 5, 'training': 5, 'value': 5, 'version': 5, 'village': 5, 'villagers': 5, 'where': 5,
    
    'consume': 15, 'disenchant': 20, 'drink': 15, 'eat': 15, 'enchant': 20,
    'gift': 15, 'join': 60, 'leave': 10, 'scrolls': 15,
}



class RFChannel:
    """
    Represents a Ravenfall channel with its configuration and state.
    
    Args:
        config: Dictionary containing channel configuration with the following keys:
            - channel_id (str): Unique channel identifier
            - channel_name (str): Name of the channel
            - rf_query_url (str): Base URL for Ravenfall API
            - ravenbot_prefix (str | list | tuple, optional): Command prefix. Defaults to '!'
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
        self.ravenbot_prefixes: tuple = config.get('ravenbot_prefix', ('!',))
        self.custom_town_msg: str = config.get('custom_town_msg', '')
        self.welcome_message: str = config.get('welcome_message', '')
        self.auto_restart: bool = bool(config.get('auto_restart', False))
        self.event_notifications: bool = bool(config.get('event_notifications', False))
        self.restart_period: int = int(config.get('restart_period', 3600))
        self.sandboxie_box: str = config.get('sandboxie_box', '')
        self.ravenfall_start_script: str = config.get('ravenfall_start_script', '')
        self.middleman_connection_id: str = config.get('middleman_connection_id', '')
        self.ravenfall_loc_strings_path: str | None = config.get('ravenfall_loc_strings_path', None)

        if isinstance(self.ravenbot_prefixes, str):
            self.ravenbot_prefixes = (self.ravenbot_prefixes,)
        elif isinstance(self.ravenbot_prefixes, list):
            self.ravenbot_prefixes = tuple(self.ravenbot_prefixes)

        self.raid: Raid | None = None
        self.dungeon: Dungeon | None = None
        self.multiplier: GameMultiplier | None = None
        self.event_text: str = "No active event."
        self.event: RFChannelEvent = RFChannelEvent.NONE
        self.sub_event: RFChannelSubEvent = RFChannelSubEvent.NONE

        # Message waiters
        self.twitch_message_waiter: MessageWaiter = MessageWaiter()
        self.ravenbot_waiter: RavenBotMessageWaiter = RavenBotMessageWaiter()
        self.ravenfall_waiter: RavenfallMessageWaiter = RavenfallMessageWaiter()
        
        self.rfloc = RavenfallLocalization('data/definitions.yaml', self.ravenfall_loc_strings_path)

        self.max_dungeon_hp: int = 0
        self.current_mult: float | None = None

        self.global_restart_lock: asyncio.Lock = manager.global_restart_lock
        self.channel_restart_lock: asyncio.Lock = asyncio.Lock()
        self.channel_restart_future: asyncio.Future | None = None
        self.channel_post_restart_lock: asyncio.Lock = asyncio.Lock()

        self.restart_task: RFRestartTask | None = None
        
        # Monitoring state
        self.monitoring_paused = False
        self.is_monitoring = False  # Whether we're currently monitoring a command
        self.current_monitor = None  # Current monitor info if monitoring
        self.restart_attempts = {'count': 0, 'last_attempt': 0}  # Track restart attempts
        self.restart_future = None  # Current restart future if any

        self.cooldowns: Cooldown = Cooldown()

        self.middleman_connection_status: middleman.ConnectionStatus = middleman.ConnectionStatus()
        
    async def start(self):
        await self.chat.join_room(self.channel_name)
        self.update_boosts_routine.start()
        self.update_mult_routine.start()
        self.update_events_routine.start()
        self.backup_state_data_routine.start()
        self.auto_restart_routine.start()
        self.dungeon_killswitch_routine.start()
        self.update_middleman_connection_status_routine.start()

    async def stop(self):
        self.update_boosts_routine.cancel()
        self.update_mult_routine.cancel()
        self.update_events_routine.cancel()
        self.backup_state_data_routine.cancel()
        self.auto_restart_routine.cancel()
        self.dungeon_killswitch_routine.cancel()
        self.update_middleman_connection_status_routine.cancel()

    async def send_chat_message(self, message: str):
        await self.chat.send_message(self.channel_name, message)
        await self.monitor_ravenfall_command(content=message)

    async def event_twitch_message(self, message: ChatMessage):
        await self.twitch_message_waiter.process_message(message)
        await self.monitor_ravenfall_command(message=message)
        if message.first and any(message.text.lower().startswith(f"{prefix}join") for prefix in self.ravenbot_prefixes):
            await self.first_time_joiner(message)
    
    async def event_ravenbot_message(self, message: RavenBotMessage):
        await self.ravenbot_waiter.process_message(message)

    async def event_ravenfall_message(self, message: RavenfallMessage):
        await self.ravenfall_waiter.process_message(message)

    async def process_ravenbot_message(self, message: RavenBotMessage, metadata: MessageMetadata):
        return message

    async def process_ravenfall_message(self, message: RavenfallMessage, metadata: MessageMetadata):
        if not self.ravenfall_loc_strings_path:
            return message
        # Make sure session data and other things are not processed
        if message['Identifier'] != 'message':
            return message
        match = self.rfloc.get_match(message['Format'])
        key = ""
        if match is not None:
            key = match.key
        asyncio.create_task(self.process_auto_raid_sessionless(message.copy(), key))
        trans_str = self.rfloc.translate_string(message['Format'], message['Args'], match).strip()
        if len(trans_str) == 0:
            return {'block': True}
        trans_strs = split_by_utf16_bytes(trans_str, 500)
        if len(trans_strs) > 1:
            asyncio.create_task(self.send_split_msgs(message, trans_strs))
            return {'block': True}
        message['Format'] = trans_strs[0]
        message['Args'] = []
        return message
    
    async def send_split_msgs(self, message: RavenfallMessage, msgs: list[str]):
        for msg in msgs:
            message['Format'] = msg
            message['Args'] = []
            await send_to_client(self.middleman_connection_id, json.dumps(message))
            await asyncio.sleep(0.1)

    async def process_auto_raid_sessionless(self, message: RavenfallMessage, key: str):
        async with get_async_session() as session:
            await self.process_auto_raid(session, message, key)
    
    async def process_auto_raid(self, session: AsyncSession, message: RavenfallMessage, key: str):
        char_id = message['Recipent']['CharacterId']
        twitch_name = message['Recipent']['PlatformUserName']
        twitch_id = message['Recipent']['PlatformId']
        match key:
            case "auto_raid_activate_count":
                await self.add_auto_raid(session, char_id, twitch_id, twitch_name, int(message['Args'][0]))
            case "auto_raid_activate":
                await self.add_auto_raid(session, char_id, twitch_id, twitch_name)
            case "auto_raid_activate_cost":
                await self.add_auto_raid(session, char_id, twitch_id, twitch_name)
            case "auto_raid_deactivate":
                await self.remove_auto_raid(session, char_id)
            case "auto_raid_status_none":
                await self.remove_auto_raid(session, char_id)
            case "join_welcome":
                await self.restore_auto_raid(session, char_id, twitch_name)
    
    async def add_auto_raid(self, session: AsyncSession, char_id: str, twitch_id: str, username: str, count: int = 2147483647):
        _char = await db_utils.get_character(session, char_id, twitch_id=twitch_id, name=username)
        result = await session.execute(
            select(AutoRaidStatus).where(AutoRaidStatus.char_id == char_id)
        )
        auto_raid_obj = result.scalar_one_or_none()
        if auto_raid_obj is None:
            auto_raid_obj = AutoRaidStatus(
                char_id=char_id,
                auto_raid_count=count
            )
            session.add(auto_raid_obj)
        else:
            auto_raid_obj.auto_raid_count = count
        logging.debug(f"Added auto raid for {username} with count {count}")
    
    async def remove_auto_raid(self, session: AsyncSession, char_id: str):
        result = await session.execute(
            select(AutoRaidStatus).where(AutoRaidStatus.char_id == char_id)
        )
        auto_raid_obj = result.scalar_one_or_none()
        if auto_raid_obj is not None:
            await session.delete(auto_raid_obj)
        logging.debug(f"Removed auto raid for {char_id}")

    async def get_auto_raids(self, session: AsyncSession, char_ids: list[str]):
        result = await session.execute(
            select(AutoRaidStatus).where(AutoRaidStatus.char_id.in_(char_ids))
        )
        return result.scalars().all()

    async def fetch_auto_raids(self):
        chars: List[Player] = await self.get_query("select * from players")
        char_ids = [char['id'] for char in chars]
        char_id_to_player = {char['id']: char for char in chars}
        async with get_async_session() as session:
            result = await session.execute(
                select(AutoRaidStatus, Character.twitch_id).where(AutoRaidStatus.char_id.in_(char_ids)).join(Character)
            )
            auto_raids = result.all()
            for row in auto_raids:
                auto_raid: AutoRaidStatus
                twitch_id: int
                auto_raid, twitch_id = row
                sender = SenderBuilder(
                    username=char_id_to_player[auto_raid.char_id]['name'],
                    display_name=char_id_to_player[auto_raid.char_id]['name'],
                    platform_id=str(twitch_id)
                ).build()
                msg = RavenBotTemplates.auto_raid_status(sender)
                response = await send_to_server_and_wait_response(self.middleman_connection_id, msg)
                if response['success']:
                    match = self.rfloc.get_match(response['responses'][0]['Format'])
                    await self.process_auto_raid(session, response['responses'][0], match.key)
    
    async def restore_auto_raids(self):
        chars: List[Player] = await self.get_query("select * from players")
        char_ids = [char['id'] for char in chars]
        char_id_to_player = {char['id']: char for char in chars}
        logging.debug(f"Restoring auto raids for {len(char_ids)} characters")
        async with get_async_session() as session:
            result = await session.execute(
                select(AutoRaidStatus, Character.twitch_id).where(AutoRaidStatus.char_id.in_(char_ids)).join(Character)
            )
            auto_raids = result.all()
            for row in auto_raids:
                auto_raid: AutoRaidStatus
                twitch_id: int
                auto_raid, twitch_id = row
                sender = SenderBuilder(
                    username=char_id_to_player[auto_raid.char_id]['name'],
                    display_name=char_id_to_player[auto_raid.char_id]['name'],
                    platform_id=str(twitch_id)
                ).build()
                msg = RavenBotTemplates.auto_join_raid(sender, auto_raid.auto_raid_count)
                await send_to_server(self.middleman_connection_id, msg)
    
    async def restore_auto_raid(self, session: AsyncSession, char_id: str, username: str):
        result = await session.execute(
            select(AutoRaidStatus, Character.twitch_id)
            .where(AutoRaidStatus.char_id == char_id)
            .join(Character)
        )
        row = result.one_or_none()  # Returns a Row object or None
        if row is not None:
            auto_raid: AutoRaidStatus
            twitch_id: int
            auto_raid, twitch_id = row  # Unpack the row
            logging.debug(f"Restoring auto raid for {username}")
            sender = SenderBuilder(
                username=username,
                display_name=username,
                platform_id=str(twitch_id)
            ).build()
            msg = RavenBotTemplates.auto_join_raid(sender, auto_raid.auto_raid_count)
            await send_to_server(self.middleman_connection_id, msg)
        else:
            logging.debug(f"No auto raid found for {username}")   
    
    async def send_ravenbot_chat_message(self, text: str, cid: str):
        message = {
            "Identifier": "message",
            "CorrelationId": cid,
            "Recipent": {
                "UserId": "00000000-0000-0000-0000-000000000000",
                "CharacterId": "00000000-0000-0000-0000-000000000000",
                "Platform": "twitch",
                "PlatformId": "",
                "PlatformUserName": ""
            },
            "Format": text,
            "Args": []
        }
        await send_to_client(self.middleman_connection_id, json.dumps(message))

    async def get_town_boost(self) -> List[TownBoost] | None:
        village: Village = await self.get_query("select * from village")
        split = village['boost'].split()
        if len(split) < 2:
            return []
        boost_stat = split[0]
        boost_value = float(split[1].rstrip("%"))
        return [TownBoost(boost_stat, boost_value/100)]

    async def first_time_joiner(self, msg: ChatMessage):
        cd = self.cooldowns.scoped(
            "first_time_joiner",
            CooldownBucket(rate=1, per=10)
        )
        if cd.is_rate_limited():
            return
        await asyncio.sleep(3)
        boost = await self.get_town_boost()
        welcome_msg = self.welcome_message.format_map({
            "townSkillLower": boost[0].skill.lower(),
            "townSkill": boost[0].skill,
            "userName": msg.user.name,
            "userDisplayName": msg.user.display_name
        })
        await self.send_chat_message(welcome_msg)
        cd.update_rate_limit()
    
    async def monitor_ravenfall_command(
        self,
        *,
        message: ChatMessage | None = None,
        content: str | None = None,
    ):
        if message is None:
            content = content  # this is here because of typing yes
        else:
            content = message.text
        prefix = next((p for p in self.ravenbot_prefixes if content.startswith(p)), None)
        if not prefix:
            return

        parts = content[len(prefix):].strip().split(maxsplit=1)
        if not parts:  # If there's no command after prefix
            return
            
        command = parts[0].lower()
        timeout = COMMAND_TIMEOUTS.get(command)
        is_monitored_command = timeout is not None
        if not is_monitored_command:
            return
        if self.sub_event == RFChannelSubEvent.DUNGEON_PREPARE:
            await asyncio.sleep(0.5)
            if message is not None:
                await message.reply("please wait, the game is currently frozen because it is busy preparing a dungeon...")
        else:
            if message is None:
                resend_text = content
            else:
                resend_text = message.text if message.user.id == os.getenv("BOT_ID") else None
            self.monitor_ravenbot_response(command, timeout=timeout, resend_text=resend_text)

    async def _monitor_ravenbot_response_task(self, command: str, timeout: float = 5, resend_text: str = None):
        """
        Monitor for RavenBot's response to a command.
        If no response, restart RavenBot up to MAX_RETRIES times.
        If still unresponsive after MAX_RETRIES, restart Ravenfall.
        """
        if self.monitoring_paused or self.is_monitoring:
            return
            
        self.is_monitoring = True
        self.current_monitor = {
            'command': command,
            'start_time': time.time()
        }
        logger.debug(f"Monitoring for response to command: {command} in {self.channel_name}")
        
        while self.sub_event == RFChannelSubEvent.DUNGEON_PREPARE:
            await asyncio.sleep(5)

        # Wait for any message from RavenBot
        if not self.manager.middleman_connected:
            response = await self.twitch_message_waiter.wait_for_message(
                check=lambda m: m.user.id == os.getenv("RAVENBOT_USER_ID"),
                timeout=timeout,
                max_age=1  
            )
        else:
            response = await self.ravenbot_waiter.wait_for_message(
                timeout=timeout,
                max_age=1,
                check=lambda m: True
            )
        
        asked_to_retry = False
        if response is None:
            # No response, handle retry or restart
            current_time = time.time()
            
            # Reset counter if last attempt was long ago
            if current_time - self.restart_attempts['last_attempt'] > RETRY_WINDOW:
                self.restart_attempts['count'] = 0
            
            self.restart_attempts['count'] += 1
            self.restart_attempts['last_attempt'] = current_time
            attempts = self.restart_attempts['count']
            attempts_remaining = MAX_RETRIES - attempts
            
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
                resp_giveup = "I give up @abrokecube dinkDonk"
            
            if attempts_remaining > 0:
                if attempts == 1:
                    await self.send_chat_message(resp_retry)
                elif attempts > 1 and attempts < MAX_RETRIES:
                    await self.send_chat_message(resp_retry_2)

                await self.restart_ravenbot()
                await asyncio.sleep(3)
                asked_to_retry = True
                
                await self.send_chat_message(resp_user_retry)
            elif attempts_remaining == 0:
                await self.send_chat_message(resp_restart_ravenfall)
                restart_task = self.queue_restart(
                    time_to_restart=10,  # Start restart in 10 seconds
                    label="RavenBot is unresponsive",
                    reason=RestartReason.UNRESPONSIVE
                )
                await restart_task.wait()
                await self.send_chat_message(resp_user_retry_2)
                asked_to_retry = True
            else:
                await self.send_chat_message(resp_giveup)
        else:
            logger.debug(f"Recieved a response to command: {command} in {self.channel_name}")

        self.is_monitoring = False
        self.current_monitor = None
        if asked_to_retry and resend_text:
            self.monitor_ravenbot_response(command, timeout, resend_text)

    def monitor_ravenbot_response(self, command: str, timeout: float = 10.0, resend_text: str = None):
        asyncio.create_task(self._monitor_ravenbot_response_task(command, timeout, resend_text))

                        
    async def wait_for_ravenbot_command(self, command: str, timeout: float = 10.0) -> Optional[RavenBotMessage]:
        """Wait for a specific RavenBot command.
        
        Args:
            command: The command to wait for
            timeout: Maximum time to wait in seconds
            
        Returns:
            The matching message if found, or None if timeout
        """
        return await self.ravenbot_waiter.wait_for_command(command, timeout=timeout)
        
    async def wait_for_ravenfall_format(self, format_str: str, timeout: float = 10.0) -> Optional[RavenfallMessage]:
        """Wait for a Ravenfall message with a specific format.
        
        Args:
            format_str: The format string to match
            timeout: Maximum time to wait in seconds
            
        Returns:
            The matching message if found, or None if timeout
        """
        return await self.ravenfall_waiter.wait_for_format_match(format_str, timeout=timeout)

    async def get_query(self, query: str, timeout: int = 5, suppress_timeout_error: bool = False) -> Any:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            try:
                r = await session.get(f"{self.rf_query_url}/{query}")
            except asyncio.TimeoutError:
                if not suppress_timeout_error:
                    logger.error(f"Timeout fetching Ravenfall query from {self.rf_query_url}")
                return None
            except Exception as e:
                logger.error(f"Error fetching Ravenfall query from {self.rf_query_url}: {e}", exc_info=True)
                return None
            data = await r.json()
        return data

    def _ravenbot_is_muted(self):
        if not self.manager.middleman_enabled:
            return False
        return not self.middleman_connection_status.server_connected

    @routine(delta=timedelta(hours=3), wait_first=True)
    async def update_boosts_routine(self):
        if self.channel_restart_lock.locked():
            async with self.channel_restart_lock:
                return
        village: Village = await self.get_query("select * from village")
        if len(village['boost'].strip()) <= 0:
            return
        split = village['boost'].split()
        boost_stat = split[0]
        boost_value = float(split[1].rstrip("%"))
        msg = f"{self.ravenbot_prefixes[0]}town {boost_stat.lower()}"
        await self.send_chat_message(msg)

    @routine(delta=timedelta(seconds=3))
    async def update_mult_routine(self):
        if self.channel_restart_lock.locked():
            async with self.channel_restart_lock:
                return
        multiplier: GameMultiplier = await self.get_query("select * from multiplier")
        if not multiplier:
            return
        self.multiplier = multiplier
        if not self.current_mult:
            self.current_mult = multiplier['multiplier']
        ravenbot_is_muted = self._ravenbot_is_muted()
        if multiplier['multiplier'] > self.current_mult:
            msg = f"{multiplier['eventname']} increased the multiplier to {int(multiplier['multiplier'])}x, ending in {format_seconds(multiplier['timeleft'], TimeSize.MEDIUM_SPACES)}!"
            await self.send_chat_message(msg)
        elif ravenbot_is_muted and multiplier['multiplier'] < self.current_mult and multiplier['multiplier'] == 1:
            msg = f"The exp multiplier has expired."
            await self.send_chat_message(msg)
        
        self.current_mult = multiplier['multiplier']

    @routine(delta=timedelta(seconds=1), wait_first=True)
    async def update_events_routine(self):
        if self.channel_restart_lock.locked():
            self.event_text = "Ravenfall is restarting..."
            self.dungeon = None
            self.raid = None
            async with self.channel_restart_lock:
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
                boss_max_hp = min(1, self.max_dungeon_hp)
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
        asyncio.create_task(self.game_event_chat_message(sub_event, old_sub_event))
        asyncio.create_task(self.game_event_wake_ravenbot(sub_event))
        asyncio.create_task(self.game_event_fetch_auto_raids(old_sub_event, sub_event))
    
    async def game_event_chat_message(self, sub_event: RFChannelSubEvent, old_sub_event: RFChannelSubEvent):
        if self.event_notifications:
            if old_sub_event != RFChannelSubEvent.RAID and sub_event == RFChannelSubEvent.RAID:
                msg = (
                    f"RAID – "
                    f"Boss HP: {self.raid['boss']['health']:,} "
                )
                await self.send_chat_message(msg)
            elif old_sub_event != RFChannelSubEvent.DUNGEON_STARTED and sub_event == RFChannelSubEvent.DUNGEON_STARTED:
                msg = (
                    f"DUNGEON – "
                    f"Boss HP: {self.max_dungeon_hp:,} – "
                    f"Enemies: {self.dungeon['enemies']:,} – "
                    f"Players: {self.dungeon['players']:,}"
                )
                await self.send_chat_message(msg)
        if self._ravenbot_is_muted():
            if old_sub_event != RFChannelSubEvent.DUNGEON_READY and sub_event == RFChannelSubEvent.DUNGEON_READY:
                await asyncio.sleep(2)
                players = self.dungeon['players']
                if players == 0:
                    await self.send_chat_message(f"A dungeon is available!")
                else:
                    await self.send_chat_message(f"A dungeon is available! {utils.pl2(players, 'player has', 'players have')} joined.")
            elif old_sub_event != RFChannelSubEvent.RAID and sub_event == RFChannelSubEvent.RAID:
                await asyncio.sleep(2)
                players = self.raid['players']
                if self.event_notifications:
                    if players > 0:
                        await self.send_chat_message(f"{utils.pl2(players, 'player has', 'players have')} joined the raid.")
                else:
                    if players == 0:
                        await self.send_chat_message(f"A level {self.raid['boss']['combatlevel']} raid is available!")
                    else:
                        await self.send_chat_message(f"A level {self.raid['boss']['combatlevel']} raid is available! {utils.pl2(players, 'player has', 'players have')} joined.")

    async def game_event_wake_ravenbot(self, sub_event: RFChannelSubEvent):
        if self.manager.middleman_power_saving and self.manager.middleman_connected:
            if sub_event == RFChannelSubEvent.DUNGEON_BOSS:
                await middleman.ensure_connected(self.middleman_connection_id, 60)
            if sub_event == RFChannelSubEvent.RAID and self.raid['players'] > 0:
                await middleman.ensure_connected(self.middleman_connection_id, 60)

    async def game_event_fetch_auto_raids(self, old_sub_event: RFChannelSubEvent, sub_event: RFChannelSubEvent):
        if old_sub_event != sub_event and sub_event == RFChannelSubEvent.RAID:
            await asyncio.sleep(10)
            await self.fetch_auto_raids()

    @routine(delta=timedelta(seconds=30), wait_first=True)
    async def dungeon_killswitch_routine(self):
        if not self.sub_event == RFChannelSubEvent.DUNGEON_BOSS:
            return
        if self.dungeon['elapsed'] > 60 * 15:  # 15 minutes
            await self.send_chat_message(f"{self.ravenbot_prefixes[0]}dungeon stop")

    @routine(delta=timedelta(hours=5), wait_first=True)
    async def backup_state_data_routine(self):
        async with self.channel_restart_lock:
            async with self.channel_post_restart_lock:
                if self.manager.ravennest_is_online:
                    r = await send_multichat_command(
                        text="?resync",
                        user_id=self.channel_id,
                        user_name=self.channel_name,
                        channel_id=self.channel_id,
                        channel_name=self.channel_name
                    )
                    if r['status'] != 200:
                        await self.send_chat_message("?resync")
                    await asyncio.sleep(15)
                await backup_file_with_date_async(
                    f"{os.getenv('RAVENFALL_SANDBOXED_FOLDER').replace('{box}', self.sandboxie_box).rstrip('\\/')}\\state-data.json",
                    int(os.getenv('BACKUP_RETENTION_COUNT'))
                )
                logger.info(f"Backed up state data for {self.channel_name}")

    @routine(delta=timedelta(seconds=20))
    async def auto_restart_routine(self):
        if self.channel_restart_lock.locked():
            async with self.channel_restart_lock:
                return
        game_session: GameSession = await self.get_query("select * from session", 1)
        uptime = None
        if game_session:
            uptime = game_session['secondssincestart']
        if uptime is None:
            logger.warning(f"{self.channel_name} seems to be offline...")
            self.queue_restart(5, label="Ravenfall seems to be offline...", reason=RestartReason.UNRESPONSIVE)
            return

        if not self.auto_restart:
            return
        if not self.restart_period or self.restart_period <= 0:
            return
        if self.restart_task and not self.restart_task.finished():
            return
        period = max(20*60,self.restart_period)
        seconds_to_restart = max(60, period - uptime)
        self.queue_restart(seconds_to_restart, label="Scheduled restart", reason=RestartReason.AUTO)

    @routine(delta=timedelta(seconds=1))
    async def update_middleman_connection_status_routine(self):
        conn_status = None
        if self.manager.middleman_connected:
            conn_status, err = await middleman.get_connection_status(self.middleman_connection_id)
        if conn_status is None:
            if self.manager.middleman_enabled:
                self.middleman_connection_status = middleman.ConnectionStatus(
                    connection_id=self.middleman_connection_id,
                    client_connected=False,
                    server_connected=False,
                )
            else:
                self.middleman_connection_status = middleman.ConnectionStatus(
                    connection_id=self.middleman_connection_id,
                    client_connected=True,
                    server_connected=True,
                )
            return
        self.middleman_connection_status = conn_status

    async def ravenfall_pre_restart(self):
        r = await send_multichat_command(
            text="?randleave",
            user_id=self.channel_id,
            user_name=self.channel_name,
            channel_id=self.channel_id,
            channel_name=self.channel_name
        )
        if r['status'] != 200:
            await self.send_chat_message("?randleave")
        await asyncio.sleep(15)

    async def restart_ravenfall(
        self, 
        run_pre_restart: bool = True, 
        run_post_restart: bool = True,
        silent: bool = False,
    ):
        if self.channel_restart_future and not self.channel_restart_future.done():
            return await self.channel_restart_future
        self.channel_restart_future = asyncio.Future()

        if run_pre_restart:
            await self.ravenfall_pre_restart()
            
        await self.channel_restart_lock.acquire()
        if not silent and self.global_restart_lock.locked():
            await self.send_chat_message("Waiting for other restarts to finish...")
        await self.global_restart_lock.acquire()
        logger.info(f"Restarting Ravenfall for {self.channel_name}")
        if not silent:
            await self.send_chat_message("Restarting Ravenfall...")
        code, text = await restart_process(
            self.sandboxie_box, 
            "Ravenfall.exe", 
            f"cd {os.getenv('RAVENFALL_FOLDER')} & {self.ravenfall_start_script}"
        )
        if code != 0:
            logger.error(f"Failed to restart Ravenfall for {self.channel_name}: code {code}, text {text}")
            if not silent:
                await self.send_chat_message("Failed to restart Ravenfall!")
            self.channel_restart_future.set_result(False)
            self.channel_restart_lock.release()
            self.global_restart_lock.release()
            return False

        await asyncio.sleep(5)
        start_time = time.time()
        auth_timeout = 120
        authenticated = False
        
        while time.time() - start_time < auth_timeout:
            session: GameSession = await self.get_query("select * from session", 1, suppress_timeout_error=True)
            if session and session.get('authenticated', False):
                authenticated = True
                break
            await asyncio.sleep(1)
        if not authenticated:
            self.channel_restart_future.set_result(False)
            self.channel_restart_lock.release()
            self.global_restart_lock.release()
            return False
        # if not silent:
        #     await self.send_chat_message("Ravenfall has been restarted.")
        if self.manager.middleman_power_saving and self.manager.middleman_connected:
            await middleman.force_reconnect(self.middleman_connection_id, 60)
        logger.info(f"Restarted Ravenfall for {self.channel_name}")

        if run_post_restart:
            await self.channel_post_restart_lock.acquire()
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
        r = await send_multichat_command(
            text="?undorandleave",
            user_id=self.channel_id,
            user_name=self.channel_name,
            channel_id=self.channel_id,
            channel_name=self.channel_name
        )
        if r['status'] != 200:
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
        r = await send_multichat_command(
            text="?sailall",
            user_id=self.channel_id,
            user_name=self.channel_name,
            channel_id=self.channel_id,
            channel_name=self.channel_name
        )
        if r['status'] != 200:
            await self.send_chat_message("?sailall")
        await self.restore_auto_raids()
    
    async def restart_ravenbot(self):
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

    def queue_restart(self, time_to_restart: int | None = None, mute_countdown: bool = False, label: str = "", reason: RestartReason | None = None):
        if self.restart_task:
            self.restart_task.cancel()
        self.restart_task = RFRestartTask(self, self.manager, time_to_restart, mute_countdown, label, reason)
        self.restart_task.start()
        logger.info(f"Restart task queued for {self.channel_name} with label {label} in {format_seconds(time_to_restart, TimeSize.SMALL, 2, False)}.")
        return self.restart_task

    def postpone_restart(self, seconds: int):
        if self.restart_task:
            self.restart_task.postpone(seconds)
        