from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from bot.ravenfallmanager import RFChannelManager
from bot.ravenfallchannel import RFChannel
from bot.commands import Converter, Check, Context
from bot.command_exceptions import ArgumentConversionError
from bot.command_contexts import TwitchContext, ServerContext
from bot.command_utils import Choice

from ravenpy import ravenpy
from ravenpy.ravenpy import Item


import re

class RFChannelConverter(Converter):
    title = "RFChannel"
    short_help = "A Ravenfall channel name"
    help = "A Ravenfall channel monitored by the bot."
    
    async def convert(ctx: Context, arg: str) -> RFChannel:
        if not hasattr(ctx.command.cog, 'rf_manager'):
            raise ValueError("RFChannelConverter requires an rf_manager property in the cog.")
        rf_manager: 'RFChannelManager' = ctx.command.cog.rf_manager
        if arg == 'this':
            if isinstance(ctx, TwitchContext):
                query = ctx.data.room.name
            elif isinstance(ctx, ServerContext):
                query = ctx.data.room_name
            else:
                raise ArgumentConversionError("A channel must be specified.")
        else:
            query = arg
        channel_by_name = rf_manager.get_channel(channel_name=query)
        channel_by_id = rf_manager.get_channel(channel_id=query)
        channel = channel_by_name or channel_by_id
        if channel is None:
            if arg == 'this':
                raise ArgumentConversionError("A channel must be specified.")
            else:
                raise ArgumentConversionError(f"Ravenfall channel '{arg}' not found.")
        return channel

class RFItemConverter(Converter):
    title = "Item"
    short_help = "An item name"
    help = "An item name"
    
    async def convert(ctx: Context, arg: str) -> Item:
        item_search_results = ravenpy.search_item(arg, limit=1)
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
    
    async def convert(ctx: Context, arg: str):
        is_valid = is_twitch_username(arg)
        if not is_valid:
            raise ArgumentConversionError("Not a valid username.")
        return arg.lstrip("@").replace("\U000e0000", '').replace("|","").replace("/","")

class _RFSkill(Choice):
    def __init__(self, case_sensitive=False):
        definition = {
            "Attack": ['atk', 'att'],
            "Defense": ['def'],
            "Strength": ['str'],
            "Health": ['hp'],
            "Woodcutting": ['wood', 'chop', 'wdc', 'chomp'],
            "Mining": ['mine', 'min'],
            "Crafting": ['craft'],
            "Cooking": ['cook', "ckn"],
            "Farming": ['farm', 'fm'],
            "Slayer": ['slay'],
            "Magic": [],
            "Ranged": ["range"],
            "Sailing": ['sail'],
            "Healing": ['heal'],
            "Gathering": ["gath"],
            "Alchemy": ["brew", "alch"],
            "CombatLevel": ["combat"]
        }
        super().__init__(definition, "Ravenfall skill", case_sensitive)

RFSkill = _RFSkill()
