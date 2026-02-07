from __future__ import annotations

from typing import Optional, Dict, Any, List, Tuple, TYPE_CHECKING, Literal
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from twitchAPI.chat import Chat, ChatMessage
from ravenpy import RavenNest, experience_for_level
import aiohttp

from datetime import timedelta, timezone

from .models import (
    Player, Village, Dungeon, Raid, GameMultiplier, GameSession, Ferry,
    RavenBotMessage, RavenfallMessage, TownBoost, RFChannelEvent, RFChannelSubEvent,
    ScrollType, QueuedScroll
)
from .messagewaiter import MessageWaiter, RavenBotMessageWaiter, RavenfallMessageWaiter
from .middleman import send_to_client, send_to_server_and_wait_response
from .ravenfallrestarttask import RFRestartTask, RestartReason, WARNING_MSG_TIMES
from .cooldown import Cooldown, CooldownBucket
from .multichat_command import send_multichat_command, get_scroll_counts
from .messageprocessor import RavenMessage, MessageMetadata
from .ravenfallloc import RavenfallLocalization
from .message_templates import RavenBotTemplates
from .message_builders import SenderBuilder
from .exceptions import OutOfStockError
from .command_contexts import TwitchRedeemContext
from .command_enums import CustomRewardRedemptionStatus
from bot import middleman
from database.session import get_async_session
from database.models import AutoRaidStatus, Character, User
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
from collections import defaultdict, deque

# Configure logger for this module
logger = logging.getLogger(__name__)

MAX_RETRIES = 3  # Maximum number of restart attempts before giving up
RETRY_WINDOW = 3 * 60  # Number of seconds to wait before resetting attempt counter
MAX_DUNGEON_LENGTH = 15 * 60

# Command timeout values in seconds for monitored commands
COMMAND_TIMEOUTS = {
    'coins': 5, 'count': 5, 'damage': 5, 'dmg': 5, 'dps': 5, 'effects': 5, 'ferry': 5, 'items': 5,
    'multiplier': 5, 'online': 5, 'res': 5, 'resources': 5, 'rested': 5, 'status': 5, 'stats': 5,
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
        
        self.twitch = manager.bot.twitches.get(self.channel_id)
        
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
        self.auto_restore_raids: bool = config.get('auto_restore_raids', False)
        self.restart_timeout: int = int(config.get('restart_timeout', 120))
        self.town_level_notifications: bool = bool(config.get('town_level_notifications', True))

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
        self.town_level: int = 0

        # Message waiters
        self.twitch_message_waiter: MessageWaiter = MessageWaiter()
        self.ravenbot_waiter: RavenBotMessageWaiter = RavenBotMessageWaiter()
        self.ravenfall_waiter: RavenfallMessageWaiter = RavenfallMessageWaiter()
        
        self.rfloc = RavenfallLocalization('data/definitions.yaml', self.ravenfall_loc_strings_path)

        self.max_dungeon_hp: int = 0
        self.current_mult: float | None = None

        self.global_restart_lock: asyncio.Lock = manager.global_restart_lock
        self.channel_restart_lock: asyncio.Lock = asyncio.Lock()
        self.channel_post_restart_lock: asyncio.Lock = asyncio.Lock()
        self.monitoring_paused = False

        self.restart_task: RFRestartTask | None = None
        
        # Monitoring state
        self.monitoring_paused = config.get('pause_monitoring', False)
        self.is_monitoring = False  # Whether we're currently monitoring a command
        self.current_monitor = None  # Current monitor info if monitoring
        self.ravenbot_restart_attempts = {'count': 0, 'last_attempt': 0}  # Track restart attempts
        self.ravenfall_restart_attempts = 0
        self.restart_future = None  # Current restart future if any

        self.cooldowns: Cooldown = Cooldown()

        self.middleman_connection_status: middleman.ConnectionStatus = middleman.ConnectionStatus()
        
        self.island_arrivals = defaultdict(list)  # island name -> list of names
        self.island_last_arrival_time = defaultdict(lambda: 0)  # island name -> last arrival timestamp
        
        self.update_events_routine_first_iteration = True
        self.scroll_queue: deque[QueuedScroll] = deque()
        
    async def save_scroll_queue(self):
        encoded = []
        for item in self.scroll_queue:
            encoded.append({
                "scroll": item.scroll.value,
                "reward_id": item.reward_id,
                "reward_redemption_id": item.reward_redemption_id,
                "user_id": item.user_id,
                "credits_spent": item.credits_spent
            })
        async with get_async_session() as session:
            await db_utils.update_scroll_queue(session, self.channel_id, encoded)
    
    async def load_scroll_queue(self):
        encoded = []
        async with get_async_session() as session:
            encoded = await db_utils.get_scroll_queue(session, self.channel_id) or []
        decoded = []
        for item in encoded:
            decoded.append(QueuedScroll(
                scroll=ScrollType(item['scroll']),
                reward_id=item['reward_id'],
                reward_redemption_id=item['reward_redemption_id'],
                user_id=item['user_id'],
                credits_spent=item['credits_spent']
            ))
        self.scroll_queue = deque(decoded)
        
    async def start(self):
        if self.monitoring_paused:
            return
        
        await self.load_scroll_queue()        

        await self.chat.join_room(self.channel_name)
        self.update_mult_routine.start()
        self.update_events_routine.start()
        self.backup_state_data_routine.start(instant=False)
        self.auto_restart_routine.start()
        self.dungeon_killswitch_routine.start()
        self.update_middleman_connection_status_routine.start()
        self.town_level_notification_routine.start()
        self.island_arrival_grouping_routine.start()
        self.scroll_queue_routine.start()

    async def stop(self):
        self.update_mult_routine.cancel()
        self.update_events_routine.cancel()
        self.backup_state_data_routine.cancel()
        self.auto_restart_routine.cancel()
        self.dungeon_killswitch_routine.cancel()
        self.update_middleman_connection_status_routine.cancel()
        self.town_level_notification_routine.cancel()
        self.island_arrival_grouping_routine.cancel()
        self.scroll_queue_routine.cancel()

    async def send_chat_message(self, message: str, ignore_error: bool = False, reply_id: Optional[str] = None):
        try:
            await self.chat.send_message(self.channel_name, message, reply_id=reply_id)
            await self.monitor_ravenfall_command(content=message)
        except Exception as e:
            if not ignore_error:
                raise e
            else:
                logger.warning(f"Failed to send chat message for {self.channel_name}: {e}")
    
    async def send_announcement(self, message: str, color: str = None, ignore_error: bool = True):
        try:
            await self.chat.twitch.send_chat_announcement(self.channel_id, os.getenv("BOT_USER_ID"), message, color)
        except Exception as e:
            if not ignore_error:
                raise e
            else:
                logger.warning(f"Failed to send announcement for {self.channel_name}: {e}")

    async def send_message_as_ravenbot(self, text: str, cid: str):
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

    async def event_twitch_message(self, message: ChatMessage):
        await self.twitch_message_waiter.process_message(message)
        if not self.monitoring_paused:
            await self.monitor_ravenfall_command(message=message)
        stripped_text = message.text.strip()
        if not stripped_text:
            return
        command = None
        arguments = []
        if stripped_text[0] in self.ravenbot_prefixes:
            parts = stripped_text[1:].split()
            command = parts[0].lower()
            if len(parts) > 1:
                arguments = parts[1:]
        if message.first and command == "join":
            await self.first_time_joiner(message)
        if command == "inspect":
            response = await send_to_server_and_wait_response(
                self.middleman_connection_id,
                RavenBotTemplates.inspect(
                    username=arguments[0] if arguments else message.user.name,
                ),
                message.id,
                timeout=3
            )
            if not response.get('timeout', False):
                await send_to_client(self.middleman_connection_id, json.dumps(response['responses'][0]))
            else:
                is_inspecting_other = len(arguments) > 0 and arguments[0].lower() != message.user.name.lower()
                if not is_inspecting_other:
                    await self.send_chat_message(
                        "You are not currently playing. Use !join to start playing!",
                        reply_id=message.id
                    )
                else:
                    await self.send_chat_message(
                        "The user you are trying to inspect is not currently playing.",
                        reply_id=message.id
                    )

    
    async def event_ravenbot_message(self, message: RavenBotMessage):
        await self.ravenbot_waiter.process_message(message)

    async def event_ravenfall_message(self, message: RavenfallMessage):
        await self.ravenfall_waiter.process_message(message)

    # Messages from ravenbot
    async def process_ravenbot_message(self, message: RavenBotMessage, metadata: MessageMetadata):
        platform_id = message['Sender']['PlatformId']
        if not platform_id:
            if message['Identifier'] in ('observe', 'travel'):
                message['Sender'] = await self.get_sender_data(message['Sender']['Username'].lower())
                if message['Sender']['PlatformId'] is not None:
                    logger.info(f"Replaced sender data for {message['Sender']['Username']}")
            return message

        # sometimes "server-request" is the platform ID
        if not platform_id.isdigit():
            return message

        asyncio.create_task(self.record_user(
            int(platform_id),
            message['Sender']['Username'],
            message['Sender']['Color'],
            message['Sender']['DisplayName']
        ))
        asyncio.create_task(self.record_sender_data(message['Sender']))

        if message['Identifier'] == 'task':
            asyncio.create_task(self.fetch_training(message['Sender']['Username'], wait_first=True))
        elif message['Identifier'] == 'leave':
            await self.fetch_training(message['Sender']['Username'])
        elif message['Identifier'] == 'inspect':
            return None
            
        return message

    # Messages from ravenfall
    async def process_ravenfall_message(self, message: RavenfallMessage, metadata: MessageMetadata):
        # Make sure session data messages and other things are not processed
        if message['Identifier'] != 'message':
            return message
        match = self.rfloc.identify_string(message['Format'])
        key = ""
        if match is not None:
            key = match.key
        message_args = message['Args']
        additional_args = {}
        if message['Recipent']['Platform'].lower() == 'system':
            if key in (
                "dungeon_spawned", "dungeon_auto_joined", "dungeon_auto_joined_count", "dungeon_countdown",
                "raid_start", "raid_auto_joined", "raid_auto_joined_count", 
                "multiplier_ends", "multiplier_ended"):
                return {'block': True}
        if message['Recipent']['Platform'].lower() == 'twitch':
            asyncio.create_task(self.record_character(
                message['Recipent']['CharacterId'],
                message['Recipent']['PlatformUserName'],
                message['Recipent']['PlatformId'],
            ))
            asyncio.create_task(self.process_auto_raid_sessionless(message.copy(), key))
            if key == "join_welcome":
            #     # Auto raid is already handled in process_auto_raid_sessionless
            #     asyncio.create_task(self.restore_sailor(message['Recipent']['PlatformUserName']))
                asyncio.create_task(self.fetch_training(message['Recipent']['PlatformUserName'], wait_first=True))
            elif key in ("village_boost", "village_boost_no_boost"):
                town_level, exp_left = message_args[0], message_args[1]
                level_exp = experience_for_level(town_level+1)
                additional_args['requiredExp'] = level_exp
                additional_args['currentExp'] = (level_exp - exp_left)
                additional_args['levelPercent'] = f"{(level_exp - exp_left) / level_exp:.2%}"
            elif key == "ferry_arrived":
                user = message['Recipent']['PlatformUserName']
                destination = message_args[0]
                t = time.monotonic()
                self.island_arrivals[destination].append(user)
                self.island_last_arrival_time[destination] = t
                return {'block': True}
            elif key == "loot":
                loots = [x.strip() for x in message['Format'].split(". ")]
                if len(loots) > 3:
                    paste_out = []
                    paste_out.append(f"Loot gained by {message['Recipent']['PlatformUserName']} ({datetime.now(timezone.utc).strftime('%d %B %Y %H:%M:%S UTC')})")
                    paste_out.append("")
                    paste_out.extend(loots)
                    paste_url = await utils.upload_to_pastes('\n'.join(paste_out))
                    await self.send_chat_message(f"{', '.join(loots[:3])} ✦ More: {paste_url}", reply_id=message['CorrelationId'])
                else:
                    await self.send_chat_message(f"{', '.join(loots)}", reply_id=message['CorrelationId'])
                return {'block': True}
        if self.ravenfall_loc_strings_path:
            trans_str = self.rfloc.translate_string(message['Format'], message['Args'], match, additional_args).strip()
            if len(trans_str) == 0:
                return {'block': True}
            max_length = 500
            recipient_name = message['Recipent']['PlatformUserName']
            max_length -= len(recipient_name) + 2
            trans_strs = split_by_utf16_bytes(trans_str, max_length)
            if len(trans_strs) > 1:
                asyncio.create_task(self.send_split_msgs(message, trans_strs))
                return {'block': True}
            message['Format'] = trans_strs[0]
            message['Args'] = []
        return message
    
    async def record_character(
        self, 
        char_id: str, 
        username: str, 
        twitch_id: int, 
        name_tag_color: str = None,
        display_name: str = None
    ):
        async with get_async_session() as session:
            if name_tag_color and len(name_tag_color) != 7:
                name_tag_color = None
            user, character = await db_utils.record_character_and_user(
                session=session,
                character_id=char_id,
                twitch_id=twitch_id,
                user_name=username,
                name_tag_color=name_tag_color,
                display_name=display_name
            )


    async def build_sender_from_character_id(self, char_id: str, session: AsyncSession = None, default_username: str = None) -> Optional[Dict[str, Any]]:
        """
        Build a Sender dictionary from a character ID by looking up user and character data.
        
        Args:
            char_id: The character ID to look up
            session: Optional database session to use. If not provided, a new session will be created.
            default_username: Default username to use if not found in the database
            
        Returns:
            Dictionary suitable for SenderBuilder or None if character not found
        """
        async def _build_sender(session: AsyncSession):
            result = await session.execute(
                select(Character, User)
                .where(Character.id == char_id)
                .join(User, Character.twitch_id == User.twitch_id, isouter=True)
            )
            row = result.one_or_none()
            
            if not row:
                if default_username:
                    return SenderBuilder(
                        username=default_username,
                        display_name=default_username,
                        platform_id=""
                    ).build()
                return None
                
            character, user = row
            username = default_username or character.id  # Fallback to character ID if no default provided
            display_name = user.display_name if user else username
            
            return SenderBuilder(
                username=username,
                display_name=display_name,
                platform_id=str(character.twitch_id) if character.twitch_id else "",
                color=user.name_tag_color if user else None,
            ).build()
            
        if session is not None:
            return await _build_sender(session)
            
        async with get_async_session() as session:
            return await _build_sender(session)

    async def record_user(
        self, 
        twitch_id: int, 
        user_name: str, 
        name_tag_color: str,
        display_name: Optional[str] = None
    ):
        async with get_async_session() as session:
            await db_utils.record_user(
                session=session,
                user_name=user_name,
                name_tag_color=name_tag_color,
                twitch_id=twitch_id,
                display_name=display_name
            )

    async def record_sender_data(self, sender_json: dict):
        async with get_async_session() as session:
            await db_utils.record_sender_data(session, "twitch", self.channel_id, sender_json)

    async def get_sender_data(self, user_name: str):
        async with get_async_session() as session:
            return await db_utils.get_formatted_sender_data(session, self.channel_id, user_name)

    async def send_split_msgs(self, message: RavenfallMessage, msgs: list[str]):
        for msg in msgs:
            message['Format'] = msg
            message['Args'] = []
            await send_to_client(self.middleman_connection_id, json.dumps(message))
            await asyncio.sleep(0.1)

    async def get_town_boost(self) -> List[TownBoost] | None:
        village: Village = await self.get_query("select * from village")
        if not village:
            return []
        split = village['boost'].split()
        if len(split) < 2:
            return []
        boost_stat = split[0]
        boost_value = float(split[1].rstrip("%"))
        return [TownBoost(boost_stat, boost_value/100)]
    
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
            if (message is not None) and self.dungeon['enemies'] < 48:
                await message.reply(f"(Loading) Game is busy preparing a new dungeon... ({self.dungeon['enemies']}/49) (Use !event to check progress)")
        else:
            if message is None:
                resend_text = content
            else:
                resend_text = message.text if message.user.id == os.getenv("BOT_USER_ID") else None
            self.monitor_ravenbot_response(command, timeout=timeout, resend_text=resend_text)
                        
    async def wait_for_ravenbot_command(self, command: str, timeout: float = 10.0) -> Optional[RavenBotMessage]:
        return await self.ravenbot_waiter.wait_for_command(command, timeout=timeout)
        
    async def wait_for_ravenfall_format(self, format_str: str, timeout: float = 10.0) -> Optional[RavenfallMessage]:
        return await self.ravenfall_waiter.wait_for_format_match(format_str, timeout=timeout)

    async def get_query(self, query: str, timeout: int = 5, suppress_timeout_error: bool = False) -> Any:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            try:
                r = await session.get(f"{self.rf_query_url}/{query}")
            except asyncio.TimeoutError:
                if not suppress_timeout_error:
                    logger.error(f"Timeout fetching Ravenfall query from {self.rf_query_url}")
                return None
            except aiohttp.ClientConnectorError as e:
                logger.error(f"Error fetching Ravenfall query from {self.rf_query_url}: {e}")
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

    async def update_boost(self):
        village: Village = await self.get_query("select * from village")
        if village is None:
            return False
        if len(village['boost'].strip()) <= 0:
            return True
        split = village['boost'].split()
        boost_stat = split[0]
        boost_value = float(split[1].rstrip("%"))
        msg = f"{self.ravenbot_prefixes[0]}town {boost_stat.lower()}"
        await self.send_chat_message(msg)
        return True

    @routine(delta=timedelta(seconds=3), max_attempts=99999)
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
        if multiplier['multiplier'] > self.current_mult:
            msg = f"{multiplier['eventname']} increased the multiplier to {int(multiplier['multiplier'])}x, ending in {format_seconds(multiplier['timeleft'], TimeSize.MEDIUM_SPACES)}!"
            await self.send_chat_message(msg)
        elif multiplier['multiplier'] < self.current_mult and multiplier['multiplier'] == 1:
            msg = self.rfloc.s("The exp multiplier has expired.")
            await self.send_chat_message(msg)
        
        self.current_mult = multiplier['multiplier']

    @routine(delta=timedelta(seconds=1), wait_first=True, max_attempts=99999)
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
            dungeon_name = "DUNGEON"
            if dungeon.get("name", None):
                dungeon_name = f"DUNGEON: {dungeon['name']}"
                
            if not dungeon['started']:
                self.max_dungeon_hp = dungeon["boss"]["health"]
                time_starting = format_seconds(dungeon['secondsuntilstart'])
                if dungeon['boss']['health'] > 0:
                    event_text = (
                        f"{dungeon_name} starting in {time_starting} – "
                        f"Boss HP: {dungeon['boss']['health']:,} – "
                        f"Enemies: {dungeon['enemies']:,} – "
                        f"Players: {dungeon['players']:,}"
                    )
                    event = RFChannelEvent.DUNGEON
                    sub_event = RFChannelSubEvent.DUNGEON_READY
                else:
                    event_text = (
                        f"{dungeon_name} is being prepared... – "
                        f"Enemies: {dungeon['enemies']:,}/49"
                    )
                    event = RFChannelEvent.DUNGEON
                    sub_event = RFChannelSubEvent.DUNGEON_PREPARE
            else:
                if dungeon['enemiesalive'] > 0 or self.max_dungeon_hp < dungeon['boss']['health']:
                    self.max_dungeon_hp = dungeon["boss"]["health"]
                boss_max_hp = max(1, self.max_dungeon_hp)
                event_text = (
                    f"{dungeon_name} – "
                    f"Boss HP: {dungeon['boss']['health']:,}/{boss_max_hp:,} "
                    f"({dungeon['boss']['health']/boss_max_hp:.1%}) – "
                    f"Enemies: {dungeon['enemiesalive']:,}/{dungeon['enemies']:,} – "
                    f"Players: {dungeon['playersalive']:,}/{dungeon['players']:,} – "
                    f"Elapsed time: {format_seconds(dungeon['elapsed'])} – "
                    f"Time limit: {format_seconds(MAX_DUNGEON_LENGTH)}"
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
        if not self.update_events_routine_first_iteration:
            asyncio.create_task(self.game_event_notification(sub_event, old_sub_event))
            asyncio.create_task(self.game_event_muted_ravenbot_notification(sub_event, old_sub_event))
        asyncio.create_task(self.game_event_wake_ravenbot(sub_event))
        asyncio.create_task(self.game_event_fetch_auto_raids(old_sub_event, sub_event))
        
        self.update_events_routine_first_iteration = False
    
    @routine(delta=timedelta(seconds=46), max_attempts=99999)
    async def town_level_notification_routine(self):
        if not self.town_level_notifications:
            return
        if self.channel_restart_lock.locked():
            async with self.channel_restart_lock:
                return

        village: Village = await self.get_query("select * from village")
        if not village:
            return

        if village['level'] == self.town_level:
            return
        
        old_town_level = self.town_level
        self.town_level = village['level']

        if old_town_level <= 1 or self.town_level <= old_town_level:
            return

        if self.town_level % 10 == 0:
            await self.update_boost()
            await asyncio.sleep(10)

        await self.send_chat_message(
            f"Town level is now {self.town_level}!"
        )
        

    async def game_event_notification(self, sub_event: RFChannelSubEvent, old_sub_event: RFChannelSubEvent):
        if self.event_notifications:
            if old_sub_event != RFChannelSubEvent.RAID and sub_event == RFChannelSubEvent.RAID:
                await asyncio.sleep(2)
                msg = (
                    f"RAID – "
                    f"Boss HP: {self.raid['boss']['health']:,} "
                )
                await self.send_chat_message(msg)
            elif old_sub_event != RFChannelSubEvent.DUNGEON_STARTED and sub_event == RFChannelSubEvent.DUNGEON_STARTED:
                await asyncio.sleep(2)
                msg = (
                    f"DUNGEON – "
                    f"Boss HP: {self.max_dungeon_hp:,} – "
                    f"Enemies: {self.dungeon['enemies']:,} – "
                    f"Players: {self.dungeon['players']:,}"
                )
                await self.send_chat_message(msg)

    async def game_event_muted_ravenbot_notification(self, sub_event: RFChannelSubEvent, old_sub_event: RFChannelSubEvent):
        if old_sub_event != RFChannelSubEvent.DUNGEON_READY and sub_event == RFChannelSubEvent.DUNGEON_READY:
            await asyncio.sleep(2)
            players = self.dungeon['players']
            dungeon_name = self.dungeon['name']
            if not dungeon_name:
                dungeon_name = "A dungeon"
            if players == 0:
                await self.send_chat_message(f"{dungeon_name} is available!")
            else:
                await self.send_chat_message(f"{dungeon_name} is available! {utils.pl2(players, 'player has', 'players have')} joined.")
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

    @routine(delta=timedelta(seconds=30), wait_first=True, max_attempts=99999)
    async def dungeon_killswitch_routine(self):
        if not self.sub_event in (RFChannelSubEvent.DUNGEON_STARTED, RFChannelSubEvent.DUNGEON_BOSS):
            return
        if not self.dungeon:
            return
        if self.dungeon.get('elapsed', 0) > MAX_DUNGEON_LENGTH:
            await self.send_chat_message(f"{self.ravenbot_prefixes[0]}dungeon stop")

    @routine(delta=timedelta(hours=5), wait_first=True, max_attempts=99999)
    async def backup_state_data_routine(self, instant: bool = True):
        while self.channel_restart_lock.locked() or self.channel_post_restart_lock.locked():
            await asyncio.sleep(5)
        locked_global_resync = False
        if self.manager.ravennest_is_online:
            if not instant:
                await self.manager.global_resync_lock.acquire()
                locked_global_resync = True
            try:
                r = await send_multichat_command(
                    text="?resync",
                    user_id=self.channel_id,
                    user_name=self.channel_name,
                    channel_id=self.channel_id,
                    channel_name=self.channel_name
                )
                if r['status'] != 200:
                    await self.send_chat_message("?resync")
            except Exception as e:
                if locked_global_resync:
                    self.manager.global_resync_lock.release()
                raise e
            await asyncio.sleep(15)
        try:
            await backup_file_with_date_async(
                f"{os.getenv('RAVENFALL_SANDBOXED_FOLDER').replace('{box}', self.sandboxie_box).rstrip('\\/')}\\state-data.json",
                int(os.getenv('STATE_DATA_BACKUP_RETENTION_COUNT'))
            )
            logger.info(f"Backed up state data for {self.channel_name}")
        except Exception as e:
            logger.error(f"Failed to backup state data for {self.channel_name}: {e}")
        finally:
            if locked_global_resync:
                await asyncio.sleep(60 - 15)
                self.manager.global_resync_lock.release()

    @routine(delta=timedelta(seconds=1), max_attempts=99999)
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
        
    @routine(delta=timedelta(seconds=1), max_attempts=99999)
    async def scroll_queue_routine(self):
        if self.update_events_routine_first_iteration:
            return
        if self.channel_restart_lock.locked():
            async with self.channel_restart_lock:
                pass
            return
        if self.channel_post_restart_lock.locked():
            async with self.channel_post_restart_lock:
                pass
            return
        if self.restart_task and (not self.restart_task.finished()) and (self.restart_task.get_time_left() < WARNING_MSG_TIMES[0][0] + 5):
            return
        if self.event != RFChannelEvent.NONE:
            return
        if len(self.scroll_queue) == 0:
            return
        scrolls = await get_scroll_counts(self.channel_id)
        stock = 0
        name = ''
        command = ''
        expected_event = RFChannelEvent.NONE
        next_scroll = self.scroll_queue[0]
        if next_scroll.scroll == ScrollType.RAID:
            stock = scrolls['data']['channel']['Raid Scroll']
            name = 'raid'
            command = '?rs'
            expected_event = RFChannelEvent.RAID
        elif next_scroll.scroll == ScrollType.DUNGEON:
            stock = scrolls['data']['channel']['Dungeon Scroll']
            name = 'dungeon'
            command = '?ds'
            expected_event = RFChannelEvent.DUNGEON
        else:
            return
        if stock <= 0:
            logging.info(f"Out of {name} scrolls! Skipping queue entry...")
            return
        await send_multichat_command(command, "0", self.channel_name, self.channel_id, self.channel_name)
        for _ in range(30):
            await asyncio.sleep(1)
            if self.event == expected_event:
                self.scroll_queue.popleft()
                await self.save_scroll_queue()
                if next_scroll.reward_id:
                    await self.twitch.update_redemption_status(
                        self.channel_id,
                        next_scroll.reward_id,
                        next_scroll.reward_redemption_id,
                        CustomRewardRedemptionStatus.FULFILLED
                    )
                return
        logging.warning(f"Scroll queue: Expected event {expected_event} did not occur")
        
    def get_scroll_queue_length(self):
        return len(self.scroll_queue)
    
    def get_scroll_count_in_queue(self, scroll: Literal['dungeon', 'raid']):
        scroll_id = ScrollType.NONE
        if scroll == 'dungeon':
            scroll_id = ScrollType.DUNGEON
        elif scroll == 'raid':
            scroll_id = ScrollType.RAID
        else:
            raise ValueError("Scroll must be 'dungeon' or 'raid")
        count = 0
        for item in self.scroll_queue:
            if item.scroll == scroll_id:
                count += 1
        return count
    
    async def add_scroll_to_queue(self, scroll: Literal['dungeon', 'raid'], redeem_ctx: TwitchRedeemContext = None, user_id: str = None, credits_spent: int = 0):
        scrolls = await get_scroll_counts(self.channel_id)
        stock = 0
        scroll_id = ScrollType.NONE
        if scroll == 'dungeon':
            stock = scrolls['data']['channel']['Dungeon Scroll']
            scroll_id = ScrollType.DUNGEON
        elif scroll == 'raid':
            stock = scrolls['data']['channel']['Raid Scroll']
            scroll_id = ScrollType.RAID
        else:
            raise ValueError("Scroll must be 'dungeon' or 'raid")
        amount_in_queue = self.get_scroll_count_in_queue(scroll)
        if amount_in_queue >= stock:
            raise OutOfStockError(amount_in_queue, stock, f"Out of {scroll.capitalize()} scrolls!")
        if redeem_ctx:
            queue_obj = QueuedScroll(scroll_id, redeem_ctx.redemption.reward.id, redeem_ctx.redemption.id, user_id, credits_spent)
        else:
            queue_obj = QueuedScroll(scroll_id, None, None, user_id, credits_spent)
        self.scroll_queue.append(queue_obj)
        await self.save_scroll_queue()
        
    async def remove_scrolls_from_queue(self, start_pos: int):
        while len(self.scroll_queue) > start_pos:
            item = self.scroll_queue.pop()
            if item.reward_id:
                await self.twitch.update_redemption_status(
                    self.channel_id,
                    item.reward_id,
                    item.reward_redemption_id,
                    CustomRewardRedemptionStatus.CANCELED
                )
            if item.credits_spent != 0:
                async with get_async_session() as session:
                    await db_utils.add_credits(session, item.user_id, item.credits_spent, "Scroll refund")
        await self.save_scroll_queue()

        
    # --- [ AUTO RESTART ] ---------------------------------------

    @routine(delta=timedelta(seconds=20), max_attempts=99999)
    async def auto_restart_routine(self):
        if self.channel_restart_lock.locked():
            async with self.channel_restart_lock:
                return
        game_session: GameSession = await self.get_query("select * from session", 5)
        uptime = None
        if game_session:
            uptime = game_session['secondssincestart']
        if uptime is None:
            logger.warning(f"{self.channel_name} seems to be offline...")
            self.queue_restart(2, label="Ravenfall may have crashed...", reason=RestartReason.UNRESPONSIVE)
            return

        if not self.auto_restart:
            return
        if not self.restart_period or self.restart_period <= 0:
            return
        if self.restart_task and not self.restart_task.finished():
            return
        period = max(20*60,self.restart_period)
        seconds_to_restart = max(60, period - uptime)
        self.queue_restart(seconds_to_restart, label="Scheduled restart", reason=RestartReason.AUTO, overwrite_same_reason=True)

    async def _ravenfall_pre_restart(self):
        try:
            await self.fetch_all_training()
        except TypeError:  # Expected error: 'NoneType' object is not iterable
            logger.warning("Pre-restart: Ravenfall is offline, skipping")
            return
        r = await send_multichat_command(
            text="?randleave",
            user_id=self.channel_id,
            user_name=self.channel_name,
            channel_id=self.channel_id,
            channel_name=self.channel_name
        )
        if r['status'] != 200:
            await self.send_chat_message("?randleave")

    async def _restart_ravenfall(
        self, 
        run_pre_restart: bool = True, 
        run_post_restart: bool = True,
        silent: bool = False,
        *, 
        reset_attempts: bool = True,
        restart_task: RFRestartTask = None
    ):
        if reset_attempts:
            self.ravenfall_restart_attempts = 0
        self.ravenfall_restart_attempts += 1
        if run_pre_restart:
            await self._ravenfall_pre_restart()
            
        await self.channel_restart_lock.acquire()
        if not silent and self.global_restart_lock.locked():
            await self.send_chat_message("Waiting for other restarts to finish...")
        await self.global_restart_lock.acquire()
        logger.info(f"Restarting Ravenfall for {self.channel_name}")
        if not silent:
            msg = "Restarting Ravenfall..."
            if restart_task and (not restart_task.sent_reason) and restart_task.label:
                msg += f" Reason: {restart_task.label}"
            await self.send_chat_message(msg)
        code, text = await restart_process(
            self.sandboxie_box, 
            "Ravenfall.exe", 
            f"cd {os.getenv('RAVENFALL_FOLDER')} & {self.ravenfall_start_script}"
        )
        if code != 0:
            logger.error(f"Failed to restart Ravenfall for {self.channel_name}: code {code}, text {text}")
            if not silent:
                await self.send_chat_message("Failed to restart Ravenfall!")
            self.channel_restart_lock.release()
            self.global_restart_lock.release()
            return False

        await asyncio.sleep(5)
        start_time = time.time()
        auth_timeout = self.restart_timeout
        authenticated = False
        
        while time.time() - start_time < auth_timeout:
            session: GameSession = await self.get_query("select * from session", 1, suppress_timeout_error=True)
            if session and session.get('authenticated', False):
                authenticated = True
                break
            await asyncio.sleep(1)
        if not authenticated:
            logger.error(f"Failed to authenticate Ravenfall for {self.channel_name}")
            self.channel_restart_lock.release()
            self.global_restart_lock.release()
            if self.ravenfall_restart_attempts % 3 == 2:
                await self.send_chat_message(f"Restart failed, retrying in 2 minutes")
                await asyncio.sleep(120)
            else:
                await self.send_chat_message(f"Restart failed, retrying in 20 seconds")
                await asyncio.sleep(20)
            return self._restart_ravenfall(False, run_post_restart, silent, reset_attempts=False)
            # await self.send_chat_message(f"Restart failed (pinging @{os.getenv('OWNER_TWITCH_USERNAME', 'abrokecube')})")
            # return False
        # if not silent:
        #     await self.send_chat_message("Ravenfall has been restarted.")
        if self.manager.middleman_power_saving and self.manager.middleman_connected:
            await middleman.force_reconnect(self.middleman_connection_id, 60)
        logger.info(f"Restarted Ravenfall for {self.channel_name}")

        if run_post_restart:
            await self.channel_post_restart_lock.acquire()
            self.channel_restart_lock.release()
            self.global_restart_lock.release()
            try:
                await self._ravenfall_post_restart()
            except Exception as e:
                logger.error(f"Failed to run post restart for {self.channel_name}: {e}")
            self.channel_post_restart_lock.release()
        else:
            self.channel_restart_lock.release()
            self.global_restart_lock.release()

        return True

    async def _ravenfall_post_restart(self):
        # Wait for the game to start rejoining players
        start_time = time.time()
        while True:
            if time.time() - start_time > self.restart_timeout:
                logger.warning(f"Timed out waiting for players to join after restart in {self.channel_name}")
                await self.send_chat_message(f"Players did not join back @{os.getenv('OWNER_TWITCH_USERNAME', 'abrokecube')}")
                return
                
            await asyncio.sleep(1)
            session: GameSession = await self.get_query("select * from session", 5, suppress_timeout_error=True)
            if session['players'] > 0:
                break

        # Wait for the game to finish rejoining players
        player_count = 0
        while True:
            await asyncio.sleep(2)
            session: GameSession = await self.get_query("select * from session", 5, suppress_timeout_error=True)
            new_player_count = session['players']
            if player_count > 0 and new_player_count == player_count:
                break
            player_count = new_player_count

        r = await send_multichat_command(
            text="?undorandleave",
            user_id=self.channel_id,
            user_name=self.channel_name,
            channel_id=self.channel_id,
            channel_name=self.channel_name
        )
        if r['status'] != 200:
            await self.send_chat_message("?undorandleave")
        # if not self.manager.middleman_connected:
        #     r = await send_multichat_command(
        #         text="?sailall",
        #         user_id=self.channel_id,
        #         user_name=self.channel_name,
        #         channel_id=self.channel_id,
        #         channel_name=self.channel_name
        #     )
        #     if r['status'] != 200:
        #         await self.send_chat_message("?sailall")
        # else:
            # await self.restore_sailors()
            # await self.restore_auto_raids()
        if self.manager.middleman_connected and self.auto_restore_raids:
            await self.restore_auto_raids()

    async def restart_ravenbot(self):
        await restart_process(
            self.sandboxie_box, "RavenBot.exe", f"cd {os.getenv('RAVENBOT_FOLDER')} & start RavenBot.exe"
        )

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
            if current_time - self.ravenbot_restart_attempts['last_attempt'] > RETRY_WINDOW:
                self.ravenbot_restart_attempts['count'] = 0
            
            self.ravenbot_restart_attempts['count'] += 1
            self.ravenbot_restart_attempts['last_attempt'] = current_time
            attempts = self.ravenbot_restart_attempts['count']
            attempts_remaining = MAX_RETRIES - attempts
            
            resp_retry = "Hmm , let me restart RavenBot..."
            resp_retry_2 = "Hmm , let me restart RavenBot again.."
            resp_user_retry = "Okay , try again"
            resp_user_retry_2 = "Okay , try again, surely this time it will work"
            resp_restart_ravenfall = "okie then i will restart Ravenfall, please hold..."
            resp_giveup = f"I give up, please try again later (pinging @{os.getenv('OWNER_TWITCH_USERNAME', 'abrokecube')})"

            if resend_text:
                resp_retry = "Hmm"
                resp_retry_2 = "Hmm ..."
                resp_user_retry = resend_text
                resp_user_retry_2 = resend_text
                resp_restart_ravenfall = "Hmm ........."
                resp_giveup = f"I give up @{os.getenv('OWNER_TWITCH_USERNAME', 'abrokecube')} dinkDonk"
            
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
                    label="Ravenfall is not responding",
                    reason=RestartReason.UNRESPONSIVE
                )
                await restart_task.wait()
                await self.send_chat_message(resp_user_retry_2)
                asked_to_retry = True
            else:
                await self.send_chat_message(resp_giveup)
        else:
            logger.debug(f"Received a response to command: {command} in {self.channel_name}")

        self.is_monitoring = False
        self.current_monitor = None
        if asked_to_retry and resend_text:
            self.monitor_ravenbot_response(command, timeout, resend_text)

    def monitor_ravenbot_response(self, command: str, timeout: float = 10.0, resend_text: str = None):
        asyncio.create_task(self._monitor_ravenbot_response_task(command, timeout, resend_text))

    def queue_restart(self, time_to_restart: int | None = None, mute_countdown: bool = False, label: str = "", reason: RestartReason | None = None, overwrite_same_reason: bool = False):
        if self.monitoring_paused and reason != RestartReason.USER:
            logger.error(f"Not queuing restart for {self.channel_name} because monitoring is paused.", exc_info=True)
            return
        allow_overwrite = True
        if self.restart_task:
            if self.restart_task.reason == reason:
                allow_overwrite = overwrite_same_reason
            if self.restart_task.finished():
                allow_overwrite = True
        if not allow_overwrite:
            return
        if self.restart_task:
            self.restart_task.cancel()
        self.restart_task = RFRestartTask(self, self.manager, time_to_restart, mute_countdown, label, reason)
        self.restart_task.start()
        logger.info(f"Restart task queued for {self.channel_name} with label {label} in {format_seconds(time_to_restart, TimeSize.SMALL, 2, False)}.")
        return self.restart_task

    def postpone_restart(self, seconds: int):
        if self.restart_task:
            self.restart_task.postpone(seconds)
    
    def cancel_restart(self):
        if self.restart_task is None:
            return False
        self.restart_task.cancel()
        if self.channel_restart_lock.locked():
            self.channel_restart_lock.release()
            self.global_restart_lock.release()
        if self.channel_post_restart_lock.locked():
            self.channel_post_restart_lock.release()
        return True
    
    @routine(delta=timedelta(seconds=0.5), max_attempts=99999)
    async def island_arrival_grouping_routine(self):
        t = time.monotonic()
        for island, timestamp in self.island_last_arrival_time.items():
            if timestamp > 0 and t - timestamp >= 0.25:
                players = self.island_arrivals[island]
                if len(players) > 1:
                    player_names = utils.strjoin(', ', *[f'@{a}' for a in players], before_end=' and ')
                    await self.send_chat_message(f"{player_names} have arrived at {island}.")
                elif len(players) == 1:
                    await self.send_chat_message(f"@{players[0]} has arrived at {island}.")
                self.island_arrivals[island] = []
                self.island_last_arrival_time[island] = 0

    # --- [ AUTO RAID ] ---------------------------------------

    async def game_event_fetch_auto_raids(self, old_sub_event: RFChannelSubEvent, sub_event: RFChannelSubEvent):
        if old_sub_event != sub_event and sub_event == RFChannelSubEvent.RAID:
            await asyncio.sleep(2)
            await self.fetch_auto_raids()

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
                if self.auto_restore_raids:
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
        if not self.manager.middleman_connected:
            return
        chars: List[Player] = await self.get_query("select * from players")
        char_id_to_player = {char['id']: char for char in chars}
        
        async with get_async_session() as session:
            result = await session.execute(
                select(AutoRaidStatus)
                .where(
                    AutoRaidStatus.char_id.in_(char_id_to_player.keys()),
                    AutoRaidStatus.auto_raid_count != 2147483647
                )
            )
            auto_raids = result.scalars().all()
            
            for auto_raid in auto_raids:
                char_data = char_id_to_player.get(auto_raid.char_id)
                if not char_data:
                    continue
                    
                sender = await self.build_sender_from_character_id(
                    char_id=auto_raid.char_id,
                    session=session,
                    default_username=char_data['name']
                )
                
                if not sender:
                    continue
                    
                msg = RavenBotTemplates.auto_raid_status(sender)
                response = await send_to_server_and_wait_response(self.middleman_connection_id, msg)
                if response['success'] and response['responses']:
                    match = self.rfloc.identify_string(response['responses'][0]['Format'])
                    await self.process_auto_raid(session, response['responses'][0], match.key)
    
    async def restore_auto_raids(self):
        if not self.manager.middleman_connected:
            return
            
        chars: List[Player] = await self.get_query("select * from players")
        char_id_to_player = {char['id']: char for char in chars}
        logging.debug(f"Restoring auto raids for {len(char_id_to_player)} characters")
        
        async with get_async_session() as session:
            result = await session.execute(
                select(AutoRaidStatus)
                .where(AutoRaidStatus.char_id.in_(char_id_to_player.keys()))
            )
            auto_raids = result.scalars().all()
            
            for auto_raid in auto_raids:
                char_data = char_id_to_player.get(auto_raid.char_id)
                if not char_data:
                    continue
                    
                sender = await self.build_sender_from_character_id(
                    char_id=auto_raid.char_id,
                    session=session,
                    default_username=char_data['name']
                )
                
                if not sender:
                    continue
                    
                msg = RavenBotTemplates.auto_join_raid(sender, auto_raid.auto_raid_count)
                await send_to_server_and_wait_response(self.middleman_connection_id, msg)
    
    async def restore_auto_raid(self, session: AsyncSession, char_id: str, username: str):
        if not self.manager.middleman_connected:
            return
                    
        result = await session.execute(
            select(AutoRaidStatus)
            .where(AutoRaidStatus.char_id == char_id)
        )
        auto_raid = result.scalar_one_or_none()
        
        if auto_raid is not None:
            sender = await self.build_sender_from_character_id(char_id, session=session, default_username=username)
            if not sender:
                logging.debug(f"Could not build sender for character {char_id}")
                return
                
            logging.debug(f"Restoring auto raid for {sender.get('display_name', username)}")
            msg = RavenBotTemplates.auto_join_raid(sender, auto_raid.auto_raid_count)
            await send_to_server_and_wait_response(self.middleman_connection_id, msg)
        else:
            logging.debug(f"No auto raid found for {username}")   

    async def restore_sailors(self):
        if not self.manager.middleman_connected:
            return
        chars: List[Player] = await self.get_query("select * from players")
        char_id_to_player = {char['id']: char for char in chars}

        logging.debug(f"Restoring sailors for {len(chars)} characters")
        async with get_async_session() as session:
            result = await session.execute(
                select(Character)
                .where(Character.id.in_(char_id_to_player.keys()))
            )
            characters = result.scalars().all()

            for char in characters:
                char_data = char_id_to_player.get(char.id)
                if not char_data:
                    continue
                if char_data['training'] != "None":
                    continue
                if char.training != "Sailing":
                    continue
                sender = await self.build_sender_from_character_id(char.id, session=session, default_username=char_data['name'])
                if not sender:
                    logging.debug(f"Could not build sender for character {char.id}")
                    return
                msg = RavenBotTemplates.sail(sender)
                await send_to_server_and_wait_response(self.middleman_connection_id, msg)
    
    async def restore_sailor(self, username: str):
        if not self.manager.middleman_connected:
            return
        char: Player = await self.get_query(f"select * from players where name = '{username}'")
        if not char:
            return
        if char['training'] != "None":
            return
        async with get_async_session() as session:
            result = await session.execute(
                select(Character.training)
                .where(Character.id == char['id'])
            )
            training = result.scalar_one_or_none()
            if not training:
                return
            if training != "Sailing":
                return
            sender = await self.build_sender_from_character_id(char['id'], session=session)
            if not sender:
                logging.debug(f"Could not build sender for character {char['id']}")
                return
            msg = RavenBotTemplates.sail(sender)
            logging.debug(f"Restoring sailing for {username}")
            await send_to_server_and_wait_response(self.middleman_connection_id, msg)

    async def fetch_all_training(self):
        if not self.manager.middleman_connected:
            return
        chars: List[Player] = await self.get_query("select * from players")
        char_id_to_player = {char['id']: char for char in chars}
        
        async with get_async_session() as session:
            result = await session.execute(
                select(Character)
                .where(
                    Character.id.in_(char_id_to_player.keys()),
                )
            )
            characters = result.scalars().all()
            for char in characters:
                char_data = char_id_to_player.get(char.id)
                if not char_data:
                    continue
                training = char_data['training']
                if (not training) or training == "None":
                    if (not char_data['island']) or char_data['sailing']:
                        training = "Sailing"
                    else:
                        continue
                char.training = training
    
    async def fetch_training(self, username: str, wait_first: bool = False):
        if not self.manager.middleman_connected:
            return
        if wait_first:
            await asyncio.sleep(1)
        char: Player = await self.get_query(f"select * from players where name = '{username}'")
        if not char:
            return
        async with get_async_session() as session:
            result = await session.execute(
                select(Character)
                .where(Character.id == char['id'])
            )
            char_db = result.scalar_one_or_none()
            if not char_db:
                return
            training = char['training']
            if (not training) or training == "None":
                if (not char['island']) or char['sailing']:
                    training = "Sailing"
                else:
                    return
            char_db.training = training