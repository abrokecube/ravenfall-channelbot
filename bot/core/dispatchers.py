from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Any, override
from collections.abc import Awaitable
import logging

if TYPE_CHECKING:
    from .listeners import BaseListener
    from .global_context import GlobalContext
    from .event_manager import EventManager
    from .types import ListenerFuncType
from .events import (
    BaseEvent, 
    MessageEvent
)
from .listeners import (
    BaseListener,
    GenericListener, 
    CommandListener
)
from .events import (
    CommandEvent,
    TwitchRedemptionEvent
)
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
    CommandError,
    ListenerError
)
from .modals import CommandDispatchResult
from .command_parser import CommandArgs
from utils.format_time import format_seconds, TimeSize
from .cooldown import Cooldown

LOGGER = logging.getLogger(__name__)

TEXT_REPLACEMENTS: dict[int, str | int | None] = {
    ord("\U000e0000"): None,
    ord("\u034f"): None
}
def filter_text(text: str):
    text = text.translate(TEXT_REPLACEMENTS)
    text = text.strip()
    return text

class BaseDispatcher:
    def __init__(self):
        self.id: Dispatcher = Dispatcher.Base
        self._func_listener: type[BaseListener] = BaseListener
        self.listeners: dict[str, BaseListener] = {}
        self.categories: set[EventCategory] = set([EventCategory.Generic])
        
    async def setup(self, event_manager: EventManager):  # pyright: ignore[reportUnusedParameter]
        pass

    async def teardown(self):
        pass
                
    def add_listener(self, listener: BaseListener):
        if listener.id in self.listeners:
            raise ValueError(f"Listener with id '{listener.id}' already exists!")
        if listener.expected_dispatcher != self.id:
            raise ValueError(f"Listener {listener} cannot be assigned to this dispatcher!")
        self.listeners[listener.id] = listener
    
    def remove_listener(self, listener: BaseListener):
        listener_id = listener.id
        if listener_id not in self.listeners:
            raise ValueError(f"Listener with id '{listener_id}' doesn't exist!")
        __ = self.listeners.pop(listener_id)
    
    async def _invoke_listener(self, listener: BaseListener, g_ctx: GlobalContext, event: BaseEvent, *args: Any, **kwargs: Any):
        try:
            await listener.invoke(g_ctx, event, *args, **kwargs)
        except Exception as error:
            if not isinstance(error, ListenerError):
                LOGGER.error(f"Error in {listener.func.__name__} occurred during command invocation: {error}", exc_info=True)
            else:
                LOGGER.error(f"Error in {listener.func.__name__} handled during command invocation: {error}")
            await self.on_invoke_error(g_ctx, event, error)
    
    async def dispatch(self, global_context: GlobalContext, event: BaseEvent, *args: Any, **kwargs: Any) -> Any:
        for l in self.listeners.values():
            match_result = False
            try:
                match_result = await l.check_for_match(event)
            except Exception as e:
                LOGGER.error(f"Listener matcher returned an error: {e}", exc_info=True)
            
            if match_result:
                await self._invoke_listener(l, global_context, event)
                
    async def on_invoke_error(self, global_context: GlobalContext, event: BaseEvent, error: Exception, *args: Any, **kwargs: Any) -> None:  # pyright: ignore[reportUnusedParameter]
        pass
            
class SimpleDispatcher(BaseDispatcher):
    def __init__(self) -> None:
        super().__init__()
        self._id: Dispatcher = Dispatcher.Generic
        self._func_listener: type[BaseListener] = GenericListener
        self.categories: set[EventCategory] = set([
            EventCategory.Generic, EventCategory.Message, EventCategory.RavenBotMessage,
            EventCategory.RavenfallMessage
        ])

    @override
    async def dispatch(self, global_context: GlobalContext, event: BaseEvent):
        for l in self.listeners.values():
            match_result = False
            try:
                match_result = await l.check_for_match(event)
            except Exception as e:
                LOGGER.error(f"Listener matcher returned an error: {e}", exc_info=True)
            
            if match_result:
                await self._invoke_listener(l, global_context, event, match_result)

class TwitchRedeemDispatcher(SimpleDispatcher):
    def __init__(self):
        super().__init__()
        self._id: Dispatcher = Dispatcher.TwitchRedeem
    
    @override
    async def on_invoke_error(self, global_context: GlobalContext, event: BaseEvent, error: Exception, *args: Any, **kwargs: Any) -> None:
        if not isinstance(event, TwitchRedemptionEvent):
            return
        if isinstance(error, CommandError):
            await event.send(f"❌ {error.message.rstrip('.')}. (Points refunded)")
        else:
            await event.send(f"❌ An error occurred. Points will be refunded.")
        try:
            await event.cancel()
        except Exception:
            LOGGER.error("Failed to refund points", exc_info=True)

class CommandDispatcher(BaseDispatcher):
    def __init__(self, case_sensitive: bool = False):
        super().__init__()
        self._id: Dispatcher = Dispatcher.Command
        self._func_listener: type[BaseListener] = CommandListener
        self.categories: set[EventCategory] = set([EventCategory.Message])
        self.listeners: dict[str, BaseListener] = {}
        self.listeners_and_aliases: dict[str, CommandListener] = {}
        self.error_cooldown: Cooldown = Cooldown(1, 5, [BucketType.USER, BucketType.CHANNEL])
        self.case_sensitive: bool = case_sensitive

    @override
    def add_listener(self, listener: BaseListener):
        if listener.expected_dispatcher != self._id:
            raise ValueError(f"Listener {listener} cannot be assigned to this dispatcher!")
        
        if isinstance(listener, CommandListener):
            name: str = listener.id
            aliases: list[str] = listener.aliases.copy()
            cog_name: str = listener.cog.__qualname__
        else:
            raise ValueError(f"Listener {listener} cannot be assigned to this dispatcher!")

        if not self.case_sensitive:
            name = name.lower()
            aliases = [a.lower() for a in aliases]
        
        if name in self.listeners:
            other = self.listeners[name]
            raise ValueError(f"Command name '{name}' ({cog_name}) is taken by command '{other.id}' ({other.cog.__qualname__})")
        if name in self.listeners_and_aliases:
            other = self.listeners_and_aliases[name]
            raise ValueError(f"Command name '{name}' ({cog_name}) is taken by an alias of '{other.id}' ({other.cog.__qualname__})")
        for alias in aliases:
            if alias in self.listeners:
                other = self.listeners[alias]
                raise ValueError(f"Command alias '{alias}' of command '{name}' ({cog_name}) is taken by command '{other.id}' ({other.cog.__qualname__})")
            if alias in self.listeners_and_aliases:
                other = self.listeners_and_aliases[alias]
                raise ValueError(f"Command alias '{alias}' of command '{name}' ({cog_name}) is taken by an alias of '{other.id}' ({other.cog.__qualname__})")
            
        self.listeners[name] = listener
        self.listeners_and_aliases[name] = listener
        for alias in aliases:
            self.listeners_and_aliases[alias] = listener
    
    @override
    def remove_listener(self, listener: BaseListener):
        name: str = ""
        aliases: list[str] = []
        if isinstance(listener, CommandListener):
            name = listener.id
            aliases = listener.aliases.copy()
        else:
            name = listener.id
            aliases = []
            
        if not self.case_sensitive:
            name = name.lower()
            aliases = [a.lower() for a in aliases]
            
        if not name in self.listeners:
            raise ValueError(f"Dispatcher '{self.__qualname__}' does not have a listener with the name '{listener.id}'")
            
        __ = self.listeners.pop(name)
        __ = self.listeners_and_aliases.pop(name)
        for alias in aliases:
            __ = self.listeners_and_aliases.pop(alias)

    def _find_command(self, text: str) -> tuple[str, str]:
        norm_text = text
        if not self.case_sensitive:
            norm_text = text.lower()
        for cmd in sorted(self.listeners_and_aliases.keys(), key=len, reverse=True):
            if norm_text == cmd or norm_text.startswith(cmd + ' '):
                return cmd, text[len(cmd):].strip()
        return "", text

    @override
    async def dispatch(
        self, global_context: GlobalContext, event: BaseEvent, 
        *args: Any,
        respond_to_errors: bool = True, no_prefix: bool = False,
        **kwargs: Any,
        ) -> CommandDispatchResult:
        if isinstance(event, MessageEvent):            
            if no_prefix:
                prefix = ""
            else:
                prefix = await self.get_prefix(global_context, event)
            used_prefix = ""
            if isinstance(prefix, list):
                for p in prefix:
                    if event.text.startswith(p):
                        used_prefix = p
                        break
                else:
                    return CommandDispatchResult(None, None)
            else:
                if not event.text.startswith(prefix):
                    return CommandDispatchResult(None, None)
                used_prefix = prefix
            content = event.text[len(used_prefix):]
            
            command_name, remaining_text = self._find_command(content)
            if not command_name or command_name not in self.listeners_and_aliases:
                return CommandDispatchResult(None, None)

            command = self.listeners_and_aliases[command_name]

            new_event = CommandEvent(
                message=event,
                prefix=used_prefix,
                invoked_with=content[:len(command_name)],
                parameters_text=remaining_text,
                parsed_args=CommandArgs(remaining_text)
            )
        elif isinstance(event, CommandEvent):
            new_event = event
            command_name, remaining_text = self._find_command(event.message.text[len(event.prefix):])
            command = self.listeners_and_aliases.get(command_name)
            if not command:
                return CommandDispatchResult(None, None)
        else:
            raise ValueError(f"Command dispatcher does not support event type '{event.__class__.__name__}'")

        try:
            await command.invoke(global_context, new_event)
            return CommandDispatchResult(command, None)
        except Exception as error:
            if not isinstance(error, ListenerError):
                LOGGER.error(f"Error occurred during command invocation: {error}", exc_info=True)
            else:
                LOGGER.error(f"Error handled during command invocation: {error}")
            if not respond_to_errors:
                raise error
            await self.on_invoke_error(global_context, new_event, error, command=command)
            return CommandDispatchResult(command, error)

                
    async def on_invoke_error(self, g_ctx: GlobalContext, event: BaseEvent, error: Exception, *args: Any, command: CommandListener | None = None, **kwargs: Any):
        if command is None:
            return
        if not isinstance(event, CommandEvent):
            return
        usage_text = command.get_usage_text(event.prefix, event.invoked_with)
        if isinstance(error, ListenerOnCooldown):
            if error.cooldown.per >= 60 and self.error_cooldown.get_retry_after(event) <= 0:
                await event.message.reply(f"❌ Listener '{command.name}' is on cooldown. Try again in {format_seconds(error.retry_after, TimeSize.LONG)}.")
                self.error_cooldown.update_rate_limit(event)
        elif isinstance(error, MissingRequiredArgumentError):
            await event.message.reply(f"❌ Usage: {usage_text} – Missing argument: {error.parameter.name}")
        elif isinstance(error, EmptyFlagValueError):
            if not error.parameter:
                logging.error("EmptyFlagValueError does not have an assigned parameter")
                await event.message.reply("❌ Expected a value for an argument")
            else:
                await event.message.reply(f"❌ Expected a value for argument '{error.parameter.name}' (type: {error.parameter.type_title})")
        elif isinstance(error, ArgumentConversionError):
            if not error.parameter:
                out_text = "❌ Error parsing an argument"
                logging.error("ArgumentConversionError does not have an assigned parameter")
            else:
                if not error.parameter.name in event.specified_parameters:
                    if error.message:
                        out_text = f"❌ Error parsing argument '{error.parameter.name}' (default value): {error.message}"
                    else:
                        out_text = f"❌ '{error.value}' ({error.parameter.name} default value) is not a valid {error.parameter.type_title}"
                else:
                    if error.message:
                        out_text = f"❌ Error parsing argument '{error.parameter.name}': {error.message}"
                    else:
                        out_text = f"❌ '{error.value}' ({error.parameter.name}) is not a valid {error.parameter.type_title}"
            await event.message.reply(out_text)
        elif isinstance(error, UnknownArgumentError):
            await event.message.reply(f"❌ Usage: {usage_text} – Unknown argument: {error.arguments[0]}")
        elif isinstance(error, UnknownFlagError):
            await event.message.reply(f"❌ Usage: {usage_text} – Unknown parameter: {error.flag_name}")
        elif isinstance(error, CheckFailure):
            if self.error_cooldown.get_retry_after(event) <= 0:
                await event.message.reply(f"❌ {error.message}")
                self.error_cooldown.update_rate_limit(event)
        elif isinstance(error, VerificationFailure):
            await event.message.reply(f"❌ {error.message}")
        elif isinstance(error, ArgumentError):
            await event.message.reply(f"❌ {error.message}")
        elif isinstance(error, CommandError):
            await event.message.reply(f"❌ {error.message}")
        elif isinstance(error, ListenerError):
            await event.message.reply(f"❌ {error.message}")
        else:
            await event.message.reply(f"❌ An unknown error occurred")
                
    async def get_prefix(self, global_context: GlobalContext, event: MessageEvent) -> str:
        return "!"
