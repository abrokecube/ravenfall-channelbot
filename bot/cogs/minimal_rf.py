from ..commands import Context, Commands
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager

class MinimalCog(Cog):
    def __init__(self, rf_manager: RFChannelManager, **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager
    
    @Cog.command(name="hi", help="Says hi")
    async def hi(self, ctx: Context):
        await ctx.reply(f"hi {ctx.author}")

def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(MinimalCog, rf_manager=rf_manager, **kwargs)