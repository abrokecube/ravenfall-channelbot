from typing import List
from .ravenfallchannel import RFChannel
from .models import GameMultiplier
from twitchAPI.chat import Chat, ChatMessage
from ravenpy import RavenNest, ExpMult
import asyncio
import aiohttp
import logging
from utils.routines import routine
from datetime import timedelta

logger = logging.getLogger(__name__)

class RFChannelManager:
    def __init__(self, config: dict, chat: Chat, rfapi: RavenNest):
        self.config = config
        self.rfapi = rfapi
        self.chat = chat
        self.channels: List[RFChannel] = []
        self.ravennest_is_online = True
        self.global_multiplier = 1.0

        self.global_restart_lock = asyncio.Lock()
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

    async def stop(self):
        for channel in self.channels:
            await channel.stop()
        self.mult_check_routine.cancel()

    async def event_twitch_message(self, message: ChatMessage):
        for channel in self.channels:
            if channel.channel_id == message.room.room_id:
                await channel.event_twitch_message(message)

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
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            try:
                async with session.get(f"https://www.ravenfall.stream/api/game/exp-multiplier") as response:
                    data: GameMultiplier = await response.json()
                    if data:
                        self.ravennest_is_online = True
                        self.global_multiplier = data["multiplier"]
                    else:
                        self.ravennest_is_online = False
            except Exception as e:
                self.ravennest_is_online = False
                logger.error(f"Error checking online status: {e}", exc_info=True)
        
        # if self.ravennest_is_online:
        #     if data['multiplier'] > 1:
        #         for channel in self.channels:
        #             if channel.multiplier['multiplier'] != self.global_multiplier:
        #                 await channel.send_chat_message(f"?say {channel.ravenbot_prefix}multiplier")
        if self.ravennest_is_online != old_online:
            if self.ravennest_is_online:
                msg = "🟢 RavenNest is online!"
            else:
                msg = "🔴 RavenNest is offline!"
            for channel in self.channels:
                await channel.send_chat_message(msg)