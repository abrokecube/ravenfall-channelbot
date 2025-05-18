import time
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
import psutil

from gotify import AsyncGotify
from gotify import gotify

from models import *
from utils.routines import routine
from utils.format_time import *
from utils import strutils
from utils import chatmsg_cd
from dataclasses import dataclass
load_dotenv()

USER_SCOPE = [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT]

with open("channels.json", "r") as f:
    channels: List[Channel] = json.load(f)
for channel in channels:
    channel["rf_query_url"] = channel["rf_query_url"].rstrip("/")

async def on_ready(ready_event: EventData):
    update_task.start(ready_event.chat)
    update_events.start()
    update_mult.start(ready_event.chat)
    print('Bot is ready for work')
    ...

async def on_message(msg: ChatMessage):
    ch_data = get_channel_data(msg.room.room_id)
    if msg.first and msg.text[:5].lower() == f"{ch_data['ravenbot_prefix']}join":
        await first_time_joiner(msg)

@chatmsg_cd.chat_autoresponse_cd(5, chatmsg_cd.CooldownType.CHANNEL)
async def first_time_joiner(msg: ChatMessage):
    await asyncio.sleep(3)
    ch = get_channel_data(msg.room.room_id) 
    if ch is None:
        return
    boost = await get_town_boost(ch)
    welcome_msg = ch['welcome_message'].format_map({
        "townSkillLower": boost[0].skill.lower(),
        "townSkill": boost[0].skill,
        "userName": msg.user.name,
        "userDisplayName": msg.user.display_name
    })
    await msg.chat.send_message(msg.room.name, welcome_msg)
    
    
async def test_cmd(cmd: ChatCommand):
    await cmd.reply(f'hello {cmd.user.name}')


async def towns_cmd(cmd: ChatCommand):
    async with aiohttp.ClientSession() as session:
        r = await asyncio.gather(*[
            session.get(f"{x['rf_query_url']}/select * from village") for x in channels
        ], return_exceptions=True)
        villages: List[Village] = await asyncio.gather(*[
            x.json() for x in r if not isinstance(x, Exception)
        ])
    out_str = []
    for idx, village in enumerate(villages):
        if not isinstance(village, dict):
            continue
        channel: Channel = channels[idx]
        if len(village['boost'].strip()) > 0:
            split = village['boost'].split()
            boost_stat = split[0]
            boost_value = float(split[1].rstrip("%"))
            asdf = f"Town #{idx+1}: @{channel['channel_name']} - {boost_stat} {int(round(boost_value))}%"
        else:
            asdf = f"Town #{idx+1}: @{channel['channel_name']} - No boost"
        if channel['custom_town_msg']:
            asdf += f" {channel['custom_town_msg']}"
        out_str.append(asdf)
        
    await cmd.reply(' ✦ '.join(out_str))

def get_channel_data(channel_id) -> Channel | None:
    for channel in channels:
        if channel['channel_id'] == channel_id:
            return channel
    return None

async def get_town_boost(channel: Channel) -> List[TownBoost] | None:
    async with aiohttp.ClientSession() as session:
        try:
            r = await session.get(f"{channel['rf_query_url']}/select * from village")
        except aiohttp.client_exceptions.ContentTypeError:
            return None
        village: Village = await r.json()
        split = village['boost'].split()
        if len(split) < 2:
            return []
        boost_stat = split[0]
        boost_value = float(split[1].rstrip("%"))
        return [TownBoost(boost_stat, boost_value/100)]
        
async def update_cmd(cmd: ChatCommand):
    this_channel: Channel = None
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            town_boost = await get_town_boost(channel)
            this_channel = channel
            break
    else:
        await cmd.reply("Town not found :(")
        return
    if town_boost is None:
        await cmd.reply("This channel's town is currently not online!")
        return
    if len(town_boost) == 0:
        await cmd.reply(f"This town has no active boost.")
        return
    await cmd.send(f"{this_channel['ravenbot_prefix']}town {town_boost[0].skill.lower()}")
    
    
village_events: Dict[str, str] = {}
async def event_cmd(cmd: ChatCommand):
    if not cmd.room.room_id in village_events:
        await cmd.reply("No active event.")
        return
    await cmd.reply(f"{village_events[cmd.room.room_id]}")

async def uptime_cmd(cmd: ChatCommand):
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            village_url = channel['rf_query_url']
            break
    else:
        await cmd.reply("Town not found :(")
        return
    async with aiohttp.ClientSession() as session:
        try:
            r = await session.get(f"{village_url}/select * from session")
        except aiohttp.client_exceptions.ContentTypeError:
            await cmd.reply("Ravenfall uptime: Offline!")
            return
        game_session: GameSession = await r.json()
        await cmd.reply(f"Ravenfall uptime: {seconds_to_dhms(game_session['secondssincestart'])}")

def bytes_to_human_readable(size_bytes):
    if size_bytes == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB']
    power = 1024
    unit_index = 0

    while size_bytes >= power and unit_index < len(units) - 1:
        size_bytes /= power
        unit_index += 1

    return f"{size_bytes:.2f} {units[unit_index]}"

async def system_cmd(cmd: ChatCommand):
    cpu_usage = await asyncio.to_thread(psutil.cpu_percent, 1)
    cpu_freq = psutil.cpu_freq().current
    ram = psutil.virtual_memory()
    ram_usage = ram.used
    ram_total = ram.total
    battery = psutil.sensors_battery()
    battery_text = ""
    if battery:
        battery_percent = battery.percent
        battery_plugged = "Charging" if battery.power_plugged else "Not charging"
        battery_time_left = format_seconds(battery.secsleft)
        battery_text = f"Battery: {battery_percent}%, {battery_plugged} ({battery_time_left} left)"
    uptime = time.time() - psutil.boot_time()
    await cmd.reply(strutils.strjoin(
        " – ", 
        f"CPU: {cpu_usage/100:.1%}, {cpu_freq:.0f} MHz",
        f"RAM: {bytes_to_human_readable(ram_usage)}/{bytes_to_human_readable(ram_total)}",
        battery_text,
        f"Uptime: {seconds_to_dhms(uptime)}"
    ))

async def welcome_msg_cmd(cmd: ChatCommand):
    await first_time_joiner(cmd)

max_dungeon_hp: Dict[str, int] = {}
@routine(delta=timedelta(seconds=2))
async def update_events():
    async with aiohttp.ClientSession() as session:
        tasks = []
        tasks.extend([
            session.get(f"{x['rf_query_url']}/select * from dungeon") for x in channels
        ])
        tasks.extend([
            session.get(f"{x['rf_query_url']}/select * from raid") for x in channels
        ])
        r = await asyncio.gather(*tasks, return_exceptions=True)
        data = await asyncio.gather(*[
            x.json() for x in r
        ])
    a = int(len(tasks) / 2)
    dungeons: List[Dungeon] = data[a*0:a*1]
    raids: List[Raid] = data[a*1:a*2]
    for dungeon, raid, channel in zip(dungeons, raids, channels):
        event_text = "No active event."
        if dungeon and dungeon.get('enemies'):
            if not dungeon['started']:
                max_dungeon_hp[channel['channel_id']] = dungeon["boss"]["health"]
                time_starting = format_seconds(dungeon['secondsuntilstart'])
                event_text = (
                    f"DUNGEON starting in {time_starting} – "
                    f"Boss HP: {dungeon['boss']['health']:,} – "
                    f"Enemies: {dungeon['enemies']:,} – "
                    f"Players: {dungeon['players']:,}"
                )
            else:
                if dungeon['enemiesalive'] > 0 or not channel['channel_id'] in max_dungeon_hp:
                    max_dungeon_hp[channel['channel_id']] = dungeon["boss"]["health"]
                boss_max_hp = max_dungeon_hp[channel['channel_id']]
                event_text = (
                    f"DUNGEON – "
                    f"Boss HP: {dungeon['boss']['health']:,}/{boss_max_hp:,} "
                    f"({dungeon['boss']['health']/boss_max_hp:.1%}) – "
                    f"Enemies: {dungeon['enemiesalive']:,}/{dungeon['enemies']:,} – "
                    f"Players: {dungeon['playersalive']:,}/{dungeon['players']:,} – "
                    f"Elapsed time: {format_seconds(dungeon['elapsed'])}"
                )
        elif raid and raid['started'] and raid['boss']['maxhealth'] > 0:
            event_text = (
                "RAID – "
                f"Boss HP: {raid['boss']['health']:,}/{raid['boss']['maxhealth']:,} "
                f"({raid['boss']['health']/raid['boss']['maxhealth']:.1%}) – "
                f"Players: {raid['players']:,} – "
                f"Time left: {format_seconds(raid['timeleft'])}"
            )
        village_events[channel['channel_id']] = event_text


current_mult: float = None
@routine(delta=timedelta(seconds=2))
async def update_mult(chat: Chat):
    global current_mult
    async with aiohttp.ClientSession() as session:
        r = await session.get(f"{channels[0]['rf_query_url']}/select * from multiplier")
        multiplier: GameMultiplier = await r.json()
    if current_mult is None:
        current_mult = multiplier['multiplier']
    if multiplier['multiplier'] > current_mult:
        current_mult = multiplier['multiplier']
        msg = f"{multiplier['eventname']} increased the multiplier to {int(current_mult)}x!"
        for channel in channels:
            await chat.send_message(channel['channel_name'], msg)
    current_mult = multiplier['multiplier']
    


@routine(delta=timedelta(hours=6), wait_first=True)
async def update_task(chat: Chat):
    async with aiohttp.ClientSession() as session:
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
        if len(village['boost'].strip()) > 0:
            split = village['boost'].split()
            boost_stat = split[0]
            boost_value = float(split[1].rstrip("%"))
            asdf = f"{channel['ravenbot_prefix']}town {boost_stat.lower()}"
            await chat.send_message(channel['channel_name'], asdf)

async def event_gotify_msg(msg: gotify.Message, chat: Chat):
    split = msg['message'].split("::", 2)
    target = None
    text = None
    if len(split) == 2:
        target, text = split
    else:
        text = msg['message']
        
    print(f"Recieved gotify message: {msg}")
    if target is not None:
        targets = target.split(', ')
        for room in targets:
            if chat.is_in_room(room):
                await chat.send_message(room, text)
            else:
                print(f"Unknown room: {room}")
    else:
        for channel in channels:
            await chat.send_message(channel['channel_name'], text)

async def gotify_listener(chat: Chat):
    while True:
        await asyncio.sleep(1)
        try:
            gotify = AsyncGotify(
                base_url=os.getenv("GOTIFY_URL"),
                client_token=os.getenv("GOTIFY_CLIENT_TOKEN")
            )
            print("Connected to Gotify")
            async for msg in gotify.stream():
                if msg['appid'] == os.getenv("GOTIFY_APP_ID"):
                    await event_gotify_msg(msg, chat)
        except Exception as e:
            print(f"Gotify listener failed: {e}, retrying...")

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

    chat.register_command('hi', test_cmd)
    chat.register_command('towns', towns_cmd)
    chat.register_command('update', update_cmd)
    chat.register_command('event', event_cmd)
    chat.register_command('uptime', uptime_cmd)
    chat.register_command('system', system_cmd)
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