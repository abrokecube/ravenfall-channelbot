from __future__ import annotations
from typing import TYPE_CHECKING, List, Set, Dict, Callable, Awaitable
import logging
import dataclasses

if TYPE_CHECKING:
    from .listeners import BaseListener
    from .global_context import GlobalContext
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
from .command_parser import CommandArgs
from utils.format_time import format_seconds, TimeSize
from .cooldown import Cooldown

LOGGER = logging.getLogger(__name__)

TEXT_REPLACEMENTS = {
    "\U000e0000": None,
    "\u034f": None
}
TEXT_TRANS = str.maketrans(TEXT_REPLACEMENTS)
def filter_text(text: str):
    text = text.translate(TEXT_REPLACEMENTS)
    text = text.strip()
    return text

class BaseDispatcher:
    def __init__(self):
        self._id: Dispatcher = Dispatcher.Base
        self._func_listener: BaseListener = BaseListener
        self.listeners: Dict[str, BaseListener] = {}
        self.categories: Set[EventCategory] = set([EventCategory.Generic])
        
    def _func_to_listener(self, func: Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]):
        name = func.__name__
        init_params = getattr(func, "_listener_init_params", {})
        listener_cls = getattr(func, "_listener_class", None) or self._func_listener
        listener: BaseListener = listener_cls(listener, **init_params)
        listener._id = name
        return listener
        
    def add_listener(self, listener: BaseListener | Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]):
        if not isinstance(listener, BaseListener):
            listener = self._func_to_listener(listener)
        if listener._id in self.listeners:
            raise ValueError(f"Listener with id '{listener._id}' already exists!")
        if listener.expected_dispatcher != self._id:
            raise ValueError(f"Listener {listener} cannot be assigned to this dispatcher!")
        self.listeners[listener._id] = listener
    
    def remove_listener(self, listener: BaseListener | Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]):
        if not isinstance(listener, BaseListener):
            listener_id = listener.__name__
        else:
            listener_id = listener._id
        if not listener_id in self.listeners:
            raise ValueError(f"Listener with id '{listener._id}' doesn't exist!")
        self.listeners.pop(listener._id)
    
    async def _invoke_listener(self, listener: BaseListener, g_ctx: GlobalContext, event: BaseEvent, *args, **kwargs):
        try:
            await listener.invoke(g_ctx, event, *args, **kwargs)
        except Exception as error:
            if not isinstance(error, ListenerError):
                LOGGER.error(f"Error in {listener.func.__name__} occurred during command invocation: {error}", exc_info=True)
            else:
                LOGGER.error(f"Error in {listener.func.__name__} handled during command invocation: {error}")
            await self.on_invoke_error(g_ctx, event, error)
    
    async def dispatch(self, global_context: GlobalContext, event: BaseEvent):
        for l in self.listeners.values():
            match_result = False
            try:
                match_result = await l.check_for_match(event)
            except Exception as e:
                LOGGER.error(f"Listener matcher returned an error: {e}", exc_info=True)
            
            if match_result:
                await self._invoke_listener(l, global_context, event)
                
    async def on_invoke_error(self, global_context: GlobalContext, event: BaseEvent, error: Exception):
        pass
            
class SimpleDispatcher(BaseDispatcher):
    def __init__(self):
        super().__init__()
        self._id = Dispatcher.Generic
        self._func_listener = GenericListener
        self.categories: Set[EventCategory] = set([
            EventCategory.Generic, EventCategory.Message, EventCategory.RavenBotMessage,
            EventCategory.RavenfallMessage
        ])

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
        self._id = Dispatcher.TwitchRedeem
        
    async def on_invoke_error(self, global_context, event: TwitchRedemptionEvent, error):
        if isinstance(error, CommandError):
            await event.send(f"❌ {error.message.rstrip('.')}. Your points will be refunded.")
        else:
            await event.send(f"❌ An error occurred. Points will be refunded.")
        try:
            await event.cancel()
        except Exception as e:
            logging.error("Failed to refund points", exc_info=True)

class CommandDispatcher(BaseDispatcher):
    def __init__(self, case_sensitive: bool = False):
        super().__init__()
        self._id = Dispatcher.Command
        self._func_listener = CommandListener
        self.categories = set([EventCategory.Message])
        self.listeners: Dict[str, CommandListener] = {}
        self.listeners_and_aliases: Dict[str, CommandListener] = {}
        self.error_cooldown = Cooldown(1, 5, [BucketType.USER, BucketType.CHANNEL])
        self.case_sensitive = case_sensitive

    def add_listener(self, listener: CommandListener | Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]):
        if not isinstance(listener, BaseListener):
            listener = self._func_to_listener(listener)
        if listener.expected_dispatcher != self._id:
            raise ValueError(f"Listener {listener} cannot be assigned to this dispatcher!")
        
        name: str = listener._id
        aliases: List[str] = listener.aliases.copy()
        if not self.case_sensitive:
            name = name.lower()
            aliases = [a.lower() for a in aliases]
        
        if name in self.listeners:
            other = self.listeners[name]
            raise ValueError(f"Command name '{name}' ({listener.cog.__qualname__}) is taken by command '{other._id}' ({other.cog.__qualname__})")
        if name in self.listeners_and_aliases:
            other = self.listeners_and_aliases[name]
            raise ValueError(f"Command name '{name}' ({listener.cog.__qualname__}) is taken by an alias of '{other._id}' ({other.cog.__qualname__})")
        for alias in aliases:
            if alias in self.listeners:
                other = self.listeners[alias]
                raise ValueError(f"Command alias '{alias}' of command '{name}' ({listener.cog.__qualname__}) is taken by command '{other._id}' ({other.cog.__qualname__})")
            if alias in self.listeners_and_aliases:
                other = self.listeners_and_aliases[alias]
                raise ValueError(f"Command alias '{alias}' of command '{name}' ({listener.cog.__qualname__}) is taken by an alias of '{other._id}' ({other.cog.__qualname__})")
            
        self.listeners[name] = listener
        self.listeners_and_aliases[name] = listener
        for alias in aliases:
            self.listeners_and_aliases[alias] = listener
    
    def remove_listener(self, listener: CommandListener | Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]):
        name: str = ""
        aliases: List[str] = []
        if not isinstance(listener, BaseListener):
            name = listener.__name__
        else:
            name = listener._id
            aliases = listener.aliases.copy()
            
        if not self.case_sensitive:
            name = name.lower()
            aliases = [a.lower() for a in aliases]
            
        if not name in self.listeners:
            raise ValueError(f"Dispatcher '{self.__qualname__}' does not have a listener with the name '{listener._id}'")
            
        self.listeners.pop(name)
        self.listeners_and_aliases.pop(name)
        for alias in aliases:
            self.listeners_and_aliases.pop(alias)

    def _find_command(self, text: str) -> tuple[str, str]:
        norm_text = text
        if not self.case_sensitive:
            norm_text = text.lower()
        for cmd in sorted(self.listeners_and_aliases.keys(), key=len, reverse=True):
            if norm_text == cmd or norm_text.startswith(cmd + ' '):
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
            if not command_name or command_name not in self.listeners_and_aliases:
                return

            command = self.listeners_and_aliases[command_name]
            copied_msg_event = dataclasses.replace(event, text=filter_text(event.text))

            new_event = CommandEvent(
                message=copied_msg_event,
                prefix=used_prefix,
                invoked_with=content[:len(command_name)],
                parameters_text=remaining_text,
                parsed_args=CommandArgs(remaining_text)
            )
        else:
            new_event = event

        try:
            await command.invoke(global_context, new_event)
        except Exception as error:
            if not isinstance(error, ListenerError):
                LOGGER.error(f"Error occurred during command invocation: {error}", exc_info=True)
            else:
                LOGGER.error(f"Error handled during command invocation: {error}")
            if not respond_to_errors:
                raise error
            await self.on_invoke_error(global_context, new_event, command, error)
                
    async def on_invoke_error(self, g_ctx: GlobalContext, event: CommandEvent, command: CommandListener, error: Exception):
        usage_text = command.get_usage_text(event.prefix, event.invoked_with)
        if isinstance(error, ListenerOnCooldown):
            if error.cooldown.per >= 60 and self.error_cooldown.get_retry_after(event) <= 0:
                await event.message.reply(f"❌ Listener '{command.name}' is on cooldown. Try again in {format_seconds(error.retry_after, TimeSize.LONG)}.")
                self.error_cooldown.update_rate_limit(event)
        elif isinstance(error, MissingRequiredArgumentError):
            await event.message.reply(f"❌ Usage: {usage_text} – Missing argument: {error.parameter.name}")
        elif isinstance(error, EmptyFlagValueError):
            await event.message.reply(f"❌ Expected a value for argument '{error.parameter.name}' (type: {error.parameter.type_title})")
        elif isinstance(error, ArgumentConversionError):
            if error.message:
                out_text = f"❌ Error in argument '{error.parameter.name}': {error.message}"
            else:
                out_text = f"❌ Error turning '{error.value}' ({error.parameter.name}) into {error.parameter.type_title}"
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
                
    async def get_prefix(self, global_context: GlobalContext, event: MessageEvent):
        return "!"
