from bot.core.components import BaseCheck, GlobalContext, BaseEvent
from bot.integrations.chat_messages import MessageEvent
from typing import override

class TwitchOnly(BaseCheck):
    title: str | None = "Twitch only"
    help: str | None = "Can only be run in Twitch"
    hide_in_help: bool = True
    
    @override
    async def check(self, g_ctx: GlobalContext, event: BaseEvent):
        if not isinstance(event, MessageEvent):
            raise ValueError("TwitchOnly check can only be used with MessageEvent")
        if event.platform != "twitch":
            return "This command can only be run on Twitch."
        return True
