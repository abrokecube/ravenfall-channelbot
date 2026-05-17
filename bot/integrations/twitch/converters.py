from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, override

from bot.integrations.commands import ArgumentConversionError, BaseConverter, CommandError
from bot.integrations.twitch import TwitchChannel, TwitchEvent

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


class TwitchInstanceConverter(BaseConverter):
    title: str = "Twitch instance"
    short_help: str = "The Twitch instance associated with this channel."
    help: str = "The Twitch instance associated with this channel."
    MATCH_MESSAGE_EVENT: Final[object] = "__match_msg_event"

    @classmethod
    @override
    async def cls_convert(
        cls, g_ctx: GlobalContext, event: CommandEvent, arg: str | object
    ) -> TwitchChannel:
        if not isinstance(arg, str):
            msg = "Invalid input type."
            raise TypeError(msg)
        from .services import TwitchService

        twitch_srv = g_ctx.get_service(TwitchService)
        if not twitch_srv:
            msg = "Twitch service has not been loaded. Try again later."
            raise CommandError(msg)
        if arg is cls.MATCH_MESSAGE_EVENT:
            if not isinstance(event.message, TwitchEvent):
                msg = "A Twitch channel must be specified."
                raise CommandError(msg)
            twitch = twitch_srv.get_twitch_channel(event.message.channel_id)
            if not twitch:
                msg = "A Twitch channel must be specified."
                raise CommandError(msg)
            return twitch
        user = await anext(twitch_srv.get_users(logins=[arg]), None)
        if not user:
            msg = "The specified channel has not been authenticated with the bot."
            raise CommandError(msg)
        twitch = twitch_srv.get_twitch_channel(user.id)
        if not twitch:
            msg = "The specified channel has not been authenticated with the bot."
            raise CommandError(msg)
        return twitch
