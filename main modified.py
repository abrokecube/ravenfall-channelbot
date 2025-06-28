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


async def test_cmd(cmd: ChatCommand):
    await cmd.reply(f'hello {cmd.user.name}')


async def towns_cmd(cmd: ChatCommand):
    villages: List[Village] = await asyncio.gather(*[get_ravenfall_query(x['rf_query_url'], 'select * from village') for x in channels])
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
    message = f"{this_channel['ravenbot_prefix']}town {town_boost[0].skill.lower()}"
    await cmd.send(message)
    asyncio.create_task(monitor_ravenbot_response(cmd.chat, channel['channel_id'], 'town', resend_text=message))
    
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
    
async def ravenfall_ram_cmd(cmd: ChatCommand):
    processes: Dict[str, List[float]] = {}
    working_set = await get_prometheus_instant("windows_process_working_set_bytes{process='Ravenfall'}")
    change_over_time = await get_prometheus_instant("deriv(windows_process_working_set_bytes{process='Ravenfall'}[3m])")
    working_set_series = await get_prometheus_series("windows_process_working_set_bytes{process='Ravenfall'}", 60*10)
    tasks = []
    for ch in channels:
        shellcmd = (
            f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{ch['sandboxie_box']} /silent /listpids"
        )
        tasks.append(runshell(shellcmd))
    responses: List[str | None] = await asyncio.gather(*tasks)
    pid_lists = [x.splitlines() for x in responses]
    box_pids = {}
    for i in range(len(channels)):
        box_pids[channels[i]['channel_name']] = pid_lists[i]
    for metric in working_set:
        m = metric['metric']
        name = m['process_id']
        processes[name] = [int(metric['value'][1])]
    for metric in change_over_time:
        m = metric['metric']
        name = m['process_id']
        if name in processes:
            processes[name].append(float(metric['value'][1]))
    for metric in working_set_series:
        m = metric['metric']
        name = m['process_id']
        data_pairs = [(x[0], float(x[1])) for x in metric['values']]
        if name in processes:
            processes[name].append(data_pairs)
    processes_named: Dict[str, List[float]] = {}
    for name, pids in box_pids.items():
        for pid in pids:
            if pid in processes:
                processes_named[name] = processes[pid]
                break
    out_str = []
    if "all" in cmd.parameter.lower():
        for name, (bytes_used, change, series) in processes_named.items():
            s = "+"
            if change < 0:
                s = ''
            out_str.append(
                f"{name} - {bytes_to_human_readable(bytes_used)} ({s}{bytes_to_human_readable(change)}/s)"
            )
        await cmd.reply(f"Ravenfall ram usage: {' • '.join(out_str)} | Showing change over 3 minutes")
    else:
        bytes_used, change, series = processes_named[cmd.room.name]
        graph = braille.simple_line_graph(
            series, max_gap=30, width=26, fill_type=1, hard_min_val=1
        )
        await cmd.reply(
            f"[{graph}] Ravenfall is using {bytes_to_human_readable(bytes_used)} of memory; "
            f"changed by {bytes_to_human_readable(change)}/s over 3 mins. (Graph: 10 minutes)"
        )
    

async def exprate_cmd(cmd: ChatCommand):
    args = cmd.parameter.split()
    target_user = cmd.user.name
    if len(cmd.parameter) > 2 and len(args) > 0:
        target_user = filter_username(args[0])
    if not is_twitch_username(target_user):
        await cmd.reply("Invalid username.")
        return
    
    query = "sum(rate(rf_player_stat_experience_total{player_name=\"%s\",session=\"%s\",stat!=\"health\"}[90s]))" % (target_user, cmd.room.name)
    data = await get_prometheus_series(query, 10*60)
    if len(data) == 0:
        await cmd.reply("No data recorded. Your character may not be in this town right now.")
        return
    data_pairs = [(x[0], float(x[1])) for x in data[0]['values']]
    graph = braille.simple_line_graph(
        data_pairs, max_gap=30, width=26, min_val=1, fill_type=1
    )
    await cmd.reply(
        f"[{graph}] Earning {data_pairs[-1][1]*60*60:,.0f} exp/h (graph: last 10 minutes)"
    )
    ...

async def chracter_cmd(cmd: ChatCommand):
    args = cmd.parameter.split()
    target_user = cmd.user.name
    if len(cmd.parameter) > 2 and len(args) > 0:
        target_user = filter_username(args[0])
    if not is_twitch_username(target_user):
        await cmd.reply("Invalid username.")
        return

    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            village_url = channel['rf_query_url']
            break
    else:
        await cmd.reply("Town not found :(")
        return
    async with aiohttp.ClientSession() as session:
        try:
            r = await session.get(f"{village_url}/select * from players where name = \'{target_user}\'")
        except aiohttp.client_exceptions.ContentTypeError:
            await cmd.reply("Ravenfall seems to be offline!")
            return
        player_info: Player = await r.json()
        if isinstance(player_info, dict) and player_info:
            char = Character(player_info)
        else:
            await cmd.reply("You are not currently playing.")
            return
    async with aiohttp.ClientSession() as session:
        r = await session.get(f"{village_url}/select * from ferry")
        ferry_info: Ferry = await r.json()
            
    where = ""
    if char.in_raid:
        where = "in a raid"
    if char.in_arena:
        where = "in the arena"
    if char.in_dungeon:
        where = "in a dungeon"
    if char.in_onsen:
        where = "in the onsen"

    index_and_combat_level = f"(Lv{char.combat_level})"

    what = ""
    if char.training in (Skills.Attack, Skills.Defense, Skills.Strength, Skills.Health, Skills.Magic, Skills.Ranged):
        what = f"training {char.training.name.lower()}"
    elif char.training in ravenpy.resource_skills:
        if char.target_item:
            what = f"{char.training.name.lower()} {char.target_item.item.name.lower()}"
        else:
            what = f"{char.training.name.lower()}"
    elif char.training == Skills.Alchemy:
        what = f"training alchemy"
    elif char.training == Skills.Sailing:
        pass
    elif char.training is None:
        pass
    else:
        what = f"{char.training.name.lower()}"

    if char.in_onsen:
        what = "resting"

    target_item = ""
    if char.target_item and what and not char.in_onsen:
        target_item = f"{char.target_item.amount}× {char.target_item.item.name}"

    where_island = ""
    if char.island:
        where_island = f"at {char.island.name.capitalize()}"
    else:
        where_island = "sailing the seas"
        
    rested = ""
    if char.rested_time.total_seconds() > 0:
        s = TimeSize.SMALL_SPACES 
        rested = f"with {format_seconds(char.rested_time.total_seconds(),s)} of rest time"

    captain = ""
    if ferry_info['captain']['name'] == char.user_name:
        captain = "as the ship captain"
        
    stats = []
    if not char.in_onsen:
        for char_stat in char.training_stats:
            skill_name = char_stat.skill.name.capitalize()
            stats.append(
                f"{skill_name}: {char_stat.level} [+{char_stat.enchant_levels}] "\
                f"({char_stat.level_exp/char_stat.total_exp_for_level:.1%}) "\
                f"{char_stat.level_exp:,.0f}/{char_stat.total_exp_for_level:,.0f} EXP"
            )
    query = "sum(deriv(rf_player_stat_experience_total{player_name=\"%s\",session=\"%s\",stat!=\"health\"}[2m]))" % (target_user, cmd.room.name)
    data = await get_prometheus_series(query, 1)
    data_pairs = [(x[0], float(x[1])) for x in data[0]['values']]
    char_exp_per_h = data_pairs[-1][1]*60*60
    train_time = ""
    if char_exp_per_h > 0 and char.training:
        closest_stat = char.training_stats[0]
        exp_to_next_level = closest_stat.total_exp_for_level-closest_stat.level_exp
        training_time_exp = timedelta(seconds=(exp_to_next_level) / (char_exp_per_h/60/60))
    else:
        training_time_exp = timedelta(weeks=9999)
    s = TimeSize.SMALL_SPACES
    train_time_format = format_timedelta(training_time_exp, s)
    now = datetime.now(timezone.utc)
    if char.island and not char.in_onsen:
        if training_time_exp.total_seconds() > 60*60*24*100:  # 99 days
            train_time = f"Level in ∞"
        else:
            train_time = f"Level in {train_time_format}"
    exp_per_hr = f""
    if char.island and not char.in_onsen:
        exp_per_hr = f"{char_exp_per_h:,.0f} exp/hr"
        
    coins = f"{utils.pl(char.coins, 'coin')}"

    summary = utils.strjoin(
        " ", index_and_combat_level, "is", what, where, where_island, captain, rested
    )
    out_str = utils.strjoin(
        " – ", summary, target_item, utils.strjoin(', ', *stats), exp_per_hr, train_time, coins
    )
    user_name = f"{utils.unping(char.user_name)}"
    out_msgs = utils.strjoin(" ", user_name, out_str)
    if train_time:
        out_msgs = utils.strjoin('', out_msgs, f" | Training time is estimated")
    await cmd.reply(out_msgs)

async def multiplier_cmd(cmd: ChatCommand):
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            village_url = channel['rf_query_url']
            break
    else:
        await cmd.reply("Town not found :(")
        return
    async with aiohttp.ClientSession() as session:
        try:
            r = await session.get(f"{village_url}/select * from multiplier")
        except aiohttp.client_exceptions.ContentTypeError:
            await cmd.reply("Ravenfall seems to be offline!")
            return
        mult_info: GameMultiplier = await r.json()
    mult = int(mult_info['multiplier'])
    if mult <= 1:
        await cmd.reply(
            f"Current global exp multiplier is {mult}×."
        )
    else:
        await cmd.reply(
            f"Current global exp multiplier is {mult}×, "
            f"ending in {format_seconds(mult_info['timeleft'], TimeSize.LONG)}, "
            f"thanks to {mult_info['eventname']}!"
        )


async def ravenfall_restart_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    if cmd.room.room_id in village_events:
        if village_events[cmd.room.room_id].split(maxsplit=1)[0] != 'No':
            await cmd.reply("There is an active event.")
            return
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            thechannel = channel
            break
    else:
        await cmd.reply("Town not found :(")
        return
    add_restart_task(thechannel, cmd.chat, time_to_restart=20, label="User restart")
    await cmd.reply(f"Restart queued. Restarting in 20s.")

async def ravenfall_queue_restart_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    args = cmd.parameter.split()
    seconds = 5*60
    if len(args) > 0 and args[0].isdigit():
        seconds = int(args[0])
    this_channel = None
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            this_channel = channel
            break
    else:
        await cmd.reply("Town not found :(")
        return

    add_restart_task(this_channel, cmd.chat, time_to_restart=seconds, label="User restart")
    await cmd.reply(f"Restart queued. Restarting in {seconds}s.")

async def ravenfall_queue_restart_cancel_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    task = get_restart_task(cmd.room.room_id)
    if (not task) or task.finished():
        await cmd.reply("No task to cancel")
        return
    task.cancel()
    await cmd.reply("Cancelled restart task.")

async def ravenbot_restart_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    thischannel = None
    for channel in channels:
        if channel['channel_id'] == cmd.room.room_id:
            thischannel = channel
            break
    else:
        await cmd.reply("Town not found :(")
        return
    await restart_ravenbot(thischannel)
    await cmd.reply("Okay")

async def postpone_restart_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    args = cmd.parameter.split()
    seconds = 3*60
    if len(args) > 0 and args[0].isdigit():
        seconds = int(args[0])
    task = postpone_restart_task(cmd.room.room_id, seconds)
    if task is None:
        return
    await cmd.reply(f"Restart postponed by {seconds} seconds.")

async def get_restart_time_left_cmd(cmd: ChatCommand):
    task = get_restart_task(cmd.room.room_id)
    if task is None:
        await cmd.reply("No restart task found.")
        return
    time_left = task.get_time_left()
    if time_left <= 0:
        await cmd.reply("A restart is currently in progress.")
        return
    if task.finished():
        await cmd.reply("No restart task found.")
        return
    task_label = task.label
    out_text = f"Time left until restart: {format_seconds(time_left, TimeSize.LONG, 2, False)}."
    if task_label:
        out_text += f" Reason: {task_label}."
    if task.future_pause_reason and not task.paused():
        out_text += f" Will pause for {task.future_pause_reason}."
    if task.paused():
        if task.pause_event_name:
            out_text += f" (paused: {task.pause_event_name})"
        else:
            out_text += f" (paused)"
    await cmd.reply(out_text)

async def welcome_msg_cmd(cmd: ChatCommand):
    await first_time_joiner(cmd)

async def toggle_auto_restart_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    channel = get_channel_data(cmd.room.room_id)
    if channel is None:
        await cmd.reply("Channel not found.")
        return
    if channel['restart_period'] == 0:
        await cmd.reply("Auto restart period is not configured.")
        return
    old_value = channel['auto_restart']
    channel['auto_restart'] = not channel['auto_restart']
    await cmd.reply(f"Auto restart is now {'enabled' if channel['auto_restart'] else 'disabled'}.")
    if old_value == True:
        restart_task = get_restart_task(cmd.room.room_id)
        if restart_task and restart_task.label == "Scheduled restart":
            restart_task.cancel()

# Command handlers for pausing/resuming monitoring
async def toggle_bot_monitor_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    global monitoring_paused
    monitoring_paused = not monitoring_paused   
    await cmd.reply("RavenBot monitoring is now " + ("PAUSED." if monitoring_paused else "RESUMED."))

async def ping_cmd(cmd: ChatCommand):
    await cmd.reply("Pong!")

async def backup_state_data_cmd(cmd: ChatCommand):
    if not (cmd.user.mod or cmd.room.room_id == cmd.user.id):
        return
    await backup_state_data_routine(cmd.chat)
    await cmd.reply("Okay")



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
    chat.register_command('hi', test_cmd)
    chat.register_command('ping', ping_cmd)
    chat.register_command('towns', towns_cmd)
    chat.register_command('update', update_cmd)
    chat.register_command('event', event_cmd)
    chat.register_command('mult', multiplier_cmd)
    chat.register_command('uptime', uptime_cmd)
    chat.register_command('system', system_cmd)
    chat.register_command('rfram', ravenfall_ram_cmd)
    chat.register_command('welcomemsg', welcome_msg_cmd)
    chat.register_command('exprate', exprate_cmd)
    chat.register_command('expirate', exprate_cmd)
    chat.register_command('char', chracter_cmd)
    chat.register_command('show', chracter_cmd)
    chat.register_command('character', chracter_cmd)
    chat.register_command('restartrf', ravenfall_restart_cmd)
    chat.register_command('restartrfbot', ravenbot_restart_cmd)
    chat.register_command('rfrestart', ravenfall_restart_cmd)
    chat.register_command('rfbotrestart', ravenbot_restart_cmd)
    chat.register_command('rfqueuerestart', ravenfall_queue_restart_cmd)
    chat.register_command('rfqrestart', ravenfall_queue_restart_cmd)
    chat.register_command('rfcancelrestart', ravenfall_queue_restart_cancel_cmd)
    chat.register_command('rfcrestart', ravenfall_queue_restart_cancel_cmd)
    chat.register_command('postpone', postpone_restart_cmd)
    chat.register_command('rfrestartstatus', get_restart_time_left_cmd)
    chat.register_command('rfrstatus', get_restart_time_left_cmd)
    chat.register_command('autorestart', toggle_auto_restart_cmd)
    chat.register_command('togglebotmonitor', toggle_bot_monitor_cmd)
    chat.register_command('backup', backup_state_data_cmd)

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

