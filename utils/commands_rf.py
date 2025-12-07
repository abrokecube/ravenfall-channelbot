from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from ..bot.ravenfallmanager import RFChannelManager
    from ..bot.ravenfallchannel import RFChannel
from ..bot.commands import Converter, Check, Context
from ..bot.command_exceptions import ArgumentConversionError
from ..bot.command_contexts import TwitchContext


class RFChannelConverter(Converter):
    title = "RFChannel"
    short_help = "A Ravenfall channel name"
    help = "The name of a Ravenfall channel monitored by the bot."
    
    async def convert(self, ctx: Context, arg: str) -> RFChannel:
        if not hasattr(ctx.command.cog, 'rf_manager'):
            raise ValueError("RFChannelConverter requires an rf_manager property in the cog.")
        rf_manager: 'RFChannelManager' = ctx.command.cog.rf_manager
        if not arg:
            if isinstance(ctx, TwitchContext):
                arg = ctx.data.room.name
            else:
                raise ArgumentConversionError("A channel must be specified.")
        channel_by_name = rf_manager.get_channel(channel_name=arg)
        channel_by_id = rf_manager.get_channel(channel_id=arg)
        channel = channel_by_name or channel_by_id
        if channel is None:
            raise ArgumentConversionError(f"Ravenfall channel '{arg}' not found.")
        return channel
    