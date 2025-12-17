from ..commands import Context, TwitchContext, Commands, checks, parameter
from ..command_enums import UserRole, Platform
from ..command_utils import HasRole
from ..command_exceptions import CommandError
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager
from ..ravenfallchannel import RFChannel
from ..process_watchdog_client import ProcessWatcherClient
from aiohttp import ClientResponseError
from utils.commands_rf import RFChannelConverter


class BotStuffCog(Cog):
    def __init__(self, rf_manager: RFChannelManager, watcher_url="http://127.0.0.1:8110", **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager
        self.watcher = ProcessWatcherClient(watcher_url)
    
    @Cog.command(name="reloadstrings")
    @parameter("all_", display_name="all", aliases=["a"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def reloadstrings(self, ctx: Context, all_: bool = False, channel: RFChannel = 'this'):
        """Reloads Ravenfall translation strings.
        
        Args: 
            all: Reloads strings for all channels.
            channel_name: The name of the channel to reload strings for.
        """        
        if all_:
            for _channel in self.rf_manager.channels:
                _channel.rfloc.load_definitions()
                _channel.rfloc.load_translations()
            await ctx.reply("Strings reloaded for all channels!")
            return

        channel.rfloc.load_definitions()
        channel.rfloc.load_translations()
        await ctx.reply("Strings reloaded!")
    
    @Cog.command(name="pause_monitoring")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def pause_monitoring(self, ctx: Context, channel: RFChannel = 'this'):
        """Pause channel monitoring
        
        Args:
            channel: Channel to pause monitoring for
        """
        if channel.monitoring_paused:
            await ctx.reply("Channel monitoring is already paused.")
            return
        channel.monitoring_paused = True
        await channel.stop()
        await ctx.reply("Channel monitoring paused.")
        
    @Cog.command(name="resumemonitoring", help="Resume channel monitoring")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def resume_monitoring(self, ctx: Context, channel: RFChannel = 'this'):
        """Resume channel monitoring
        
        Args:
            channel: Channel to resume monitoring for
        """
        if not channel.monitoring_paused:
            await ctx.reply("Channel monitoring is not paused.")
            return
        channel.monitoring_paused = False
        await channel.start()
        await ctx.reply("Channel monitoring resumed.")
    
    @Cog.command(name="startproc", help="Start a process")
    @parameter(name="process_name", greedy=True)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def start_process(self, ctx: Context, process_name: str):
        try:
            await self.watcher.start_process(process_name)
            await ctx.reply("Okay")
        except ClientResponseError:
            raise CommandError("Failed to start process")
        
    @Cog.command(name="stopproc", help="Stop a process")
    @parameter(name="process_name", greedy=True)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def stop_process(self, ctx: Context, process_name: str):
        try:
            await self.watcher.stop_process(process_name)
            await ctx.reply("Okas")
        except ClientResponseError:
            raise CommandError("Failed to stop process")
        
    @Cog.command(name="restartproc", help="Restart a process")
    @parameter(name="process_name", greedy=True)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def restart_process(self, ctx: Context, process_name: str):
        try:
            await self.watcher.restart_process(process_name)
            await ctx.reply("Okay")
        except ClientResponseError:
            raise CommandError("Failed to restart process")
        
        
    @Cog.command(name="listproc", help="List all processes")
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def list_processes(self, ctx: Context):
        try:
            processes = await self.watcher.get_processes()
            if not processes:
                await ctx.reply("There are no registered processes.")
                return
            out_str = []
            for name, status in processes.items():
                if status == "Running":
                    out_str.append(f"{name}: running")
                else:
                    out_str.append(f"{name}: stopped")
            response = ", ".join(out_str)
            await ctx.reply(response)
        except ClientResponseError:
            raise CommandError("Failed to get processes")
        
        
def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(BotStuffCog, rf_manager=rf_manager, **kwargs)