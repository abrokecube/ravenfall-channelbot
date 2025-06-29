import time
from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticationStorageHelper
from twitchAPI.type import AuthScope, ChatEvent
from twitchAPI.chat import Chat, EventData, ChatMessage, ChatCommand
from twitchAPI.chat.middleware import *
import asyncio
import subprocess
import os
from dotenv import load_dotenv
import json
import aiohttp
import aiohttp.client_exceptions
from typing import List, Tuple, Callable, Any, TypedDict
from datetime import datetime, timedelta
import psutil
import shutil
import traceback
import logging

from gotify import AsyncGotify
from gotify import gotify

from models import *
from utils.routines import routine
from utils.format_time import *
from utils import strutils
from utils import chatmsg_cd
from utils import langstuff
from utils.is_twitch_username import is_twitch_username
from utils.filter_username import filter_username
from utils import utils
from dataclasses import dataclass

import braille
import ravenpy
from ravenpy import Skills, Islands
from datetime import timezone, timedelta, datetime
load_dotenv()

USER_SCOPE = [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT]
GOTIFY_URL = os.getenv('GOTIFY_URL').strip("/")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL").strip('/')



# Global flag to pause/resume RavenBot response monitoring
monitoring_paused = False


async def welcome_msg_cmd(cmd: ChatCommand):
    await first_time_joiner(cmd)

async def ping_cmd(cmd: ChatCommand):
    await cmd.reply("Pong!")



async def on_ready(ready_event: EventData):
    print('Bot is ready for work')

# this is where we set up the bot
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

    
    # create chat instance
    chat = await Chat(twitch, initial_channel=[x['channel_name'] for x in channels])
    # for channel in channels:
    #     chat.set_channel_prefix(channel['ravenbot_prefix'], channel['channel_name'])

    # register the handlers for the events you want

    # Register event handlers
    chat.register_event(ChatEvent.READY, on_ready)
    chat.register_event(ChatEvent.MESSAGE, on_message)
    chat.register_command('ping', ping_cmd)
    chat.register_command('welcomemsg', welcome_msg_cmd)

    asyncio.create_task(gotify_listener(chat))
    chat.start()

    try:
        while True:
            await asyncio.sleep(9999)
    finally:
        chat.stop()
        await twitch.close()

# lets run our setup
asyncio.run(run())

