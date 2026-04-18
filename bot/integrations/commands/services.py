from __future__ import annotations

import dataclasses
from types import MethodType
from typing import TYPE_CHECKING

from bot.core.components import BaseService
from bot.integrations.chat_messages.enums import UserRole
from bot.integrations.chat_messages.events import MessageEvent
from bot.integrations.chat_messages.models import ChatRoomCapabilities
from bot.integrations.commands import CommandExecutionResult, CommandResponse

if TYPE_CHECKING:
    from collections.abc import Collection

    from bot.integrations.commands.dispatchers import CommandDispatcher


class CommandService(BaseService):
    """Service for executing commands."""

    def __init__(self, dispatcher: CommandDispatcher) -> None:
        super().__init__()
        self.dispatcher: CommandDispatcher = dispatcher

    async def execute(
        self,
        text: str,
        event: MessageEvent | None = None,
        roles: Collection[UserRole] | None = None,
        *,
        capture_responses: bool = False,
    ):
        """Execute a command with the given text and context."""
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

            async def message(
                _: MessageEvent, text: str, *args: object, **kwargs: object
            ):
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
                raise
            command_exception = e
        return CommandExecutionResult(responses, command_exception)
