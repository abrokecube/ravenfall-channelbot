from ..commands import Context, TwitchContext, Commands, checks, parameter
from ..command_enums import UserRole, Platform
from ..command_utils import HasRole
from ..command_exceptions import CommandError
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager
from ..ravenfallchannel import RFChannel
from utils.commands_rf import RFChannelConverter
import os


class BotStuffCog(Cog):
    def __init__(self, rf_manager: RFChannelManager, **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager
    
    @Cog.command(name="reloadstrings")
    @parameter("all_", display_name="all", aliases=["a"])
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def reloadstrings(self, ctx: Context, all_: bool = False, channel: RFChannel = None):
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

        if isinstance(ctx, TwitchContext) and not channel_name:
            channel_name = ctx.data.room.name
        if not channel_name:
            raise CommandError("Missing channel_name")
        
        channel = self.rf_manager.get_channel(channel_name=channel_name)
        if channel is None:
            return
        channel.rfloc.load_definitions()
        channel.rfloc.load_translations()
        await ctx.reply("Strings reloaded!")
    
    @Cog.command(name="pause_monitoring")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER, UserRole.ADMIN))
    async def pause_monitoring(self, ctx: Context, channel: RFChannel = None):
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
    async def resume_monitoring(self, ctx: Context, channel: RFChannel = None):
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
        
        
def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(BotStuffCog, rf_manager=rf_manager, **kwargs)