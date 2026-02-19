"""Testing and debug tools for Ravenfall-related components.

Provides owner-only debug commands for inspecting manager and channel state.
"""
from ..commands.cog import Cog
from ..commands.events import CommandEvent
from ..commands.decorators import command, checks, parameter
from ..commands.checks import MinPermissionLevel
from ..commands.enums import UserRole
from ..commands.converters import RFChannelConverter
from ..ravenfallchannel import RFChannel
from utils.utils import upload_to_pastes
import inspect
import logging

logger = logging.getLogger(__name__)

class DebugCog(Cog):
    @command(name="debug global", help="Get properties of the global context")
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def debug_manager(self, ctx: CommandEvent, property: str):
        """Return a property value from the RFChannelManager for debugging."""
        result = getattr(self.global_context, property, "Invalid property")
        result_text = f"{property}: {result}"
        if len(result_text) > 300:
            url = await upload_to_pastes(result_text)
            await ctx.message.reply(f"Result too long. {url}")
        else:
            await ctx.message.reply(result_text)
    
    @command(name="eval", help="Eval a Python expression with access to the global context, rfchannel, and the command event")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("expr", display_name="expression", greedy=True)
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def eval_rf(self, ctx: CommandEvent, expr: str, *, channel: RFChannel = 'this'):
        """Evaluate `expr` in a restricted local context for debugging.

        WARNING: owner-only and can execute arbitrary code.
        """
        local_ctx = {
            "g_ctx": self.global_context,
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
        if len(result_text) > 300:
            url = await upload_to_pastes(result_text)
            await ctx.message.reply(f"Result too long. {url}")
        elif len(result_text) == 0:
            await ctx.message.reply("No result.")
        else:
            await ctx.message.reply(result_text)
            
    @command(name="translate", help="Translate a string")
    @parameter("channel", aliases=["channel", "c"], converter=RFChannelConverter)
    @parameter("string", greedy=True)
    @checks(MinPermissionLevel(UserRole.BOT_ADMINISTRATOR))
    async def translate_string(self, ctx: CommandEvent, string: str, *, channel: RFChannel = 'this', **kwargs):
        """Translate `string` using the channel's localization system."""
        matched = channel.rfloc.identify_string(string)
        key_name = "No match"
        if matched:
            key_name = matched.key
            
        translated = channel.rfloc.s(string, **kwargs)
        
        translation_status = "No replacement"
        if key_name in channel.rfloc.translated_strings:
            translation_status = "Translated"
            
        await ctx.message.reply(f"Key: {key_name} - {translation_status} - {translated}")

