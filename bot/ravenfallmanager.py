from typing import List, Dict
from .ravenfallchannel import RFChannel
from .models import GameMultiplier, RFMiddlemanMessage, RFChannelEvent, Village
from .multichat_command import send_multichat_command, get_desync_info, get_total_item_count
from . import middleman
from .messageprocessor import MessageProcessor, RavenMessage, MessageMetadata, ClientInfo
from twitchAPI.chat import Chat, ChatMessage
from ravenpy import RavenNest, ExpMult
import asyncio
import aiohttp
import logging
from utils.routines import routine
from utils.websocket_client import AutoReconnectingWebSocket
from datetime import timedelta, datetime, timezone
import time
from .ravenfallrestarttask import RestartReason
from utils.alert_monitor import BatchAlertMonitor
from .commands import Commands

import os

logger = logging.getLogger(__name__)

class RFChannelManager:
    def __init__(self, config: dict, chat: Chat, rfapi: RavenNest, bot: Commands):
        self.config = config
        self.rfapi = rfapi
        self.chat = chat
        self.bot = bot
        self.channels: List[RFChannel] = []
        self.channel_id_to_channel: Dict[str, RFChannel] = {}
        self.channel_name_to_channel: Dict[str, RFChannel] = {}
        self.ravennest_is_online = True
        self.global_multiplier = 1.0
        self.global_multiplier_last_change = datetime.now(timezone.utc)

        self.rf_message_processor: MessageProcessor | None = None
        self.global_restart_lock = asyncio.Lock()
        self.middleman_enabled = False
        self.middleman_power_saving = False
        self.middleman_connected = False
        self.middleman_processor_server_client_count = 0
        self.load_channels()

        self.global_resync_lock = asyncio.Lock()


    def load_channels(self):
        for channel in self.config:
            self.channels.append(RFChannel(channel, self))
        for channel in self.channels:
            self.channel_id_to_channel[channel.channel_id] = channel
            self.channel_name_to_channel[channel.channel_name] = channel    
        
    async def start(self):
        for channel in self.channels:
            await channel.start()
        self.mult_check_routine.start()
        self.resync_routine.start()
        self.update_boosts_routine.start()
        msg_processor_host = os.getenv("RF_MIDDLEMAN_PROCESSOR_HOST", None)
        msg_processor_port = os.getenv("RF_MIDDLEMAN_PROCESSOR_PORT", None)
        if msg_processor_host and msg_processor_port:
            self.rf_message_processor = MessageProcessor(
                host=msg_processor_host,
                port=msg_processor_port,
            )
            self.rf_message_processor.start()
            self.rf_message_processor.add_message_callback(self.handle_processor_message)
            self.rf_message_processor.add_connection_callback(self.on_processor_connect)
            self.rf_message_processor.add_disconnection_callback(self.on_processor_disconnect)
        else:
            logger.info("RF_MIDDLEMAN_PROCESSOR_HOST or RF_MIDDLEMAN_PROCESSOR_PORT not set, not starting message processor")
        await ItemAlertMonitor(self).start()

    async def stop(self):
        for channel in self.channels:
            await channel.stop()
        self.mult_check_routine.cancel()
        self.resync_routine.cancel()
        if self.rf_message_processor:
            await self.rf_message_processor.astop()

    async def event_twitch_message(self, message: ChatMessage):
        for channel in self.channels:
            if channel.channel_id == message.room.room_id:
                await channel.event_twitch_message(message)

    async def handle_processor_message(self, message: RavenMessage, metadata: MessageMetadata, client_info: ClientInfo):
        out_message = message.copy()
        for channel in self.channels:
            if metadata.connection_id == channel.middleman_connection_id:
                if not metadata.is_api:
                    if metadata.source.lower() == "client":
                        asyncio.create_task(channel.event_ravenbot_message(message))
                    elif metadata.source.lower() == "server":
                        asyncio.create_task(channel.event_ravenfall_message(message))
                    elif metadata.source.lower() in ("api-client", "api-server"):
                        pass
                    else:
                        logger.error(f"Unknown source: {metadata.source}")
                if metadata.source.lower() == "client":
                    out_message = await channel.process_ravenbot_message(message.copy(), metadata)
                elif metadata.source.lower() == "server":
                    out_message = await channel.process_ravenfall_message(message.copy(), metadata)
                elif metadata.source.lower() in ("api-client", "api-server"):
                    pass
                else:
                    logger.error(f"Unknown source: {metadata.source}")
                break
        else:
            logger.error(f"Unknown connection id: {metadata.connection_id}")
        return {"message": out_message}

    async def on_processor_connect(self, client_info: ClientInfo):
        self.middleman_connected = True
        self.middleman_enabled = True
        self.middleman_processor_server_client_count += 1
        serverconf, err = await middleman.get_config()
        if not err:
            self.middleman_power_saving = not serverconf['disableTimeout']

    async def on_processor_disconnect(self, client_info: ClientInfo):
        self.middleman_processor_server_client_count -= 1
        if self.middleman_processor_server_client_count <= 0:
            self.middleman_enabled = False

    def get_channel(self, *, channel_id: str | None = None, channel_name: str | None = None) -> RFChannel | None:
        if channel_id:
            if channel_id in self.channel_id_to_channel:
                return self.channel_id_to_channel[channel_id]
        elif channel_name:
            if channel_name in self.channel_name_to_channel:
                return self.channel_name_to_channel[channel_name]
        return None

    @routine(delta=timedelta(seconds=45), max_attempts=99999)
    async def mult_check_routine(self):
        now = datetime.now(timezone.utc)
        old_online = self.ravennest_is_online
        is_online = False
        multiplier: ExpMult | None = None
        attempts = 3
        while attempts > 0:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                try:
                    async with session.get(f"https://www.ravenfall.stream/api/game/exp-multiplier", ssl=False) as response:
                        data: GameMultiplier = await response.json()
                        if data:
                            is_online = True
                            if data["multiplier"] != self.global_multiplier:
                                self.global_multiplier = data["multiplier"]
                                self.global_multiplier_last_change = now
                            multiplier = ExpMult(**data)
                        break
                except Exception as e:
                    logger.error(f"Can't connect to Ravenfall API: {e}")
            attempts -= 1
        self.ravennest_is_online = is_online
        
        if self.ravennest_is_online != old_online:
            if self.ravennest_is_online:
                msg = "🟢 RavenNest is online!"
            else:
                msg = "🔴 RavenNest is offline!"
            for channel in self.channels:
                await channel.send_chat_message(msg)

        if not self.ravennest_is_online:
            return
        if multiplier.multiplier <= 1:
            return
        if (now - self.global_multiplier_last_change) < timedelta(minutes=1, seconds=30):
            return
        for channel in self.channels:
            if channel.channel_restart_lock.locked():
                continue
            if channel.monitoring_paused:
                continue
            if channel.multiplier['multiplier'] != self.global_multiplier:
                logger.debug(f"Multiplier mismatch for {channel.channel_name}: {channel.multiplier['multiplier']} != {self.global_multiplier}")
                if channel.restart_task and channel.restart_task.get_time_left() > 120:
                    channel.queue_restart(90, label="Town is desynced; multiplier is not updating", reason=RestartReason.MULTIPLIER_DESYNC)
                r = await send_multichat_command(
                    text=f"?say {channel.ravenbot_prefixes[0]}multiplier",
                    user_id=channel.channel_id,
                    user_name=channel.channel_name,
                    channel_id=channel.channel_id,
                    channel_name=channel.channel_name
                )
                if r['status'] != 200:
                    await channel.send_chat_message(f"?say {channel.ravenbot_prefixes[0]}multiplier")
    
    async def get_desync_info(self) -> Dict[str, float]:
        ch_desyncs: Dict[str, float] = {}
        data = await get_desync_info()
        if time.time() - data['data']['last_updated'] > 300:
            return ch_desyncs

        if data['status'] != 200:
            logger.error(f"Failed to fetch desync info: {data['error']}")
            return ch_desyncs
        for channel_id in self.channel_id_to_channel.keys():
            if channel_id in data['data']['towns']:
                channel_name = self.channel_id_to_channel[channel_id].channel_name
                ch_desyncs[channel_name] = data['data']['towns'][channel_id]
        return ch_desyncs

    async def get_total_item_count(self) -> Dict[str, float]:
        total_item_data: Dict[str, float] = {}
        data = await get_total_item_count()

        if data['status'] != 200:
            logger.error(f"Failed to fetch desync info: {data['error']}")
            return total_item_data
        for channel_id in self.channel_id_to_channel.keys():
            if channel_id in data['data']['towns']:
                channel_name = self.channel_id_to_channel[channel_id].channel_name
                total_item_data[channel_name] = data['data']['towns'][channel_id]
        return total_item_data

    @routine(delta=timedelta(seconds=60), max_attempts=99999)
    async def resync_routine(self):
        data = await self.get_desync_info()
        
        async def resync_task(channel: RFChannel):
            async with self.global_resync_lock:
                r = await send_multichat_command(
                    text="?resync",
                    user_id=channel.channel_id,
                    user_name=channel.channel_name,
                    channel_id=channel.channel_id,
                    channel_name=channel.channel_name
                )
                if r['status'] != 200:
                    await channel.send_chat_message("?resync")
                await asyncio.sleep(60)
                
        tasks = []                
        for channel_name, desync in data.items():
            if not self.ravennest_is_online:
                continue
            if abs(desync) < 30:  # 30 seconds
                continue
            channel = self.channel_name_to_channel[channel_name]
            if channel.monitoring_paused:
                continue
            if channel.event == RFChannelEvent.DUNGEON:
                continue
            if channel.channel_restart_lock.locked():
                continue
            if channel.channel_post_restart_lock.locked():
                continue
            tasks.append(resync_task(channel))
        if tasks:
            await asyncio.gather(*tasks)
    
    @routine(delta=timedelta(hours=3), wait_first=True, max_attempts=99999)
    async def update_boosts_routine(self):
        for channel in self.channels:
            if channel.channel_restart_lock.locked():
                async with channel.channel_restart_lock:
                    return
            while True:
                if not await channel.update_boost():
                    await asyncio.sleep(30)
                else:
                    break
            await asyncio.sleep(120)

class ItemAlertMonitor(BatchAlertMonitor):
    def __init__(self, rfmanager: RFChannelManager):
        super().__init__(interval=30, timeout=3*60, alert_interval=60*60, name='ItemAlertMonitor')
        self.rfmanager = rfmanager
        self.last_counts = {}
        
    async def check_condition(self):
        items = await self.rfmanager.get_total_item_count()
        alerts = {}
        for channel_name, item_count in items.items():
            last_count = self.last_counts.get(channel_name, item_count-1)
            self.last_counts[channel_name] = item_count
            is_alerting = (item_count == last_count)
            if is_alerting and self.rfmanager.ravennest_is_online:
                alerts[channel_name] = "No item gain"
            else:
                alerts[channel_name] = True
        return alerts
        
    async def trigger_alert(self, name: str, reason: str):
        if reason == "No item gain":
            channel = self.rfmanager.get_channel(channel_name=name)
            if not channel.monitoring_paused:
                channel.queue_restart(90, label="Town is desynced; items stopped getting rewarded", reason=RestartReason.ITEM_DESYNC)
    
    async def resolve_alert(self, name):
        pass
        