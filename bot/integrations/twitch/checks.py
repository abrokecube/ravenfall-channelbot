from bot.core.components import GlobalContext, BaseEvent
from bot.integrations.chat_messages import BaseCheck
from bot.integrations.chat_messages.events import MessageEvent
from typing import override


class TwitchOnly(BaseCheck):
    """Disallow messages from other platforms than Twitch."""

    title: str | None = "Twitch only"
    help: str | None = "Can only be run in Twitch"
    hides_command_from_help: bool = True

    @override
    async def check(self, g_ctx: GlobalContext, event: BaseEvent):
        if not isinstance(event, MessageEvent):
            msg = "TwitchOnly check can only be used with MessageEvent"
            raise ValueError(msg)
        if event.platform != "twitch":
            return "This command can only be run on Twitch."
        return True
