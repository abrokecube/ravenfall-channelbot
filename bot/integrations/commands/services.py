from __future__ import annotations
from collections.abc import Collection
from types import MethodType
from typing import Any, TYPE_CHECKING
from bot.core.components import BaseService
from bot.integrations.chat_messages.enums import UserRole
from bot.integrations.chat_messages.events import MessageEvent
import dataclasses

from bot.integrations.chat_messages.models import ChatRoomCapabilities
from bot.integrations.commands import CommandExecutionResult, CommandResponse

if TYPE_CHECKING:
    from bot.integrations.commands.dispatchers import CommandDispatcher


class CommandService(BaseService):
    def __init__(self, dispatcher: CommandDispatcher) -> None:
        super().__init__()
        self.dispatcher = dispatcher

    async def execute(
        self,
        text: str,
        event: MessageEvent | None = None,
        roles: Collection[UserRole] | None = None,
        capture_responses: bool = False,
    ):
        if not roles:
            roles = [UserRole.USER]
        if event:
            event = dataclasses.replace(event, text=text, author_roles=set(roles))
        else:
            event = MessageEvent(
                text=text,
                id="bot",
                author_login="bot",
                author_name="bot",
                author_id="bot",
                author_roles=set(roles),
                room_name="bot",
                room_id="bot",
                room_capabilities=ChatRoomCapabilities(False, 999999),
                bot_user_login="bot",
                bot_user_name="bot",
                bot_user_id="bot",
                data={},
            )
        responses: list[CommandResponse] = []
        if capture_responses:

            async def message(_: MessageEvent, text: str, *args: Any, **kwargs: Any):
                responses.append(CommandResponse(text, args, kwargs))

            event.reply = MethodType(message, event)
            event.send = MethodType(message, event)
        command_exception = None
        try:
            result = await self.dispatcher.dispatch(
                self.global_context,
                event,
                no_prefix=True,
            )
            command_exception = result.error
        except Exception as e:
            if not capture_responses:
                raise e
            command_exception = e
        return CommandExecutionResult(responses, command_exception)
