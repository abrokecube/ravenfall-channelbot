from __future__ import annotations
from typing import TYPE_CHECKING, List, Set, Dict, Callable, Awaitable
import logging

if TYPE_CHECKING:
    from .listeners import BaseListener
    from .events import BaseEvent, MessageEvent
    from .global_context import GlobalContext
from .listeners import BaseListener, GenericListener, CommandListener
from .events import CommandEvent
from .enums import EventCategory, Dispatcher, BucketType
from .exceptions import (
    ListenerOnCooldown,
    MissingRequiredArgumentError,
    EmptyFlagValueError,
    ArgumentConversionError,
    UnknownArgumentError,
    UnknownFlagError,
    CheckFailure,
    VerificationFailure,
    ArgumentError,
    ListenerError
)
from utils.format_time import format_seconds, TimeSize
from .cooldown import Cooldown

LOGGER = logging.getLogger(__name__)

class BaseDispatcher:
    def __init__(self):
        self._id: Dispatcher = Dispatcher.Base
        self._func_listener: BaseListener = BaseListener
        self.listeners: Dict[str, BaseListener] = {}
        self.categories: Set[EventCategory] = set([EventCategory.Generic])
        
    def add_listener(self, listener: BaseListener | Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]):
        if not isinstance(listener, BaseListener):
            name = f"{listener.__name__}_func"
            listener = self._func_listener(listener)
            listener._id = name
        if listener._id in self.listeners:
            raise ValueError(f"Listener with id '{listener._id}' already exists!")
        if listener.expected_dispatcher != self._id:
            raise ValueError(f"Listener {listener} cannot be assigned to this dispatcher!")
        self.listeners[listener._id] = listener
    
    def remove_listener(self, listener: BaseListener | Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]):
        if not isinstance(listener, BaseListener):
            listener_id = f"{listener.__name__}_func"
        else:
            listener_id = listener._id
        if not listener_id in self.listeners:
            raise ValueError(f"Listener with id '{listener._id}' doesn't exist!")
        self.listeners.pop(listener._id)
        
    async def dispatch(self, global_context: GlobalContext, event: BaseEvent):
        for l in self.listeners.values():
            matches = False
            try:
                matches = await l.check_for_match(event)
            except Exception as e:
                LOGGER.error(f"Listener matcher returned an error: {e}", exc_info=True)
            
            try:
                if matches:
                    await l.invoke(global_context, event)
            except Exception as e:
                LOGGER.error(f"Error occurred during listener invocation: {e}", exc_info=True)
            
class SimpleDispatcher(BaseDispatcher):
    def __init__(self):
        super().__init__()
        self._id = Dispatcher.Generic
        self._func_listener = GenericListener
        self.categories: Set[EventCategory] = set([
            EventCategory.Generic, EventCategory.Message, EventCategory.RavenBotMessage,
            EventCategory.RavenfallMessage
        ])
        
class CommandDispatcher(BaseDispatcher):
    def __init__(self):
        super().__init__()
        self._id = Dispatcher.Command
        self._func_listener = CommandListener
        self.categories = set([EventCategory.Message])
        self.listeners: Dict[str, CommandListener] = {}
        self.error_cooldown = Cooldown(1, 5, [BucketType.USER, BucketType.CHANNEL])

    def add_listener(self, listener: BaseListener | Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]):
        if not isinstance(listener, BaseListener):
            listener = self._func_listener(listener)
        if listener._id in self.listeners:
            raise ValueError(f"Command '{listener._id}' already exists!")
        if listener.expected_dispatcher != self._id:
            raise ValueError(f"Listener {listener} cannot be assigned to this dispatcher!")
        self.listeners[listener._id] = listener
    
    def remove_listener(self, listener: BaseListener | Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]):
        if not isinstance(listener, BaseListener):
            listener_id = f"{listener.__name__}"
        else:
            listener_id = listener._id
        if not listener_id in self.listeners:
            raise ValueError(f"Listener with id '{listener._id}' doesn't exist!")
        self.listeners.pop(listener._id)

    def _find_command(self, text: str) -> tuple[str, str]:
        for cmd in sorted(self.listeners.keys(), key=len, reverse=True):
            if text == cmd or text.startswith(cmd + ' '):
                return cmd, text[len(cmd):].strip()
        return None, text

    async def dispatch(self, global_context: GlobalContext, event: MessageEvent | CommandEvent, respond_to_errors: bool = True):
        if isinstance(event, MessageEvent):
            prefix = await self.get_prefix(global_context, event)
            used_prefix = ""
            if isinstance(prefix, list):
                for p in prefix:
                    if event.text.startswith(p):
                        used_prefix = p
                        break
                else:
                    return
            else:
                if not event.text.startswith(prefix):
                    return
                used_prefix = prefix
            content = event.text[len(used_prefix):]
            
            command_name, remaining_text = self._find_command(content)
            if not command_name or command_name not in self.listeners:
                return

            command = self.listeners[command_name]

            new_event = CommandEvent(
                message=event,
                prefix=used_prefix,
                invoked_with=content[:len(command_name)],
                parameters_text=remaining_text
            )
        else:
            new_event = event

        try:
            await command.invoke(global_context, new_event)
        except Exception as error:
            LOGGER.error(f"Error occurred during command invocation: {error}", exc_info=True)
            if not respond_to_errors:
                raise error
            
            usage_text = command.get_usage_text(new_event.prefix, new_event.invoked_with)
            if isinstance(error, ListenerOnCooldown):
                if error.cooldown.per >= 60 and self.error_cooldown.get_retry_after(event) <= 0:
                    await event.reply(f"❌ Listener '{command.name}' is on cooldown. Try again in {format_seconds(error.retry_after, TimeSize.LONG)}.")
                    self.error_cooldown.update_rate_limit(event)
            elif isinstance(error, MissingRequiredArgumentError):
                await event.reply(f"❌ Usage: {usage_text} – Missing argument: {error.parameter.name}")
            elif isinstance(error, EmptyFlagValueError):
                await event.reply(f"❌ Expected a value for argument '{error.parameter.name}' (type: {error.parameter.type_title})")
            elif isinstance(error, ArgumentConversionError):
                if error.message:
                    out_text = f"❌ Error in argument '{error.parameter.name}': {error.message}"
                else:
                    out_text = f"❌ Error turning '{error.value}' ({error.parameter.name}) into {error.parameter.type_title}"
                await event.reply(out_text)
            elif isinstance(error, UnknownArgumentError):
                await event.reply(f"❌ Usage: {usage_text} – Unknown argument: {error.arguments[0]}")
            elif isinstance(error, UnknownFlagError):
                await event.reply(f"❌ Usage: {usage_text} – Unknown parameter: {error.flag_name}")
            elif isinstance(error, CheckFailure):
                if self.error_cooldown.get_retry_after(event) <= 0:
                    await event.reply(f"❌ {error.message}")
                    self.error_cooldown.update_rate_limit(event)
            elif isinstance(error, VerificationFailure):
                await event.reply(f"❌ {error.message}")
            elif isinstance(error, ArgumentError):
                await event.reply(f"❌ {error.message}")
            elif isinstance(error, ListenerError):
                await event.reply(f"❌ {error.message}")
            else:
                await event.reply(f"❌ An unknown error occurred")
                
    async def get_prefix(self, global_context: GlobalContext, event: MessageEvent):
        return "!"
