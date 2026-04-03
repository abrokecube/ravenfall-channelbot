from typing import override
from bot.core.components import BaseDispatcher, BaseEvent, GlobalContext
from bot.integrations.commands.exceptions import CommandError
from bot.integrations.twitch.events import TwitchRedemptionEvent

import logging

LOGGER = logging.getLogger(__name__)


class TwitchRedeemDispatcher(BaseDispatcher):
    def __init__(self):
        super().__init__()
        self.identifier: type[BaseDispatcher] = TwitchRedeemDispatcher

    @override
    async def on_invoke_error(
        self,
        global_context: GlobalContext,
        event: BaseEvent,
        error: Exception,
        *args: object,
        **kwargs: object,
    ) -> None:
        if not isinstance(event, TwitchRedemptionEvent):
            raise
        if isinstance(error, CommandError):
            await event.send(f"❌ {error.message.rstrip('.')}. (Points refunded)")
        else:
            await event.send("❌ An error occurred. Points will be refunded.")
        try:
            await event.cancel()
        except Exception as e:
            LOGGER.exception("Failed to refund points")
