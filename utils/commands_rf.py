from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from bot.ravenfallmanager import RFChannelManager
from bot.ravenfallchannel import RFChannel
from bot.commands import Converter, Check, Context
from bot.command_exceptions import ArgumentConversionError
from bot.command_contexts import TwitchContext

from ravenpy import ravenpy
from ravenpy.ravenpy import Item


import re

class RFChannelConverter(Converter):
    title = "RFChannel"
    short_help = "A Ravenfall channel name"
    help = "A Ravenfall channel monitored by the bot."
    
    async def convert(self, ctx: Context, arg: str) -> RFChannel:
        if not hasattr(ctx.command.cog, 'rf_manager'):
            raise ValueError("RFChannelConverter requires an rf_manager property in the cog.")
        rf_manager: 'RFChannelManager' = ctx.command.cog.rf_manager
        if arg == 'this':
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

class RFItemConverter(Converter):
    title = "Item"
    short_help = "An item name"
    help = "An item name"
    
    async def convert(self, ctx: Context, arg: str) -> Item:
        item_search_results = ravenpy.search_item(item_name, limit=1)
        if not item_search_results:
            raise ArgumentConversionError(f"Could not identify item '{arg}'. Please check your spelling")
        if item_search_results[0][1] < 85:
            raise ArgumentConversionError(f"Could not identify item '{arg}'. Please check your spelling")
        return item_search_results[0][0]

tw_username_re = re.compile(r"^@?[a-zA-Z0-9][\w]{2,24}$")
tw_username_f_re = re.compile(r"^@?[a-zA-Z0-9/|][\w/|]{2,24}$")
def is_twitch_username(text: str, pre_filter=False):
    if pre_filter:
        return bool(tw_username_f_re.match(text))
    else:
        return bool(tw_username_re.match(text))

class TwitchUsername(Converter):
    title = "Twitch username"
    short_help = "A valid Twitch username"
    help = "A valid Twitch username"
    
    async def convert(self, ctx: Context, arg: str):
        is_valid = is_twitch_username(arg)
        if not is_valid:
            raise ArgumentConversionError("Not a valid username.")
        return arg.lstrip("@").replace("\U000e0000", '').replace("|","").replace("/","")
