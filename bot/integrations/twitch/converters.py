from __future__ import annotations

import re
from typing import TYPE_CHECKING, override

from bot.integrations.commands import ArgumentConversionError, BaseConverter

if TYPE_CHECKING:
    from bot.core.components import GlobalContext
    from bot.integrations.commands import CommandEvent

tw_username_re = re.compile(r"^@?[a-zA-Z0-9][\w]{2,24}$")
tw_username_f_re = re.compile(r"^@?[a-zA-Z0-9/|][\w/|]{2,24}$")


def is_twitch_username(text: str, *, pre_filter: bool = False):
    if pre_filter:
        return bool(tw_username_f_re.match(text))
    return bool(tw_username_re.match(text))


class TwitchUsername(BaseConverter):
    title: str = "Twitch username"
    short_help: str = "A valid Twitch username"
    help: str = "A valid Twitch username"

    @override
    async def convert(self, g_ctx: GlobalContext, event: CommandEvent, arg: str | object):
        if not isinstance(arg, str):
            raise TypeError("Invalid type.")
        is_valid = is_twitch_username(arg)
        if not is_valid:
            raise ArgumentConversionError("Not a valid username.")
        return arg.lstrip("@").replace("\U000e0000", "").replace("|", "").replace("/", "")
