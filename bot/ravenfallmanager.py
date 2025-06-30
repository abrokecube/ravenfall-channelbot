from typing import List
from .ravenfallchannel import RFChannel
from .models import GameMultiplier, RFMiddlemanMessage
from .ravenfallloc import RavenfallLocalization
from . import middleman
from twitchAPI.chat import Chat, ChatMessage
from ravenpy import RavenNest, ExpMult
import asyncio
import aiohttp
import logging
from utils.routines import routine
from utils.websocket_client import AutoReconnectingWebSocket
from datetime import timedelta, datetime, timezone

import os

logger = logging.getLogger(__name__)

class RFChannelManager:
    def __init__(self, config: dict, chat: Chat, rfapi: RavenNest):
        self.config = config
        self.rfapi = rfapi
        self.chat = chat
        self.channels: List[RFChannel] = []
        self.ravennest_is_online = True
        self.global_multiplier = 1.0

        self.rf_message_feed_ws: AutoReconnectingWebSocket | None = None
        self.global_restart_lock = asyncio.Lock()
        self.middleman_enabled = False
        self.middleman_power_saving = False
        self.middleman_connected = False
        self.rfloc = RavenfallLocalization()
        self.load_channels()


    def load_channels(self):
        for channel in self.config:
            self.channels.append(
                RFChannel(
                    channel, 
                    self,
                )
            )
    
    async def start(self):
        for channel in self.channels:
            await channel.start()
        self.mult_check_routine.start()
        msg_feed_host = os.getenv("RF_MIDDLEMAN_HOST", None)
        msg_feed_port = os.getenv("RF_MIDDLEMAN_PORT", None)
        if msg_feed_host and msg_feed_port:
            self.rf_message_feed_ws = AutoReconnectingWebSocket(
                f"ws://{msg_feed_host}:{msg_feed_port}/ws",
                on_message=self.on_middleman_message,
                on_connect=self.on_middleman_connect,
                on_disconnect=self.on_middleman_disconnect,
                on_error=self.on_middleman_error,
                reconnect_interval=1.0,
                max_reconnect_interval=30.0,
                logger=logger,
            )
            await self.rf_message_feed_ws.connect()

    async def stop(self):
        for channel in self.channels:
            await channel.stop()
        self.mult_check_routine.cancel()
        if self.rf_message_feed_ws:
            await self.rf_message_feed_ws.disconnect()

    async def event_twitch_message(self, message: ChatMessage):
        for channel in self.channels:
            if channel.channel_id == message.room.room_id:
                await channel.event_twitch_message(message)

    async def on_middleman_message(self, message: RFMiddlemanMessage):
        channel = None
        for ch in self.channels:
            if ch.middleman_connection_id == message["connection_id"]:
                channel = ch
        if not channel:
            logger.warning(f"Received message with unmatched connection ID: {message['connection_id']}")
            return
        if message["source"] == "CLIENT":
            await channel.event_ravenbot_message(message["message"])
        elif message["source"] == "SERVER":
            await channel.event_ravenfall_message(message["message"])

    async def on_middleman_connect(self):
        self.middleman_connected = True
        self.middleman_enabled = True
        serverconf, err = await middleman.get_config()
        if not err:
            self.middleman_power_saving = not serverconf['disableTimeout']

    async def on_middleman_disconnect(self):
        self.middleman_connected = False

    async def on_middleman_error(self, error: Exception):
        logger.error(f"Middleman error: {error}")

    def get_channel(self, *, channel_id: str | None = None, channel_name: str | None = None) -> RFChannel | None:
        if channel_id:
            for channel in self.channels:
                if channel.channel_id == channel_id:
                    return channel
        elif channel_name:
            for channel in self.channels:
                if channel.channel_name == channel_name:
                    return channel
        return None

    @routine(delta=timedelta(seconds=30))
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
                logger.error(f"Error checking online status: {e}", exc_info=True)
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
            if multiplier.start_time and (now - multiplier.start_time) > timedelta(minutes=5):
                if multiplier.multiplier > 1:
                    for channel in self.channels:
                        if channel.multiplier['multiplier'] != self.global_multiplier:
                            await channel.send_chat_message(f"?say {channel.ravenbot_prefix}multiplier")
