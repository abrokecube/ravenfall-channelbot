from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticationStorageHelper
from twitchAPI.type import AuthScope, ChatEvent
from twitchAPI.chat import Chat, EventData, ChatMessage, ChatCommand
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.chat.middleware import *
from twitchAPI.object.eventsub import ChannelPointsCustomRewardRedemptionData

from dotenv import load_dotenv

import os
import asyncio
import json
from typing import List
import logging

import ravenpy

from bot.commands import Commands, CommandContext, Command, Redeem, RedeemContext
from bot.models import *
from bot.ravenfallmanager import RFChannelManager
from database.models import update_schema
from utils.logging_fomatter import setup_logging
from bot.server import SomeEndpoints

load_dotenv()

USER_SCOPE = [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT, AuthScope.CHANNEL_MANAGE_REDEMPTIONS]
logger_config = {
    'twitchAPI': {
        'filename': "twitchAPI.log",
        'console_level': logging.INFO,
    },
    'middleman': {
        'filename': "middleman.log",
        'console_level': logging.INFO,
    },
    'aiosqlite': {
        'filename': "database.log",
        'console_level': logging.INFO,
    },
    'new_message_processor': {
        'filename': "rfmsgproc.log",
        'console_level': logging.INFO,
    },
    'aiohttp.access': {
        'filename': "httpserver.log",
        'console_level': logging.WARNING,
    },
    'bot.server': {
        'filename': "httpserver.log",
        'console_level': logging.WARNING,
    },
    'utils.runshell': {
        'filename': "runshell.log",
        'console_level': logging.WARNING,
    },
    'bot.ravenfallloc': {
        'filename': 'rfloc.log',
        'console_level': logging.WARNING,
    },
    'bot.ravenfallchannel': {
        'filename': 'rfchannels.log',
        'console_level': logging.INFO,
    },
    'bot.ravenfallmanager': {
        'filename': 'rfchannels.log',
        'console_level': logging.INFO,
    },

}
setup_logging(level=logging.DEBUG, loggers_config=logger_config)
logger = logging.getLogger(__name__)

with open("channels.json", "r") as f:
    channels: List[Channel] = json.load(f)
for channel in channels:
    channel["rf_query_url"] = channel["rf_query_url"].rstrip("/")
    # Set default command prefix if not specified
    if 'command_prefix' not in channel:
        channel['command_prefix'] = '!'

rf_manager = None

class MyCommands(Commands):
    def __init__(self, twitch: Twitch):
        super().__init__(twitch)
    
    async def on_command_error(self, ctx: CommandContext, command: Command, error: Exception):
        await ctx.send(f"❌ An error occurred")

    async def on_redeem_error(self, ctx: RedeemContext, redeem: Redeem, error: Exception):
        await ctx.send(f"❌ An error occurred")

    async def get_prefix(self, msg: ChatMessage) -> str:
        return "!"

async def run():
    def handle_loop_exception(loop, context):
        logger.error("Caught async exception: %s", context["exception"], exc_info=True)

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_loop_exception)
    
    await update_schema()
    
    # set up twitch api instance and add user authentication with some scopes
    twitch = await Twitch(os.getenv("TWITCH_CLIENT"), os.getenv("TWITCH_SECRET"))
    helper = UserAuthenticationStorageHelper(twitch, USER_SCOPE)
    rf = ravenpy.RavenNest(os.getenv("API_USER"), os.getenv("API_PASS"))
    asyncio.create_task(rf.login())
    await helper.bind()

    chat = await Chat(twitch, initial_channel=[x['channel_name'] for x in channels])
    eventsub = EventSubWebsocket(twitch)

    commands = MyCommands(chat)

    async def redemption_callback(redemption: ChannelPointsCustomRewardRedemptionData):
        await commands.process_channel_point_redemption(redemption)

    has_redeems = False
    for channel in channels:
        if channel.get("channel_points_redeems", False):
            has_redeems = True
            break
    if has_redeems:
        eventsub.start()
        has_subscribed = False
        for channel in channels:
            if channel.get("channel_points_redeems", False):
                try:
                    await eventsub.listen_channel_points_custom_reward_redemption_add(
                        channel['channel_id'],
                        redemption_callback,
                    )
                    logger.info(f"Listening for redeems in {channel['channel_name']}")
                    has_subscribed = True
                except Exception as e:
                    logger.error(f"Error listening for redeems in {channel['channel_name']}: {e}")
        if not has_subscribed:
            await eventsub.stop()

    def load_cogs():
        from bot.cogs.info import InfoCog
        commands.load_cog(InfoCog, rf_manager=rf_manager)
        from bot.cogs.testing import TestingCog
        commands.load_cog(TestingCog)
        from bot.cogs.game import GameCog
        commands.load_cog(GameCog, rf_manager=rf_manager)
        from bot.cogs.testing_rf import TestingRFCog
        commands.load_cog(TestingRFCog, rf_manager=rf_manager)
        from bot.cogs.bot import BotStuffCog
        commands.load_cog(BotStuffCog, rf_manager=rf_manager)

    async def on_ready(ready_event: EventData):
        global rf_manager
        rf_manager = RFChannelManager(channels, chat, rf)
        await rf_manager.start()
        load_cogs()
        logger.info("Bot is ready for work")

        server = SomeEndpoints(rf_manager, os.getenv("SERVER_HOST", "0.0.0.0"), os.getenv("SERVER_PORT", 8080))
        await server.start()

    chat.register_event(ChatEvent.READY, on_ready)

    async def on_message(message: ChatMessage):
        # logger.debug("%s: %s: %s", message.room.name, message.user.name, message.text)
        await commands.process_message(message)
        await rf_manager.event_twitch_message(message)
    chat.register_event(ChatEvent.MESSAGE, on_message)

    chat.start()

    try:
        while True:
            await asyncio.sleep(9999)
    except asyncio.CancelledError:
        logger.info("Bot is shutting down")
        chat.stop()
        if eventsub._running:
            await eventsub.stop()
        await rf_manager.stop()
        await twitch.close()

if __name__ == "__main__":
    asyncio.run(run())
