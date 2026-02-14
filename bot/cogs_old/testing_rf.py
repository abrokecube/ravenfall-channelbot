"""Testing and debug tools for Ravenfall-related components.

Provides owner-only debug commands for inspecting manager and channel state.
"""

from ..commands import Context, Commands, TwitchRedeemContext, TwitchContext, checks, parameter
from ..command_enums import UserRole, Platform
from ..command_utils import HasRole, TwitchOnly
from ..cog import Cog
from ..ravenfallmanager import RFChannelManager
from ..ravenfallchannel import RFChannel
from utils.commands_rf import RFChannelConverter
from utils.utils import upload_to_pastes
import os
import inspect
import logging

logger = logging.getLogger(__name__)

class TestingRFCog(Cog):
    """Owner-only debugging cog for RF manager and channel inspection."""
    def __init__(self, rf_manager: RFChannelManager, **kwargs):
        super().__init__(**kwargs)
        self.rf_manager = rf_manager
    
    @Cog.command(name="debug manager", help="Get properties of the RFChannelManager")
    @checks(HasRole(UserRole.BOT_OWNER))
    async def debug_manager(self, ctx: Context, property: str):
        """Return a property value from the RFChannelManager for debugging."""
        result = self.rf_manager.__dict__.get(property, "Invalid property")
        result_text = f"{property}: {result}"
        if len(result_text) > 480:
            url = await upload_to_pastes(result_text)
            await ctx.reply(f"Result too long. {url}")
        else:
            await ctx.reply(result_text)

    @Cog.command(name="debug channel", help="Get properties of a channel")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @checks(HasRole(UserRole.BOT_OWNER))
    async def debug_channel(self, ctx: Context, property: str, *, channel: RFChannel = 'this'):
        """Return a property value from `channel` for debugging."""
        result = channel.__dict__.get(property, "Invalid property")
        result_text = f"{property}: {result}"
        if len(result_text) > 480:
            url = await upload_to_pastes(result_text)
            await ctx.reply(f"Result too long. {url}")
        else:
            await ctx.reply(result_text)
    
    # @Cog.command(name="restore auto raids", help="Restore auto raids")
    # async def restore_auto_raids(self, ctx: Context):
    #     if os.getenv("OWNER_TWITCH_ID") != ctx.data.user.id:
    #         return
    #     channel = self.rf_manager.get_channel(channel_id=ctx.data.room.room_id)
    #     if channel is None:
    #         return
    #     await channel.restore_auto_raids()
    #     await ctx.reply("Auto raids restored.")

    # @Cog.command(name="restore sailors", help="Restore sailors")
    # async def restore_sailors(self, ctx: Context):
    #     if os.getenv("OWNER_TWITCH_ID") != ctx.data.user.id:
    #         return
    #     channel = self.rf_manager.get_channel(channel_id=ctx.data.room.room_id)
    #     if channel is None:
    #         return
    #     await channel.restore_sailors()
    #     await ctx.reply("Sailors restored.")

    # @Cog.command(name="fetch training", help="Fetch training")
    # async def fetch_training(self, ctx: Context):
    #     if os.getenv("OWNER_TWITCH_ID") != ctx.data.user.id:
    #         return
    #     channel = self.rf_manager.get_channel(channel_id=ctx.data.room.room_id)
    #     if channel is None:
    #         return
    #     await channel.fetch_all_training()
    #     await ctx.reply("Training fetched.")

    @Cog.command(name="eval", help="Eval a Python expression with access to rf_manager, channel, and ctx")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("expr", display_name="expression", greedy=True)
    @checks(HasRole(UserRole.BOT_OWNER))
    async def eval_rf(self, ctx: Context, expr: str, *, channel: RFChannel = 'this'):
        """Evaluate `expr` in a restricted local context for debugging.

        WARNING: owner-only and can execute arbitrary code.
        """
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
        except Exception as e:
            result_text = f"Error: {e!r}"
        logger.info(f"Eval result: {result_text}")
        # Upload long responses
        if len(result_text) > 480:
            url = await upload_to_pastes(result_text)
            await ctx.reply(f"Result too long. {url}")
        elif len(result_text) == 0:
            await ctx.reply("No result.")
        else:
            await ctx.reply(result_text)
            
    @Cog.command(name="translate", help="Translate a string")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("string", greedy=True)
    @checks(HasRole(UserRole.BOT_OWNER))
    async def translate_string(self, ctx: Context, string: str, *, channel: RFChannel = 'this', **kwargs):
        """Translate `string` using the channel's localization system."""
        matched = channel.rfloc.identify_string(string)
        key_name = "No match"
        if matched:
            key_name = matched.key
            
        translated = channel.rfloc.s(string, **kwargs)
        
        translation_status = "No replacement"
        if key_name in channel.rfloc.translated_strings:
            translation_status = "Translated"
            
        await ctx.reply(f"Key: {key_name} - {translation_status} - {translated}")

def setup(commands: Commands, rf_manager: RFChannelManager, **kwargs) -> None:
    """Load the testing cog with the given commands instance.
    
    Args:
        commands: The Commands instance to register commands with.
        rf_manager: The RFChannelManager instance to pass to the cog.
        **kwargs: Additional arguments to pass to the cog.
    """
    commands.load_cog(TestingRFCog, rf_manager=rf_manager, **kwargs)