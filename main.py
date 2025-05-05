from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticationStorageHelper
from twitchAPI.type import AuthScope, ChatEvent
from twitchAPI.chat import Chat, EventData, ChatMessage, ChatCommand
from twitchAPI.chat.middleware import *
import asyncio
import os
from dotenv import load_dotenv
import json
import aiohttp
import aiohttp.client_exceptions
from typing import List
from datetime import datetime, timedelta

from models import *
from utils.routines import routine

load_dotenv()

USER_SCOPE = [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT]

with open("channels.json", "r") as f:
    channels: List[Channel] = json.load(f)
for channel in channels:
    channel["rf_query_url"] = channel["rf_query_url"].rstrip("/")

async def on_ready(ready_event: EventData):
    update_task.start(ready_event.chat)
    print('Bot is ready for work')
    ...

async def on_message(msg: ChatMessage):
    # print(f'#{msg.room.name}: {msg.user.name}: {msg.text}')
    ...

    
async def test_command(cmd: ChatCommand):
    await cmd.reply(f'hello {cmd.user.name}')


async def towns_handler(cmd: ChatCommand):
    async with aiohttp.ClientSession() as session:
        r = await asyncio.gather(*[
            session.get(f"{x['rf_query_url']}/select * from village") for x in channels
        ], return_exceptions=True)
        villages: List[Village] = await asyncio.gather(*[
            x.json() for x in r
        ])
    out_str = []
    for idx, village in enumerate(villages):
        if not isinstance(village, dict):
            continue
        channel: Channel = channels[idx]
        split = village['boost'].split()
        boost_stat = split[0]
        boost_value = float(split[1].rstrip("%"))
        asdf = f"Town #{idx+1}: @{channel['channel_name']} - {boost_stat} {int(round(boost_value))}%"
        if channel['custom_town_msg']:
            asdf += f" {channel['custom_town_msg']}"
        out_str.append(asdf)
        
    await cmd.reply(' ✦ '.join(out_str))
        
async def update_handler(cmd: ChatCommand):
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            village_url = channel['rf_query_url']
            village_prefix = channel['ravenbot_prefix']
            break
    else:
        await cmd.reply("Town not found :(")
        return
    async with aiohttp.ClientSession() as session:
        try:
            r = await session.get(f"{village_url}/select * from village")
        except aiohttp.client_exceptions.ContentTypeError:
            await cmd.reply("This channel's town is currently not online!")
            return
        village: Village = await r.json()
        split = village['boost'].split()
        boost_stat = split[0]
        boost_value = float(split[1].rstrip("%"))
        await cmd.send(f"{village_prefix}town {boost_stat.lower()}")
    

@routine(delta=timedelta(hours=6), wait_first=True)
async def update_task(chat: Chat):
    async with aiohttp.ClientSession() as session:
        print([
            f"{x['rf_query_url']}/select * from village" for x in channels
        ])
        r = await asyncio.gather(*[
            session.get(f"{x['rf_query_url']}/select * from village") for x in channels
        ], return_exceptions=True)
        villages: List[Village] = await asyncio.gather(*[
            x.json() for x in r
        ])
    for idx, village in enumerate(villages):
        if not isinstance(village, dict):
            continue
        channel: Channel = channels[idx]
        split = village['boost'].split()
        boost_stat = split[0]
        boost_value = float(split[1].rstrip("%"))
        asdf = f"{channel['ravenbot_prefix']}town {boost_stat.lower()}"
        await chat.send_message(channel['channel_name'], asdf)

# this is where we set up the bot
async def run():
    # set up twitch api instance and add user authentication with some scopes
    twitch = await Twitch(os.getenv("TWITCH_CLIENT"), os.getenv("TWITCH_SECRET"))
    helper = UserAuthenticationStorageHelper(twitch, USER_SCOPE)
    await helper.bind()

    
    # create chat instance
    chat = await Chat(twitch, initial_channel=[x['channel_name'] for x in channels])
    # for channel in channels:
    #     chat.set_channel_prefix(channel['ravenbot_prefix'], channel['channel_name'])

    # register the handlers for the events you want

    chat.register_event(ChatEvent.READY, on_ready)
    chat.register_event(ChatEvent.MESSAGE, on_message)

    chat.register_command('hi', test_command)
    chat.register_command('towns', towns_handler)
    chat.register_command('update', update_handler)

    chat.start()

    # lets run till we press enter in the console
    try:
        input('press ENTER to stop\\n')
    finally:
        # now we can close the chat bot and the twitch api client
        chat.stop()
        await twitch.close()

# lets run our setup
asyncio.run(run())