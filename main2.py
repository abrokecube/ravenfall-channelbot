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

import ravenpy

from bot.commands import Commands, Context, Command
from bot.models import *
from bot.ravenfallmanager import RFChannelManager

from bot.cogs.testing import TestingCog

load_dotenv()

USER_SCOPE = [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT]
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL").strip('/')

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
        print("Caught async exception:", context["exception"])

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_loop_exception)
    
    # set up twitch api instance and add user authentication with some scopes
    twitch = await Twitch(os.getenv("TWITCH_CLIENT"), os.getenv("TWITCH_SECRET"))
    helper = UserAuthenticationStorageHelper(twitch, USER_SCOPE)
    rf = ravenpy.RavenNest(os.getenv("API_USER"), os.getenv("API_PASS"))
    asyncio.create_task(rf.login())
    await helper.bind()

    chat = await Chat(twitch, initial_channel=[x['channel_name'] for x in channels])
    commands = MyCommands()
    
    # Import and set up the testing cog
    from bot.cogs.testing import setup as setup_testing
    setup_testing(commands)

    async def on_ready(ready_event: EventData):
        global rf_manager
        rf_manager = RFChannelManager(channels, chat, rf)
        await rf_manager.start()
        print("Bot is ready for work")
    chat.register_event(ChatEvent.READY, on_ready)

    async def on_message(message: ChatMessage):
        print(f"{message.room.name}: {message.user.name}: {message.text}")
        await commands.process_message(message)
    chat.register_event(ChatEvent.MESSAGE, on_message)

    chat.start()

    try:
        while True:
            await asyncio.sleep(9999)
    finally:
        chat.stop()
        await twitch.close()

if __name__ == "__main__":
    asyncio.run(run())
