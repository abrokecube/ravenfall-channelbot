from ..commands import Context, Commands, checks, parameter
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager
from ..ravenfallrestarttask import RestartReason
from utils.format_time import format_seconds, TimeSize
from utils.commands_rf import RFChannelConverter
from ..ravenfallchannel import RFChannel
from ..command_enums import UserRole
from ..command_utils import HasRole

class GameCog(Cog):
    def __init__(self, rf_manager: RFChannelManager, **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager
    
    @Cog.command(name="update", help="Updates the town boost")
    @parameter("all_", display_name="all", aliases=["a"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def update(self, ctx: Context, channel: RFChannel = 'this', all_: bool = False):
        town_boost = await channel.get_town_boost()
        msg_text = f"{channel.ravenbot_prefixes[0]}town {town_boost[0].skill.lower()}"
        await channel.send_chat_message(msg_text)

    @Cog.command(
        name="rfrestartstatus",
        help="Gets the restart status",
        aliases=[
            'restartstatus', 
            'rsstatus', 
            'rf restart status',
            'rfrestart status',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    async def rfrestartstatus(self, ctx: Context, channel: RFChannel = 'this'):
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
        help="Toggles the auto restart feature",
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
    async def rfrestarttoggle(self, ctx: Context, channel: RFChannel = 'this'):
        old_value = channel.auto_restart
        channel.auto_restart = not channel.auto_restart
        await ctx.reply(f"Auto restart is now {'enabled' if channel.auto_restart else 'disabled'}.")
        if old_value == True:
            restart_task = channel.restart_task
            if restart_task and restart_task.reason == RestartReason.AUTO:
                restart_task.cancel()

    @Cog.command(
        name="rfrestartcancel",
        help="Cancels a restart task",
        aliases=[
            'rf restart cancel',
            'rfrestart cancel',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def rfrestartcancel(self, ctx: Context, channel: RFChannel = 'this'):
        restart_task = channel.restart_task
        if restart_task is None:
            await ctx.reply("No restart task found.")
            return
        restart_task.cancel()
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
    async def rfrestartpostpone(self, ctx: Context, seconds: float = 5*60, channel: RFChannel = 'this'):
        restart_task = channel.restart_task
        if restart_task is None:
            await ctx.reply("No restart task found.")
            return
        restart_task.postpone(seconds)
        await ctx.reply(f"Restart task postponed by {seconds} seconds.")

    @Cog.command(
        name='rfrestart',
        help='Queues a restart task',
        aliases=[
            'rf restart',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("reason", aliases=["r"])
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN, UserRole.MODERATOR))
    async def rfrestart(self, ctx: Context, seconds: float = 30, reason: str = "User restart", channel: RFChannel = 'this'):
        channel.queue_restart(seconds, label=reason, reason=RestartReason.USER, overwrite_same_reason=True)
        await ctx.reply(f"Restart queued. Restarting in {seconds}s.")

    @Cog.command(
        name="rfbotrestart",
        help="Restarts RavenBot",
        aliases=[
            'rf bot restart',
            'rfbot restart',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN, UserRole.MODERATOR))
    async def rfbotrestart(self, ctx: Context, channel: RFChannel = 'this'):
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
    async def togglebotmonitor(self, ctx: Context, channel: RFChannel = 'this', all_: bool = False):
        channel.monitoring_paused = not channel.monitoring_paused
        if all_:
            for channel in self.rf_manager.channels:
                channel.monitoring_paused = channel.monitoring_paused
            await ctx.reply("RavenBot monitoring is now " + ("PAUSED" if channel.monitoring_paused else "RESUMED") + " for all channels.")
        else:
            await ctx.reply("RavenBot monitoring is now " + ("PAUSED" if channel.monitoring_paused else "RESUMED") + " for this channel.")

    @Cog.command(
        name='backupstate',
        help='Backs up the state data',
        aliases=[
            'backup state',
        ]
    )
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("all_", display_name="all", aliases=["a"])
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def backupstate(self, ctx: Context, channel: RFChannel = 'this', all_: bool = False):
        if all_:
            for channel in self.rf_manager.channels:
                await channel.backup_state_data_routine()
            await ctx.reply("Backed up all state data.")
        else:
            await channel.backup_state_data_routine()
            await ctx.reply("Backed up state data for this channel.")

def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(GameCog, rf_manager=rf_manager, **kwargs)