from typing import override

from bot.core.components import BaseEvent, GlobalContext
from bot.integrations.chat_messages import BaseCheck
from bot.integrations.chat_messages.events import MessageEvent
from bot.integrations.twitch import TwitchEvent, TwitchService


class TwitchOnly(BaseCheck):
    """Disallow messages from other platforms than Twitch."""

    title: str | None = "Twitch only"
    help: str | None = "Can only be run in Twitch"
    will_hide_command_from_help: bool = True

    @override
    async def check(self, g_ctx: GlobalContext, event: BaseEvent):
        if not isinstance(event, MessageEvent):
            msg = "TwitchOnly check can only be used with MessageEvent"
            raise TypeError(msg)
        if not isinstance(event, TwitchEvent):
            return "This command can only be run on Twitch."
        return True


class HasTwitch(BaseCheck):
    """Channel has an authenticated Twitch instance."""

    title: str | None = "Twitch only"
    help: str | None = "Can only be run in Twitch"
    will_hide_command_from_help: bool = True

    @override
    async def check(self, g_ctx: GlobalContext, event: BaseEvent):
        if not isinstance(event, MessageEvent):
            msg = "TwitchOnly check can only be used with MessageEvent"
            raise TypeError(msg)
        if not isinstance(event, TwitchEvent):
            return "This command can only be run on Twitch."
        twitch_srv = g_ctx.get_service(TwitchService)
        if not twitch_srv:
            return "Bot has no Twitch permissions for this channel."
        if not twitch_srv.get_twitch_channel(event.channel_id):
            return "Bot has no Twitch permissions for this channel."
        return True
