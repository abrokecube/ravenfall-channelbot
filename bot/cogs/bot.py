from ..commands import CommandContext, Commands
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager

class BotStuffCog(Cog):
    def __init__(self, rf_manager: RFChannelManager, **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager
    
    @Cog.command(name="reloadstrings", help="Reloads localization strings")
    async def reloadstrings(self, ctx: CommandContext):
        if not (ctx.msg.user.mod or ctx.msg.room.room_id == ctx.msg.user.id):
            return
        
        do_all = ctx.args.get_flag(['a', 'all']) is not None
        if do_all:
            for channel in self.rf_manager.channels:
                channel.rfloc.load_definitions()
                channel.rfloc.load_translations()
            await ctx.reply("Strings reloaded for all channels!")
            return

        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        channel.rfloc.load_definitions()
        channel.rfloc.load_translations()
        await ctx.reply("Strings reloaded!")

def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(BotStuffCog, rf_manager=rf_manager, **kwargs)