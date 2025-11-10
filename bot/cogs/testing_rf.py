from ..commands import CommandContext, Commands
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager
from utils.utils import upload_to_pastes
import os
import inspect
import logging

logger = logging.getLogger(__name__)

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

    @Cog.command(name="eval", help="Eval a Python expression with access to rf_manager, channel, and ctx")
    async def eval_rf(self, ctx: CommandContext):
        # Owner-only safeguard
        if os.getenv("OWNER_TWITCH_ID") != ctx.msg.user.id:
            return
        # Join the args into a single expression string
        expr = " ".join(ctx.args.args).strip()
        if not expr:
            await ctx.reply("Usage: eval <python expression>")
            return
        channel = self.rf_manager.get_channel(channel_id=ctx.msg.room.room_id)
        # Provide a minimal, explicit local context
        local_ctx = {
            "rf_manager": self.rf_manager,
            "manager": self.rf_manager,
            "channel": channel,
            "ctx": ctx,
        }
        
        try:
            logger.info(f"Evaluating expression: {expr} in channel {channel.channel_name if channel else 'N/A'}")
            result = eval(expr, {}, local_ctx)
            if inspect.isawaitable(result):
                result = await result
            result_text = repr(result)
            logger.info(f"Eval result: {result_text}")
        except Exception as e:
            result_text = f"Error: {e!r}"
        # Upload long responses
        if len(result_text) > 480:
            url = await upload_to_pastes(result_text)
            await ctx.reply(f"Result too long. {url}")
        elif len(result_text) == 0:
            await ctx.reply("No result.")
        else:
            await ctx.reply(result_text)

def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(TestingRFCog, rf_manager=rf_manager, **kwargs)