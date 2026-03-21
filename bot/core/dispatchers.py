from __future__ import annotations
from typing import TYPE_CHECKING, override
import logging

if TYPE_CHECKING:
    from .listeners import GenericListener
    from .components import GlobalContext, BaseEvent, BaseListener
from .enums import EventCategory, Dispatcher

from .components import BaseDispatcher

LOGGER = logging.getLogger(__name__)

TEXT_REPLACEMENTS: dict[int, str | int | None] = {
    ord("\U000e0000"): None,
    ord("\u034f"): None,
}


def filter_text(text: str):
    text = text.translate(TEXT_REPLACEMENTS)
    text = text.strip()
    return text


class SimpleDispatcher(BaseDispatcher):
    def __init__(self) -> None:
        super().__init__()
        self._id: Dispatcher = Dispatcher.Generic
        self._func_listener: type[BaseListener] = GenericListener
        self.categories: set[EventCategory] = set(
            [
                EventCategory.Generic,
                EventCategory.Message,
                EventCategory.RavenBotMessage,
                EventCategory.RavenfallMessage,
            ]
        )

    @override
    async def dispatch(
        self, global_context: GlobalContext, event: BaseEvent, *args, **kwargs
    ):
        for listener in self.listeners.values():
            match_result = False
            try:
                match_result = await listener.check_for_match(event)
            except Exception as e:
                LOGGER.error(f"Listener matcher returned an error: {e}", exc_info=True)

            if match_result:
                await self._invoke_listener(
                    listener, global_context, event, match_result
                )


# class TwitchRedeemDispatcher(SimpleDispatcher):
#     def __init__(self):
#         super().__init__()
#         self._id: Dispatcher = Dispatcher.TwitchRedeem

#     @override
#     async def on_invoke_error(self, global_context: GlobalContext, event: BaseEvent, error: Exception, *args: Any, **kwargs: Any) -> None:
#         if not isinstance(event, TwitchRedemptionEvent):
#             return
#         if isinstance(error, CommandError):
#             await event.send(f"❌ {error.message.rstrip('.')}. (Points refunded)")
#         else:
#             await event.send(f"❌ An error occurred. Points will be refunded.")
#         try:
#             await event.cancel()
#         except Exception:
#             LOGGER.error("Failed to refund points", exc_info=True)
