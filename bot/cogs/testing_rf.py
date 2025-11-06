from ..commands import CommandContext, Commands
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager
from utils.utils import upload_to_pastes
import os

class TestingRFCog(Cog):
    def __init__(self, rf_manager: RFChannelManager, **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager
    
    @Cog.command(name="debug manager", help="Get properties of the RFChannelManager")
    async def debug_manager(self, ctx: CommandContext):
        if os.getenv("OWNER_TWITCH_ID") != ctx.msg.user.id:
            return
        result = self.rf_manager.__dict__.get(ctx.args.args[0], "Invalid property")
        result_text = f"{ctx.args.args[0]}: {result}"
        if len(result_text) > 480:
            url = await upload_to_pastes(result_text)
            await ctx.reply(f"Result too long. {url}")
        else:
            await ctx.reply(result_text)

    @Cog.command(name="debug channel", help="Get properties of a channel")
    async def debug_channel(self, ctx: CommandContext):
        if os.getenv("OWNER_TWITCH_ID") != ctx.msg.user.id:
            return
        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        result = channel.__dict__.get(ctx.args.args[0], "Invalid property")
        result_text = f"{ctx.args.args[0]}: {result}"
        if len(result_text) > 480:
            url = await upload_to_pastes(result_text)
            await ctx.reply(f"Result too long. {url}")
        else:
            await ctx.reply(result_text)
    
    @Cog.command(name="restore auto raids", help="Restore auto raids")
    async def restore_auto_raids(self, ctx: CommandContext):
        if os.getenv("OWNER_TWITCH_ID") != ctx.msg.user.id:
            return
        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        await channel.restore_auto_raids()
        await ctx.reply("Auto raids restored.")

    @Cog.command(name="restore sailors", help="Restore sailors")
    async def restore_sailors(self, ctx: CommandContext):
        if os.getenv("OWNER_TWITCH_ID") != ctx.msg.user.id:
            return
        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        await channel.restore_sailors()
        await ctx.reply("Sailors restored.")

    @Cog.command(name="fetch training", help="Fetch training")
    async def fetch_training(self, ctx: CommandContext):
        if os.getenv("OWNER_TWITCH_ID") != ctx.msg.user.id:
            return
        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        if channel is None:
            return
        await channel.fetch_all_training()
        await ctx.reply("Training fetched.")

def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(TestingRFCog, rf_manager=rf_manager, **kwargs)