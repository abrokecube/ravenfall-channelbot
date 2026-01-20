"""Game cog with commands for Ravenfall control.

Provides restart, monitoring, backup and utility commands used to manage
Ravenfall servers and in-channel actions.
"""

from ..commands import Context, Commands, checks, parameter, cooldown
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager
from ..ravenfallrestarttask import RestartReason
from utils.format_time import format_seconds, TimeSize
from utils.commands_rf import RFChannelConverter, RFItemConverter
from ..ravenfallchannel import RFChannel
from ..command_enums import UserRole, BucketType
from ..command_utils import HasRole, RangeInt
from ..command_exceptions import CommandError
from ..multichat_command import send_multichat_command, get_char_info
from ..rf_webops_client import WebOpsClient
from ravenpy.ravenpy import Item
from ravenpy import ravenpy
from ravenpy.itemdefs import Items
from utils.utils import upload_to_pastes

import logging
logger = logging.getLogger(__name__)
import asyncio

class GameCog(Cog):
    """Cog providing game control commands.

    Includes commands for restarting Ravenfall, managing monitoring and backups,
    and small utility actions such as refreshing town boosts.
    """
    def __init__(self, rf_manager: RFChannelManager, rf_webops_url="http://pc2-mobile:7102", **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager
        self.rf_webops = WebOpsClient(rf_webops_url)
    
    @Cog.command(name="updateboost", aliases=["update", "refreshboost"])
    @parameter("all_", display_name="all", aliases=["a"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @cooldown(1, 20, [BucketType.CHANNEL])
    async def update(self, ctx: Context, *, channel: RFChannel = 'this', all_: bool = False):
        """Refreshes the town boost
        
        Args:
            channel: Target channel.
            all_: Refresh for all channels.
        """
        channels = []
        if all_:
            channels = self.rf_manager.channels
        else:
            channels = [channel]

        tasks = []
        for channel_ in channels:
            town_boost = await channel_.get_town_boost()
            msg_text = f"{channel_.ravenbot_prefixes[0]}town {town_boost[0].skill.lower()}"
            tasks.append(channel_.send_chat_message(msg_text))
        await asyncio.gather(*tasks, return_exceptions=True)

    @Cog.command(
        name="rfrestartstatus",
        aliases=[
            'restartstatus', 
            'rsstatus', 
            'rf restart status',
            'rfrestart status',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def rfrestartstatus(self, ctx: Context, *, channel: RFChannel = 'this'):
        """Reports the current Ravenfall restart task.
        
        Args:
            channel: Target channel.
        """
        restart_task = channel.restart_task
        ch = 'locked' if channel.channel_restart_lock.locked() else 'unlocked'
        gl = 'locked' if channel.global_restart_lock.locked() else 'unlocked'

        if restart_task is None:
            await ctx.reply("No restart task found.")
            return
        if restart_task.finished():
            await ctx.reply(f"Last restart task finished. (status: {restart_task.get_status().value}, ch: {ch}, gl: {gl})")
            return
        time_left = restart_task.get_time_left()
        if time_left <= 0:
            await ctx.reply(f"A restart is currently in progress. (status: {restart_task.get_status().value}, ch: {ch}, gl: {gl})")
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
        await ctx.reply(out_text)

    @Cog.command(
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
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def rfrestarttoggle(self, ctx: Context, *, channel: RFChannel = 'this'):
        """Toggles the auto restart scheduler.
        
        Args:
            channel: Target channel.
        """
        old_value = channel.auto_restart
        channel.auto_restart = not channel.auto_restart
        await ctx.reply(f"Auto restart is now {'enabled' if channel.auto_restart else 'disabled'}.")
        if old_value == True:
            restart_task = channel.restart_task
            if restart_task and restart_task.reason == RestartReason.AUTO:
                restart_task.cancel()

    @Cog.command(
        name="rfrestartcancel",
        aliases=[
            'rf restart cancel',
            'rfrestart cancel',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def rfrestartcancel(self, ctx: Context, *, channel: RFChannel = 'this'):
        """Cancels the current restart task.
        
        Args:
            channel: Target channel.
        """
        if not channel.cancel_restart():
            await ctx.reply("No restart task found.")
            return
        await ctx.reply("Restart task cancelled.")

    @Cog.command(
        name='rfrestartpostpone',
        help='Postpones a restart task',
        aliases=[
            'rfrestart postpone',
            'rf restart postpone',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN, UserRole.MODERATOR))
    async def rfrestartpostpone(self, ctx: Context, seconds: int = 5*60, *, channel: RFChannel = 'this'):
        """Postpones the current restart task.
        
        Args:
            channel: Target channel.
        """
        restart_task = channel.restart_task
        if restart_task is None:
            await ctx.reply("No restart task found.")
            return
        restart_task.postpone(seconds)
        await ctx.reply(f"Restart task postponed by {seconds} seconds.")

    @Cog.command(
        name='rfrestart',
        aliases=[
            'rf restart',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("reason", aliases=["r"])
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN, UserRole.MODERATOR))
    async def rfrestart(self, ctx: Context, seconds: int = 30, *, reason: str = "User restart", channel: RFChannel = 'this'):
        """Creates a new restart task.
        
        Args:
            channel: Target channel.
        """
        channel.queue_restart(seconds, label=reason, reason=RestartReason.USER, overwrite_same_reason=True)
        await ctx.reply(f"Restart queued. Restarting in {seconds}s.")

    @Cog.command(
        name="rfbotrestart",
        aliases=[
            'rf bot restart',
            'rfbot restart',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN, UserRole.MODERATOR))
    async def rfbotrestart(self, ctx: Context, *, channel: RFChannel = 'this'):
        """Restarts RavenBot.
        
        Args:
            channel: Target channel.
        """
        await channel.restart_ravenbot()
        await ctx.reply("Okay")
    
    @Cog.command(
        name='togglebotmonitor',
        help='Toggles the bot monitoring',
        aliases=[
            'toggle bot monitor',
            'botmonitor toggle',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("all_", display_name="all", aliases=["a"])
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def togglebotmonitor(self, ctx: Context, *, channel: RFChannel = 'this', all_: bool = False):
        """Toggles the RavenBot monitor.
        
        Args:
            channel: Target channel.
        """
        channel.monitoring_paused = not channel.monitoring_paused
        if all_:
            for channel_ in self.rf_manager.channels:
                channel_.monitoring_paused = channel.monitoring_paused
            await ctx.reply("RavenBot monitoring is now " + ("PAUSED" if channel.monitoring_paused else "RESUMED") + " for all channels.")
        else:
            await ctx.reply("RavenBot monitoring is now " + ("PAUSED" if channel.monitoring_paused else "RESUMED") + " for this channel.")

    @Cog.command(
        name='backupstate',
        aliases=[
            'backup state',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("all_", display_name="all", aliases=["a"])
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def backupstate(self, ctx: Context, *, channel: RFChannel = 'this', all_: bool = False):
        """Creates a copy of the current state_data.json.
        
        Args:
            channel: Target channel.
        """
        if all_:
            tasks = []
            for channel in self.rf_manager.channels:
                tasks.append(channel.backup_state_data_routine())
            await asyncio.gather(*tasks)
            await ctx.reply("Backed up all state data.")
        else:
            await channel.backup_state_data_routine()
            await ctx.reply("Backed up state data for this channel.")
            
    @Cog.command(name="ds")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def ds(self, ctx: Context, *, channel: RFChannel = 'this'):
        """Use one of my Dungeon scrolls.

        Args:
            channel: Target channel.
        """
        await send_multichat_command("?ds", channel.channel_id, channel.channel_name, channel.channel_id, channel.channel_name)
    
    @Cog.command(name="rs")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def rs(self, ctx: Context, *, channel: RFChannel = 'this'):
        """Uses one of my Raid scrolls.

        Args:
            channel: Target channel.
        """
        await send_multichat_command("?rs", channel.channel_id, channel.channel_name, channel.channel_id, channel.channel_name)
    
    @Cog.command(name="exps")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("count", converter=RangeInt(1, 99))
    async def exps(self, ctx: Context, count: int = 99, *, channel: RFChannel = 'this'):
        """Uses my exp multiplier scroll(s).

        Args:
            count: Number of exp scrolls to use.
            channel: Target channel.
        """
        await send_multichat_command(f"?exps {count}", channel.channel_id, channel.channel_name, channel.channel_id, channel.channel_name)
    
    @Cog.command(name="fs")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def fs(self, ctx: Context, *, channel: RFChannel = 'this'):
        """Use one of my ferry scrolls.

        Args:
            channel: Target channel.
        """
        await send_multichat_command("?fs", channel.channel_id, channel.channel_name, channel.channel_id, channel.channel_name)
 
    @Cog.command(name="scrolls")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def scrolls(self, ctx: Context, *, channel: RFChannel = 'this'):
        """Lists the available scroll stock.

        Args:
            channel: Target channel.
        """
        await send_multichat_command("?scrolls", channel.channel_id, channel.channel_name, channel.channel_id, channel.channel_name)

    @Cog.command(name="restockscrolls")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("item", regex=r"^[a-zA-Z ]+$", converter=RFItemConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN, UserRole.MODERATOR))
    async def restockscrolls(self, ctx: Context, item: Item, count: int, *, channel: RFChannel = 'this'):
        """Restocks scrolls in the loyalty shop.

        Args:
            item: The scroll item to restock.
            count: The amount to restock.
            channel: Target channel.
        """
        if item.id not in (Items.ExpMultiplierScroll.value, Items.RaidScroll.value, Items.DungeonScroll.value):
            raise CommandError("Item must be an exp, raid or dungeon scroll.")
        chars = await get_char_info()
        if chars['status'] != 200:
            raise CommandError("Could not get character info.")
        char_list = []
        for char in chars["data"]:
            if char["channel_id"] == channel.channel_id:
                char_list.append({"username": char["user_name"], "id": str(char['index'])})
        await ctx.reply(f"Restocking {count}x {item.name}, please wait...")
        item_id_map = {
            Items.ExpMultiplierScroll.value: "exp_multiplier_scroll",
            Items.RaidScroll.value: "raid_scroll",
            Items.DungeonScroll.value: "dungeon_scroll",
        }
        result = await self.rf_webops.redeem_items(item_id_map[item.id], count, char_list)
        if result['status'] == "success":
            await ctx.reply(f"Successfully restocked {sum(result['redeemed'].values())}x {item.name}.")
        else:
            raise CommandError("Failed to restock scrolls.")

    @Cog.command(name="countloyaltypoints")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN, UserRole.MODERATOR))
    async def countloyaltypoints(self, ctx: Context, *, channel: RFChannel = 'this'):
        """Gets the total loyalty points across characters in a channel. Will take a few minutes to count.
        
        Args:
            channel: Target channel.
        """
        
        chars = await get_char_info()
        if chars['status'] != 200:
            raise CommandError("Could not get character info.")
        channel_char_list = set()
        char_list = set()
        for char in chars["data"]:
            if char["channel_id"] == channel.channel_id:
                channel_char_list.add(char["user_name"])
            char_list.add(char['user_name'])
        await ctx.reply(f"Counting loyalty points, please wait...")
        result = await self.rf_webops.get_total_loyalty_points(tuple(char_list))
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
        await ctx.reply(
            f"In this channel: {points_in_channel:,} points – Total: {result['total_points']:,} points – Breakdown: {out_url}"
        )

def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the game cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(GameCog, rf_manager=rf_manager, **kwargs)