from typing import List, Dict
from .ravenfallchannel import RFChannel
from .models import GameMultiplier, RFMiddlemanMessage, RFChannelEvent
from .multichat_command import send_multichat_command, get_desync_info
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

import os

logger = logging.getLogger(__name__)

class RFChannelManager:
    def __init__(self, config: dict, chat: Chat, rfapi: RavenNest):
        self.config = config
        self.rfapi = rfapi
        self.chat = chat
        self.channels: List[RFChannel] = []
        self.channel_id_to_channel: Dict[str, RFChannel] = {}
        self.channel_name_to_channel: Dict[str, RFChannel] = {}
        self.ravennest_is_online = True
        self.global_multiplier = 1.0

        self.rf_message_processor: MessageProcessor | None = None
        self.global_restart_lock = asyncio.Lock()
        self.middleman_enabled = False
        self.middleman_power_saving = False
        self.middleman_connected = False
        self.load_channels()


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

    async def stop(self):
        for channel in self.channels:
            await channel.stop()
        self.mult_check_routine.cancel()
        self.resync_routine.cancel()
        if self.rf_message_processor:
            await self.rf_message_processor.stop()

    async def event_twitch_message(self, message: ChatMessage):
        for channel in self.channels:
            if channel.channel_id == message.room.room_id:
                await channel.event_twitch_message(message)

    async def handle_processor_message(self, message: RavenMessage, metadata: MessageMetadata, client_info: ClientInfo):
        for channel in self.channels:
            if metadata.connection_id == channel.middleman_connection_id:
                if not metadata.is_api:
                    if metadata.source.lower() == "client":
                        asyncio.create_task(channel.event_ravenbot_message(message))
                    elif metadata.source.lower() == "server":
                        asyncio.create_task(channel.event_ravenfall_message(message))
                if metadata.source.lower() == "client":
                    return await channel.process_ravenbot_message(message.copy(), metadata)
                elif metadata.source.lower() == "server":
                    return await channel.process_ravenfall_message(message.copy(), metadata)
                else:
                    return message
        return message

    async def on_processor_connect(self, client_info: ClientInfo):
        self.middleman_connected = True
        self.middleman_enabled = True
        serverconf, err = await middleman.get_config()
        if not err:
            self.middleman_power_saving = not serverconf['disableTimeout']

    async def on_processor_disconnect(self, client_info: ClientInfo):
        self.middleman_connected = False

    def get_channel(self, *, channel_id: str | None = None, channel_name: str | None = None) -> RFChannel | None:
        if channel_id:
            if channel_id in self.channel_id_to_channel:
                return self.channel_id_to_channel[channel_id]
        elif channel_name:
            if channel_name in self.channel_name_to_channel:
                return self.channel_name_to_channel[channel_name]
        return None

    @routine(delta=timedelta(seconds=45))
    async def mult_check_routine(self):
        old_online = self.ravennest_is_online
        is_online = False
        multiplier: ExpMult | None = None
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            try:
                async with session.get(f"https://www.ravenfall.stream/api/game/exp-multiplier") as response:
                    data: GameMultiplier = await response.json()
                    if data:
                        is_online = True
                        self.global_multiplier = data["multiplier"]
                        multiplier = ExpMult(**data)
            except Exception as e:
                logger.error(f"Can't connect to Ravenfall API: {e}")
        self.ravennest_is_online = is_online
        
        if self.ravennest_is_online != old_online:
            if self.ravennest_is_online:
                msg = "🟢 RavenNest is online!"
            else:
                msg = "🔴 RavenNest is offline!"
            for channel in self.channels:
                await channel.send_chat_message(msg)

        if self.ravennest_is_online:
            now = datetime.now(timezone.utc)
            if multiplier.start_time and (now - multiplier.start_time) > timedelta(minutes=2):
                if multiplier.multiplier > 1:
                    for channel in self.channels:
                        if channel.multiplier['multiplier'] != self.global_multiplier:
                            r = await send_multichat_command(
                                text=f"?say {channel.ravenbot_prefixes[0]}multiplier",
                                user_id=channel.channel_id,
                                user_name=channel.channel_name,
                                channel_id=channel.channel_id,
                                channel_name=channel.channel_name
                            )
                            if r['status'] != 200:
                                await channel.send_chat_message(f"?say {channel.ravenbot_prefixes[0]}multiplier")
    
    @routine(delta=timedelta(seconds=120))
    async def resync_routine(self):
        data = await get_desync_info()
        if data['status'] != 200:
            logger.error(f"Failed to fetch desync info: {data['error']}")
            return
        if time.time() - data['data']['last_updated'] > 300:
            return
        if not self.ravennest_is_online:
            return
        ch_desync_times: Dict[str, float] = {}
        for channel_id in self.channel_id_to_channel.keys():
            if channel_id in data['data']['towns']:
                ch_desync_times[channel_id] = data['data']['towns'][channel_id]
        # logger.debug(', '.join([
        #     f'{self.channel_id_to_channel[channel_id].channel_name}: {round(ch_desync_times[channel_id], 3)}s'
        #     for channel_id in ch_desync_times
        # ]))
        resynced_channels = []
        for channel_id, desync in ch_desync_times.items():
            if abs(desync) < 30:  # 30 seconds
                continue
            channel = self.channel_id_to_channel[channel_id]
            if channel.event == RFChannelEvent.DUNGEON:
                continue
            r = await send_multichat_command(
                text="?resync",
                user_id=channel.channel_id,
                user_name=channel.channel_name,
                channel_id=channel.channel_id,
                channel_name=channel.channel_name
            )
            if r['status'] != 200:
                await channel.send_chat_message("?resync")
            resynced_channels.append(channel.channel_name)
