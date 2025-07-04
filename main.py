from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticationStorageHelper
from twitchAPI.type import AuthScope, ChatEvent
from twitchAPI.chat import Chat, EventData, ChatMessage, ChatCommand
from twitchAPI.chat.middleware import *

from dotenv import load_dotenv

import os
import asyncio
import json
from typing import List
import logging

import ravenpy

from bot.commands import Commands, Context, Command
from bot.models import *
from bot.ravenfallmanager import RFChannelManager
from database.models import update_schema
from utils.logging_fomatter import setup_logging

load_dotenv()

USER_SCOPE = [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT]

# Configure logging
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
log_level = getattr(logging, log_level, logging.INFO)  # Default to INFO if invalid level provided

setup_logging(level=log_level)
logger = logging.getLogger(__name__)

# Suppress twitchAPI.chat logs below WARNING level
logging.getLogger('twitchAPI.chat').setLevel(logging.INFO)
logging.getLogger('middleman').setLevel(logging.INFO)
logging.getLogger('aiosqlite').setLevel(logging.INFO)
logging.getLogger('new_message_processor').setLevel(logging.INFO)


with open("channels.json", "r") as f:
    channels: List[Channel] = json.load(f)
for channel in channels:
    channel["rf_query_url"] = channel["rf_query_url"].rstrip("/")
    # Set default command prefix if not specified
    if 'command_prefix' not in channel:
        channel['command_prefix'] = '!'

rf_manager = None

class MyCommands(Commands):
    def __init__(self):
        super().__init__()
    
    async def on_error(self, ctx: Context, command: Command, error: Exception):
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
    commands = MyCommands()
    
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
    chat.register_event(ChatEvent.READY, on_ready)

    async def on_message(message: ChatMessage):
        logger.debug("%s: %s: %s", message.room.name, message.user.name, message.text)
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
        await rf_manager.stop()
        await twitch.close()

if __name__ == "__main__":
    asyncio.run(run())
