from __future__ import annotations

import glob
import re
from typing import TYPE_CHECKING, override

from utils.strutils import strjoin

from .exceptions import ArgumentConversionError

if TYPE_CHECKING:
    from bot.core.components import GlobalContext

    from .events import CommandEvent


class BaseConverter:
    """Base converter class for command argument conversion.

    To display a custom error message when conversion fails,
    raise command_exceptions.ArgumentConversionError in the convert method.
    """

    title: str = ""
    short_help: str = ""
    help: str = ""

    async def convert(
        self,
        g_ctx: GlobalContext,  # pyright: ignore[reportUnusedParameter]
        event: CommandEvent,  # pyright: ignore[reportUnusedParameter]
        arg: str | object,  # pyright: ignore[reportUnusedParameter]
    ) -> object:
        """Convert a string argument to the desired type."""
        raise NotImplementedError

    @classmethod
    async def cls_convert(
        cls,
        g_ctx: GlobalContext,  # pyright: ignore[reportUnusedParameter]
        event: CommandEvent,  # pyright: ignore[reportUnusedParameter]
        arg: str | object,  # pyright: ignore[reportUnusedParameter]
    ) -> object:
        """Convert a string argument to the desired type."""
        raise NotImplementedError


class Choice(BaseConverter):
    """Converter for a fixed set of string choices."""

    def __init__(
        self,
        definition: list[str] | dict[str, list[str]],
        title: str | None = None,
        *,
        case_sensitive: bool = False,
    ):
        super().__init__()
        string_map = {}
        choices = []
        if isinstance(definition, list):
            if case_sensitive:
                string_map = {x: x for x in definition}
            else:
                string_map = {x.lower(): x for x in definition}
            choices = definition
        else:
            choices = list(definition.keys())
            if case_sensitive:
                string_map = {x: x for x in choices}
                for k, v in definition.items():
                    string_map.update(dict.fromkeys(v, k))
            else:
                string_map = {x.lower(): x for x in choices}
                for k, v in definition.items():
                    string_map.update({x.lower(): k for x in v})

        if title:
            self.title: str = title
        else:
            self.title = f"Choice ({len(choices)})"
        j = strjoin(", ", *choices, before_end="or ", include_conn_char_before_end=True)
        self.short_help: str = f"One of the following: {j}"
        self.help: str = self.short_help
        self.case_sensitive: bool = case_sensitive
        self.string_map: dict[str, str] = string_map

    @override
    async def convert(
        self, g_ctx: GlobalContext, event: CommandEvent, arg: str | object
    ) -> str:
        if not isinstance(arg, str):
            msg = "Input was an invalid type."
            raise TypeError(msg)
        if arg not in self.string_map:
            msg = (
                f"Choice '{arg}' is not a valid option. Valid choices: {self.short_help}"
            )
            raise ArgumentConversionError(msg)
        return self.string_map[arg]


class Regex(BaseConverter):
    """Regex converter for command arguments."""

    title: str = "Regex"
    short_help: str = "A python regular expression"
    help: str = "A python regular expression"

    @override
    async def convert(
        self, g_ctx: GlobalContext, event: CommandEvent, arg: str | object
    ) -> re.Pattern[str]:
        if not isinstance(arg, str):
            msg = "Input was an invalid type."
            raise TypeError(msg)

        try:
            return re.compile(arg)
        except Exception as e:
            raise ArgumentConversionError("Couldn't compile regex") from e


class Glob(BaseConverter):
    """Glob converter for command arguments."""

    title: str = "Glob"
    short_help: str = "A glob pattern"
    help: str = "A glob pattern"

    @override
    async def convert(
        self, g_ctx: GlobalContext, event: CommandEvent, arg: str | object
    ) -> re.Pattern[str]:
        if not isinstance(arg, str):
            msg = "Input was an invalid type."
            raise TypeError(msg)
        try:
            return re.compile(glob.translate(arg))
        except Exception as e:
            raise ArgumentConversionError("Couldn't compile glob expression") from e


class RangeInt(BaseConverter):
    """Range converter for command arguments."""

    def __init__(self, min_: int | None, max_: int | None):
        super().__init__()
        self.min: int | None = min_
        self.max: int | None = max_
        if min_ is not None and max_ is not None:
            self.title: str = f"Number ({min_}-{max_})"
            self.short_help: str = f"An integer in the range {min_} to {max_}"
            self.help: str = f"A whole number in the range {min_} to {max_}"
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

    @override
    async def convert(
        self, g_ctx: GlobalContext, event: CommandEvent, arg: str | object
    ) -> int:
        if not isinstance(arg, (str, int)):
            msg = "Input was an invalid type."
            raise TypeError(msg)
        try:
            number = int(arg)
        except ValueError:
            raise ArgumentConversionError("Expected an integer") from None

        if self.max is not None and number > self.max:
            msg = f"Number is out of range! Maximum value: {self.max}"
            raise ArgumentConversionError(msg)
        if self.min is not None and number < self.min:
            msg = f"Number is out of range! Minimum value: {self.min}"
            raise ArgumentConversionError(msg)

        return number


class RangeFloat(BaseConverter):
    """Range converter for command arguments."""

    def __init__(self, min_: float | None, max_: float | None):
        super().__init__()
        self.min: float | None = min_
        self.max: float | None = max_
        if min_ is not None and max_ is not None:
            self.title: str = f"Decimal ({min_}-{max_})"
            self.short_help: str = f"A decimal number in the range {min_} to {max_}"
            self.help: str = f"A decimal number in the range {min_} to {max_}"
        elif min_ is None and max_ is not None:
            self.title = f"Decimal ({max_}-)"
            self.short_help = f"A decimal number less than or equal to {max_}"
            self.help = f"A decimal number less than or equal to {max_}"
        elif min_ is not None and max_ is None:
            self.title = f"Decimal ({min_}+)"
            self.short_help = f"A decimal number greater than or equal to {min_}"
            self.help = f"A decimal number greater than or equal to {min_}"
        else:
            raise ValueError("min_ or max_ need to be a number")

    @override
    async def convert(
        self, g_ctx: GlobalContext, event: CommandEvent, arg: str | object
    ) -> float:
        if not isinstance(arg, (str, float, int)):
            msg = "Input was an invalid type."
            raise TypeError(msg)
        try:
            number = float(arg)
        except ValueError:
            raise ArgumentConversionError("Expected a number") from None

        if self.max is not None and number > self.max:
            msg = f"Number is out of range! Maximum value: {self.max}"
            raise ArgumentConversionError(msg)
        if self.min is not None and number < self.min:
            msg = f"Number is out of range! Minimum value: {self.min}"
            raise ArgumentConversionError(msg)

        return number


# class GameNotConnected(ListenerError):
#     def __init__(self, message: str = "Game is not connected. Please try again later."):
#         super().__init__(message)

# class RFChannelConverter(BaseConverter):
#     title: str = "RFChannel"
#     short_help: str = "A Ravenfall channel name"
#     help: str = "A Ravenfall channel monitored by the bot."

#     @override
#     async def convert(self, g_ctx: GlobalContext, event: CommandEvent, arg: str | object) -> RFChannel:
#         if arg == 'this':
#             if isinstance(event.message, TwitchMessageEvent):
#                 query = event.message.room_name
#             else:
#                 raise ArgumentConversionError("A channel must be specified.")
#         else:
#             query = arg
#         rf_manager = g_ctx.require_service(RFChannelManager)
#         if not rf_manager:
#             raise GameNotConnected()
#         channel_by_name = rf_manager.get_channel(channel_name=query)
#         channel_by_id = rf_manager.get_channel(channel_id=query)
#         channel = channel_by_name or channel_by_id
#         if channel is None:
#             if arg == 'this':
#                 raise ArgumentConversionError("A channel must be specified.")
#             else:
#                 raise ArgumentConversionError(f"Ravenfall channel '{arg}' not found.")
#         return channel


# tw_username_re = re.compile(r"^@?[a-zA-Z0-9][\w]{2,24}$")
# tw_username_f_re = re.compile(r"^@?[a-zA-Z0-9/|][\w/|]{2,24}$")
# def is_twitch_username(text: str, pre_filter: bool = False):
#     if pre_filter:
#         return bool(tw_username_f_re.match(text))
#     else:
#         return bool(tw_username_re.match(text))

# class TwitchUsername(BaseConverter):
#     title: str = "Twitch username"
#     short_help: str = "A valid Twitch username"
#     help: str = "A valid Twitch username"

#     @override
#     async def convert(self, g_ctx: GlobalContext, event: CommandEvent, arg: str | object):
#         is_valid = is_twitch_username(arg)
#         if not is_valid:
#             raise ArgumentConversionError("Not a valid username.")
#         return arg.lstrip("@").replace("\U000e0000", '').replace("|","").replace("/","")

# class _RFSkill(Choice):
#     def __init__(self, case_sensitive: bool = False):
#         definition = {
#             "Attack": ['atk', 'att'],
#             "Defense": ['def'],
#             "Strength": ['str'],
#             "Health": ['hp'],
#             "Woodcutting": ['wood', 'chop', 'wdc', 'chomp'],
#             "Mining": ['mine', 'min'],
#             "Crafting": ['craft'],
#             "Cooking": ['cook', "ckn"],
#             "Farming": ['farm', 'fm'],
#             "Slayer": ['slay'],
#             "Magic": [],
#             "Ranged": ["range"],
#             "Sailing": ['sail'],
#             "Healing": ['heal'],
#             "Gathering": ["gath"],
#             "Alchemy": ["brew", "alch"],
#             "CombatLevel": ["combat"]
#         }
#         super().__init__(definition, "Ravenfall skill", case_sensitive)

# RFSkill = _RFSkill()
