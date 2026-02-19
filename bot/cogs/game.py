"""Game cog with commands for Ravenfall control.

Provides restart, monitoring, backup and utility commands used to manage
Ravenfall servers and in-channel actions.
"""

from ..commands.events import CommandEvent
from ..commands.global_context import GlobalContext
from ..commands.checks import MinPermissionLevel
from ..commands.converters import RangeInt, RFChannelConverter, RFItemConverter
from ..commands.decorators import command, checks, parameter, cooldown
from ..commands.cog import Cog
from ..commands.enums import UserRole, BucketType
from ..commands.exceptions import CommandError
# from ..commands import CommandEvent, Commands, checks, parameter, cooldown
# from ..commands_old.cog import Cog
# from ..command_enums import UserRole, BucketType
# from ..command_utils import HasRole, RangeInt
# from ..command_exceptions import CommandError

from ..ravenfallchannel import RFChannel
from ..ravenfallmanager import RFChannelManager
from ..ravenfallrestarttask import RestartReason

from utils.format_time import format_seconds, TimeSize
from utils.utils import pl, pl2
from ..multichat_command import send_multichat_command, get_char_info, get_scroll_counts
from ..rf_webops_client import WebOpsClient
from ravenpy.ravenpy import Item
from ravenpy import ravenpy
from ravenpy.itemdefs import Items
from utils.utils import upload_to_pastes
from ..models import RFChannelEvent, GameMultiplier

import logging
logger = logging.getLogger(__name__)
import asyncio
from datetime import datetime, timezone
from typing import Dict


class GameCog(Cog):
    """Cog providing game control commands.

    Includes commands for restarting Ravenfall, managing monitoring and backups,
    and small utility actions such as refreshing town boosts.
    """
    def __init__(self, event_manager, rf_webops_url="http://pc2-mobile:7102"):
        super().__init__(event_manager)
        self.rf_webops = WebOpsClient(rf_webops_url)
        self.web_op_lock = asyncio.Lock()
    
    @command(name="updateboost", aliases=["update", "refreshboost"])
    @parameter("all_", display_name="all", aliases=["a"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @cooldown(1, 20, [BucketType.CHANNEL])
    async def update(self, ctx: CommandEvent, *, channel: RFChannel = 'this', all_: bool = False):
        """Refreshes the town boost
        
        Args:
            channel: Target channel.
            all_: Refresh for all channels.
        """
        channels = []
        if all_:
            channels = self.global_context.ravenfall_manager.channels
        else:
            channels = [channel]

        tasks = []
        for channel_ in channels:
            town_boost = await channel_.get_town_boost()
            msg_text = f"{channel_.ravenbot_prefixes[0]}town {town_boost[0].skill.lower()}"
            tasks.append(channel_.send_chat_message(msg_text))
        await asyncio.gather(*tasks, return_exceptions=True)

    @command(
        name="rfrestartstatus",
        aliases=[
            'restartstatus', 
            'rsstatus', 
            'rf restart status',
            'rfrestart status',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def rfrestartstatus(self, ctx: CommandEvent, *, channel: RFChannel = 'this'):
        """Reports the current Ravenfall restart task.
        
        Args:
            channel: Target channel.
        """
        restart_task = channel.restart_task
        ch = 'locked' if channel.channel_restart_lock.locked() else 'unlocked'
        gl = 'locked' if channel.global_restart_lock.locked() else 'unlocked'

        if restart_task is None:
            await ctx.message.reply("No restart task found.")
            return
        if restart_task.finished():
            await ctx.message.reply(f"Last restart task finished. (status: {restart_task.get_status().value}, ch: {ch}, gl: {gl})")
            return
        time_left = restart_task.get_time_left()
        if time_left <= 0:
            await ctx.message.reply(f"A restart is currently in progress. (status: {restart_task.get_status().value}, ch: {ch}, gl: {gl})")
            return
        task_label = restart_task.label
        out_text = f"Time left until restart: {format_seconds(time_left, TimeSize.LONG, 2, False)}."
        if task_label:
            out_text += f" Reason: {task_label}."
        if restart_task.future_pause_reason and not restart_task.paused():
            out_text += f" Will pause for {restart_task.future_pause_reason}."
        if restart_task.paused():
            if restart_task.pause_event_name:
                out_text += f" (paused: {restart_task.pause_event_name})"
            else:
                out_text += f" (paused)"
        await ctx.message.reply(out_text)

    @command(
        name="rfrestarttoggle",
        aliases=[
            'rfrestartauto',
            'rf restart auto',
            'rfrestart auto',
            'rf restart toggle',
            'rfrestart toggle',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def rfrestarttoggle(self, ctx: CommandEvent, *, channel: RFChannel = 'this'):
        """Toggles the auto restart scheduler.
        
        Args:
            channel: Target channel.
        """
        old_value = channel.auto_restart
        channel.auto_restart = not channel.auto_restart
        await ctx.message.reply(f"Auto restart is now {'enabled' if channel.auto_restart else 'disabled'}.")
        if old_value == True:
            restart_task = channel.restart_task
            if restart_task and restart_task.reason == RestartReason.AUTO:
                restart_task.cancel()

    @command(
        name="rfrestartcancel",
        aliases=[
            'rf restart cancel',
            'rfrestart cancel',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def rfrestartcancel(self, ctx: CommandEvent, *, channel: RFChannel = 'this'):
        """Cancels the current restart task.
        
        Args:
            channel: Target channel.
        """
        if not channel.cancel_restart():
            await ctx.message.reply("No restart task found.")
            return
        await ctx.message.reply("Restart task cancelled.")

    @command(
        name='rfrestartpostpone',
        help='Postpones a restart task',
        aliases=[
            'rfrestart postpone',
            'rf restart postpone',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    async def rfrestartpostpone(self, ctx: CommandEvent, seconds: int = 5*60, *, channel: RFChannel = 'this'):
        """Postpones the current restart task.
        
        Args:
            channel: Target channel.
        """
        restart_task = channel.restart_task
        if restart_task is None:
            await ctx.message.reply("No restart task found.")
            return
        restart_task.postpone(seconds)
        await ctx.message.reply(f"Restart task postponed by {seconds} seconds.")

    @command(
        name='rfrestart',
        aliases=[
            'rf restart',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("reason", aliases=["r"])
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    async def rfrestart(self, ctx: CommandEvent, seconds: int = 30, *, reason: str = "User restart", channel: RFChannel = 'this'):
        """Creates a new restart task.
        
        Args:
            channel: Target channel.
        """
        channel.queue_restart(seconds, label=reason, reason=RestartReason.USER, overwrite_same_reason=True)
        await ctx.message.reply(f"Restart queued. Restarting in {seconds}s.")

    @command(
        name="rfbotrestart",
        aliases=[
            'rf bot restart',
            'rfbot restart',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    async def rfbotrestart(self, ctx: CommandEvent, *, channel: RFChannel = 'this'):
        """Restarts RavenBot.
        
        Args:
            channel: Target channel.
        """
        await channel.restart_ravenbot()
        await ctx.message.reply("Okay")
    
    @command(
        name='togglebotmonitor',
        help='Toggles the bot monitoring',
        aliases=[
            'toggle bot monitor',
            'botmonitor toggle',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("all_", display_name="all", aliases=["a"])
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def togglebotmonitor(self, ctx: CommandEvent, *, channel: RFChannel = 'this', all_: bool = False):
        """Toggles the RavenBot monitor.
        
        Args:
            channel: Target channel.
        """
        channel.monitoring_paused = not channel.monitoring_paused
        if all_:
            for channel_ in self.global_context.ravenfall_manager.channels:
                channel_.monitoring_paused = channel.monitoring_paused
            await ctx.message.reply("RavenBot monitoring is now " + ("PAUSED" if channel.monitoring_paused else "RESUMED") + " for all channels.")
        else:
            await ctx.message.reply("RavenBot monitoring is now " + ("PAUSED" if channel.monitoring_paused else "RESUMED") + " for this channel.")

    @command(
        name='backupstate',
        aliases=[
            'backup state',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("all_", display_name="all", aliases=["a"])
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    async def backupstate(self, ctx: CommandEvent, *, channel: RFChannel = 'this', all_: bool = False):
        """Creates a copy of the current state_data.json.
        
        Args:
            channel: Target channel.
        """
        if all_:
            tasks = []
            for channel in self.global_context.ravenfall_manager.channels:
                tasks.append(channel.backup_state_data_routine())
            await asyncio.gather(*tasks)
            await ctx.message.reply("Backed up all state data.")
        else:
            await channel.backup_state_data_routine()
            await ctx.message.reply("Backed up state data for this channel.")
            
    @command(name="ds")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def ds(self, ctx: CommandEvent, *, channel: RFChannel = 'this'):
        """Use one of my Dungeon scrolls.

        Args:
            channel: Target channel.
        """
        queue_len = channel.get_scroll_queue_length()
        if queue_len > 0:
            raise CommandError(f"There {pl2(queue_len, 'is', 'are', False)} currently {pl(queue_len, 'scrolls')} in the queue. Wait until the queue is empty.")
        scrolls = await get_scroll_counts(channel.channel_id)
        if scrolls["data"]["channel"]["Dungeon Scroll"] == 0:
            raise CommandError("Currently out of dungeon scrolls.")
        if channel.is_restarting():
            raise CommandError("Ravenfall is restarting")
        if channel.get_seconds_until_restart() <= 60:
            raise CommandError("Ravenfall will restart soon")
        if channel.event == RFChannelEvent.DUNGEON:
            raise CommandError("There is currently an active dungeon")
        if channel.event == RFChannelEvent.RAID:
            raise CommandError("There is currently an active raid")
        await send_multichat_command("?ds", "0", "", channel.channel_id, channel.channel_name)
    
    @command(name="rs")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def rs(self, ctx: CommandEvent, *, channel: RFChannel = 'this'):
        """Uses one of my Raid scrolls.

        Args:
            channel: Target channel.
        """
        queue_len = channel.get_scroll_queue_length()
        if queue_len > 0:
            raise CommandError(f"There {pl2(queue_len, 'is', 'are', False)} currently {pl(queue_len, 'scrolls')} in the queue. Wait until the queue is empty.")
        scrolls = await get_scroll_counts(channel.channel_id)
        if scrolls["data"]["channel"]["Raid Scroll"] == 0:
            raise CommandError("Currently out of raid scrolls.")
        if channel.is_restarting():
            raise CommandError("Ravenfall is restarting")
        if channel.get_seconds_until_restart() <= 60:
            raise CommandError("Ravenfall will restart soon")
        if channel.event == RFChannelEvent.DUNGEON:
            raise CommandError("There is currently an active dungeon")
        if channel.event == RFChannelEvent.RAID:
            raise CommandError("There is currently an active raid")
        await send_multichat_command("?rs", "0", "", channel.channel_id, channel.channel_name)
    
    @command(name="exps")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("count", converter=RangeInt(1, 99))
    async def exps(self, ctx: CommandEvent, count: int = 99, *, channel: RFChannel = 'this'):
        """Uses my exp multiplier scroll(s).

        Args:
            count: Number of exp scrolls to use.
            channel: Target channel.
        """
        
        tasks = [
            channel.get_query("select * from multiplier"),
            self.global_context.ravennest.get_global_mult()
        ]
        m_client, m_server = await asyncio.gather(*tasks, return_exceptions=True)
        m_client: GameMultiplier
        m_server: ravenpy.ExpMult
        if (isinstance(m_client, Exception)):
            raise CommandError("Ravenfall is offline. Try again later.")
        if (isinstance(m_server, Exception)):
            raise CommandError("RavenNest is offline. Try again later.")

        scrolls = await get_scroll_counts(channel.channel_id)
        scrolls_remaining = scrolls["data"]["total"]["Exp Multiplier Scroll"]
        if scrolls_remaining == 0:
            raise CommandError("Currently out of exp multiplier scrolls.")

        if m_server.multiplier > 1:
            s_duration = (m_server.end_time - m_server.start_time).total_seconds()
            s_remaining = (m_server.end_time - datetime.now(tz=timezone.utc)).total_seconds()
        else:
            s_duration = 0
            s_remaining = 0
            
        if m_client["multiplier"] > 1:
            c_duration = m_client["duration"]
            c_remaining = m_client["timeleft"]
        else:
            c_duration = 0
            c_remaining = 0
        
        if c_duration >= s_duration:
            duration = c_duration
            remaining = c_remaining
            multiplier_value = m_client['multiplier']
        else:
            duration = s_duration
            remaining = s_remaining
            multiplier_value = m_server.multiplier
            
        if multiplier_value == 100:
            raise CommandError("Multiplier is already maxed.")
        
        count = min(count, 100 - multiplier_value)
        if count > scrolls_remaining:
            raise CommandError("There are not enough scrolls in stock.")
        
        elif multiplier_value > 1 and remaining / duration < 0.8:
            raise CommandError("Wait for the current multiplier to expire before using this command again.")
        
        
        await send_multichat_command(f"?exps {count}", "0", "", channel.channel_id, channel.channel_name)
    
    @command(name="fs")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def fs(self, ctx: CommandEvent, *, channel: RFChannel = 'this'):
        """Use one of my ferry scrolls.

        Args:
            channel: Target channel.
        """
        try:
            f = await channel.get_query("select * from ferry")
        except Exception:
            raise CommandError("Ravenfall is offline. Try again later.")
        
        scrolls = await get_scroll_counts(channel.channel_id)
        if scrolls["data"]["channel"]["Ferry Scroll"] == 0:
            raise CommandError("Currently out of ferry scrolls.")
        
        if f['boost']['isactive']:
            raise CommandError(f"There is currently an active ferry boost, ending in {format_seconds(f['boost']['remainingtime'], size=TimeSize.LONG, include_zero=False)}.")
        await send_multichat_command("?fs", "0", "", channel.channel_id, channel.channel_name)
 
    @command(name="channelscrolls", aliases=['sharedscrolls'])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def scrolls(self, ctx: CommandEvent, *, channel: RFChannel = 'this'):
        """Lists the available scroll stock.

        Args:
            channel: Target channel.
        """
        scrolls = await get_scroll_counts(channel.channel_id)
        scroll_names = {
            "Raid Scroll": "channel",
            "Dungeon Scroll": "channel",
            "Exp Multiplier Scroll": "total",
            "Ferry Scroll": "channel",
        }
        scroll_list = []
        for name, scope in scroll_names.items():
            count = scrolls["data"][scope][name]
            scroll_list.append(f"{pl(count, name)}")
        await ctx.message.reply(f"Available channel scrolls: {', '.join(scroll_list)}")


    @command(name="restockscrolls")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("item", regex=r"^[a-zA-Z ]+$", converter=RFItemConverter)
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    async def restockscrolls(self, ctx: CommandEvent, item: Item, count: int, *, channel: RFChannel = 'this'):
        """Restocks scrolls in the loyalty shop.

        Args:
            item: The scroll item to restock.
            count: The amount to restock.
            channel: Target channel.
        """
        if self.web_op_lock.locked():
            raise CommandError("There is currently an ongoing operation. Try again later.")
        
        if item.id not in (Items.ExpMultiplierScroll.value, Items.RaidScroll.value, Items.DungeonScroll.value):
            raise CommandError("Item must be an exp, raid or dungeon scroll.")
        
        use_all_users = False
        if item.id == Items.ExpMultiplierScroll.value:
            use_all_users = True
            
        chars = await get_char_info()
        if chars['status'] != 200:
            raise CommandError("Could not get character info.")
        
        char_list = []
        users_used = set()
        for char in chars["data"]:
            username = char["user_name"]
            if char["channel_id"] == channel.channel_id and not username in users_used:
                char_list.append({"username": username, "id": str(char['index'])})
                users_used.add(username)
        if use_all_users:
            for char in chars["data"]:
                username = char["user_name"]
                if not username in users_used:
                    char_list.append({"username": username, "id": str(char['index'])})
                    users_used.add(username)

        await ctx.message.reply(f"Restocking {count}x {item.name}, please wait...")
        item_id_map = {
            Items.ExpMultiplierScroll.value: "exp_multiplier_scroll",
            Items.RaidScroll.value: "raid_scroll",
            Items.DungeonScroll.value: "dungeon_scroll",
        }
        try:
            async with self.web_op_lock:
                result = await self.rf_webops.redeem_items(item_id_map[item.id], count, char_list)
        except asyncio.TimeoutError:
            raise CommandError("Task timed out.")
        if result['status'] == "success":
            await ctx.message.reply(f"Successfully restocked {sum(result['redeemed'].values())}x {item.name}.")
        else:
            raise CommandError("Failed to restock scrolls.")

    @command(name="countloyaltypoints")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(MinPermissionLevel(UserRole.MODERATOR))
    async def countloyaltypoints(self, ctx: CommandEvent, *, channel: RFChannel = 'this'):
        """Gets the total loyalty points across characters in a channel. Will take a few minutes to count.
        
        Args:
            channel: Target channel.
        """
        if self.web_op_lock.locked():
            raise CommandError("There is currently an ongoing operation. Try again later.")

        chars = await get_char_info()
        if chars['status'] != 200:
            raise CommandError("Could not get character info.")
        channel_char_list = set()
        char_list = set()
        for char in chars["data"]:
            if char["channel_id"] == channel.channel_id:
                channel_char_list.add(char["user_name"])
            char_list.add(char['user_name'])
        await ctx.message.reply(f"Counting loyalty points, please wait...")
        try:
            async with self.web_op_lock:
                result = await self.rf_webops.get_total_loyalty_points(tuple(char_list))
        except asyncio.TimeoutError:
            raise CommandError("Task timed out.")
        if result['status'] != "success":
            raise CommandError("Failed to get loyalty points.")
        
        out_str = []
        out_str.append(f"Loyalty points info for {channel.channel_name}")
        out_str.append("")
        points_in_channel = 0
        total_points = 0
        for char_name in channel_char_list:
            points = result['breakdown'].get(char_name, 0)
            if points == -1:
                out_str.append(f"{char_name}: Failed to get points")
                continue
            points_in_channel += points
            total_points += points
            out_str.append(f"{char_name}: {points:,} points")
        out_str.append("")
        out_str.append("Characters not in this channel:")
        for char_name in char_list - channel_char_list:
            points = result['breakdown'].get(char_name, 0)
            if points == -1:
                out_str.append(f"{char_name}: Failed to get points")
                continue
            total_points += points
            out_str.append(f"{char_name}: {points:,} points")
        out_str.append("")
        out_str.append(f"Points in {channel.channel_name}: {points_in_channel:,} points")
        out_str.append(f"Total: {total_points:,} points")
        out_str.append("")
        out_url = await upload_to_pastes("\n".join(out_str))
        await ctx.message.reply(
            f"In this channel: {points_in_channel:,} points – Total: {result['total_points']:,} points – Breakdown: {out_url}"
        )

