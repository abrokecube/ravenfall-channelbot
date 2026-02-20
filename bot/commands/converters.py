from __future__ import annotations
from typing import TYPE_CHECKING, NamedTuple, Any, List, Dict
if TYPE_CHECKING:
    from ..commands.global_context import GlobalContext
from .events import CommandEvent, TwitchMessageEvent

from ravenpy import ravenpy
from ravenpy.ravenpy import Item as RFItem
    
from utils.utils import strjoin
from .exceptions import ArgumentConversionError

class BaseConverter:
    """To display a custom error message when conversion fails,
    raise command_exceptions.ArgumentConversionError in the convert method."""
    title: str = None
    short_help: str = None
    help: str = None

    @staticmethod
    async def convert(g_ctx: GlobalContext, event: CommandEvent, arg: str) -> Any:
        raise NotImplementedError

class Choice(BaseConverter):
    def __init__(self, definition: List[str] | Dict[str, List[str]], title=None, case_sensitive=False):
        super().__init__()
        string_map = {}
        choices = []
        if isinstance(definition, list):
            if case_sensitive:
                string_map = {x: x for x in definition}
            else:
                string_map = {x.lower(): x for x in definition}
            choices = definition
        elif isinstance(definition, dict):
            choices = list(definition.keys())
            if case_sensitive:
                string_map = {x: x for x in choices}
                for k, v in definition.items():
                    string_map.update({x: k for x in v})
            else:
                string_map = {x.lower(): x for x in choices}
                for k, v in definition.items():
                    string_map.update({x.lower(): k for x in v})
        else:
            raise TypeError()
        
        if title:
            self.title = title
        else:
            self.title = f"Choice ({len(choices)})"
        self.short_help = f"One of the following: {strjoin(', ', *choices, before_end='or ', include_conn_char_before_end=True)}"
        self.help = self.short_help
        self.case_sensitive = case_sensitive
        self.string_map = string_map
        
    async def convert(self, g_ctx: GlobalContext, ctx: CommandEvent, arg: str) -> str:
        if not arg in self.string_map:
            raise ArgumentConversionError(f"Choice '{arg}' is not a valid option. Valid choices: {self.short_help}")
        return self.string_map[arg]

import re
import glob 

class Regex(BaseConverter):
    title = "Regex"
    short_help = "A python regular expression"
    help = "A python regular expression"
    
    @staticmethod
    async def convert(g_ctx: GlobalContext, ctx: CommandEvent, arg: str) -> re.Pattern:
        try:
            return re.compile(arg)
        except Exception as e:
            raise ArgumentConversionError("Couldn't compile regex")

class Glob(BaseConverter):
    title = "Glob"
    short_help = "A glob pattern"
    help = "A glob pattern"
    
    @staticmethod
    async def convert(g_ctx: GlobalContext, ctx: CommandEvent, arg: str) -> re.Pattern:
        try:
            return re.compile(glob.translate(arg))
        except Exception as e:
            raise ArgumentConversionError("Couldn't compile glob expression")
        
class RangeInt(BaseConverter):
    def __init__(self, min_: int | None, max_: int | None):
        super().__init__()
        self.min = min_
        self.max = max_
        if min_ is not None and max_ is not None:
            self.title = f"Number ({min_}-{max_})"
            self.short_help = f"An integer in the range {min_} to {max_}"
            self.help = f"A whole number in the range {min_} to {max_}"
        elif min_ is None and max_ is not None:
            self.title = f"Number ({max_}-)"
            self.short_help = f"An integer less than or equal to {max_}"
            self.help = f"A whole number less than or equal to {max_}"
        elif min_ is not None and max_ is None:
            self.title = f"Number ({min_}+)"
            self.short_help = f"An integer greater than or equal to {min_}"
            self.help = f"A whole number greater than or equal to {min_}"
        else:
            raise ValueError("min_ or max_ need to be a number")
        
    async def convert(self, g_ctx: GlobalContext, ctx: CommandEvent, arg: str) -> int:
        try:
            number = int(arg)
        except ValueError as e:
            raise ArgumentConversionError("Expected an integer")
        
        if self.max is not None and number > self.max:
            raise ArgumentConversionError(f"Number is out of range! Maximum value: {self.max}")
        if self.min is not None and number < self.min:
            raise ArgumentConversionError(f"Number is out of range! Minimum value: {self.min}")
    
        return number

class RangeFloat(BaseConverter):
    def __init__(self, min_: float | None, max_: float | None):
        super().__init__()
        self.min = min_
        self.max = max_
        if min_ is not None and max_ is not None:
            self.title = f"Decimal ({min_}-{max_})"
            self.short_help = f"A decimal number in the range {min_} to {max_}"
            self.help = f"A decimal number in the range {min_} to {max_}"
        elif min_ is None and max_ is not None:
            self.title = f"Decimal ({max_}+)"
            self.short_help = f"A decimal number less than or equal to {max_}"
            self.help = f"A decimal number less than or equal to {max_}"
        elif min_ is not None and max_ is None:
            self.title = f"Decimal ({min_}-)"
            self.short_help = f"A decimal number greater than or equal to {min_}"
            self.help = f"A decimal number greater than or equal to {min_}"
        else:
            raise ValueError("min_ or max_ need to be a number")
        
    async def convert(self, g_ctx: GlobalContext, ctx: CommandEvent, arg: str) -> float:
        try:
            number = int(arg)
        except ValueError as e:
            raise ArgumentConversionError("Expected a number")
        
        if self.max is not None and number > self.max:
            raise ArgumentConversionError(f"Number is out of range! Maximum value: {self.max}")
        if self.min is not None and number < self.min:
            raise ArgumentConversionError(f"Number is out of range! Minimum value: {self.min}")

        return number

if TYPE_CHECKING:
    from bot.ravenfallmanager import RFChannelManager
    from bot.ravenfallchannel import RFChannel

class RFChannelConverter(BaseConverter):
    title = "RFChannel"
    short_help = "A Ravenfall channel name"
    help = "A Ravenfall channel monitored by the bot."

    @staticmethod
    async def convert(g_ctx: GlobalContext, ctx: CommandEvent, arg: str) -> RFChannel:
        if arg == 'this':
            if isinstance(ctx.message, TwitchMessageEvent):
                query = ctx.message.room_name
            else:
                raise ArgumentConversionError("A channel must be specified.")
        else:
            query = arg
        channel_by_name = g_ctx.ravenfall_manager.get_channel(channel_name=query)
        channel_by_id = g_ctx.ravenfall_manager.get_channel(channel_id=query)
        channel = channel_by_name or channel_by_id
        if channel is None:
            if arg == 'this':
                raise ArgumentConversionError("A channel must be specified.")
            else:
                raise ArgumentConversionError(f"Ravenfall channel '{arg}' not found.")
        return channel


class RFItemConverter(BaseConverter):
    title = "Item"
    short_help = "An item name"
    help = "An item name"
    
    @staticmethod
    async def convert(g_ctx: GlobalContext, ctx: CommandEvent, arg: str) -> RFItem:
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

class TwitchUsername(BaseConverter):
    title = "Twitch username"
    short_help = "A valid Twitch username"
    help = "A valid Twitch username"
    
    @staticmethod
    async def convert(g_ctx: GlobalContext, ctx: CommandEvent, arg: str):
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
