from __future__ import annotations
from typing import TYPE_CHECKING, List, Dict, Callable, Awaitable, Type, Optional, Sequence
from types import MethodType
from collections import defaultdict
import logging
if TYPE_CHECKING:
    from .event_sources import BaseEventSource
    from .dispatchers import BaseDispatcher
    from .cog import Cog
    
from .events import BaseEvent, MessageEvent
from .global_context import GlobalContext
from .enums import Dispatcher, UserRole
from .dispatchers import SimpleDispatcher, CommandDispatcher
from .listeners import BaseListener
from .modals import ChatRoomCapabilities, CommandResponse, CommandExecutionResult
from . import middlewares
import asyncio
import dataclasses

LOGGER = logging.getLogger(__name__)

MiddlewareFunc = Callable[[GlobalContext, BaseEvent], BaseEvent | Awaitable[BaseEvent]]

class EventManager:
    def __init__(self, global_context: GlobalContext):
        self.event_sources: List[BaseEventSource] = []
        self.event_middlewares: Dict[type[BaseEvent], List[MiddlewareFunc]] = defaultdict(list)
        self.dispatchers: Dict[Dispatcher, BaseDispatcher] = {
            Dispatcher.Generic: SimpleDispatcher()
        }
        self.cogs: Dict[str, Cog] = {}
        self.global_context: GlobalContext = global_context
        
        self.add_event_middleware(MessageEvent, middlewares.filter_message_event_text)
        
    def add_event_source(self, source: BaseEventSource):
        source.event_processor_callback = self.process_event
        self.event_sources.append(source)
        
    def remove_event_source(self, source: BaseEventSource):
        try:
            source_idx = self.event_sources.index(source)
        except:
            raise ValueError("Source not found")
        self.event_sources[source_idx].event_processor_callback = None
        self.event_sources.pop(source_idx)
        
    def add_dispatcher(self, dispatcher: BaseDispatcher):
        if dispatcher._id in self.dispatchers:
            raise ValueError(f"Dispatcher with id '{dispatcher._id}' has already been added!")
        self.dispatchers[dispatcher._id] = dispatcher
        
    def remove_dispatcher(self, dispatcher: BaseDispatcher):
        if not dispatcher._id in self.dispatchers:
            raise ValueError(f"Dispatcher with id '{dispatcher._id}' was not found!")
        self.dispatchers.pop(dispatcher._id)
    
    def add_listener(self, listener: BaseListener | Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]):
        if isinstance(listener, BaseListener):
            expd_dispatcher = listener.expected_dispatcher
        else:
            expd_dispatcher = getattr(listener, "_listener_dispatcher", Dispatcher.Generic)
        if not expd_dispatcher in self.dispatchers:
            raise ValueError(f"No dispatcher exists for listener {listener}")
        self.dispatchers[expd_dispatcher].add_listener(listener)
    
    def remove_listener(self, listener: BaseListener | Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]):
        if isinstance(listener, BaseListener):
            expd_dispatcher = listener.expected_dispatcher
        else:
            expd_dispatcher = getattr(listener, "_listener_dispatcher", Dispatcher.Generic)
        if not expd_dispatcher in self.dispatchers:
            raise ValueError(f"No dispatcher exists for listener {listener}")
        self.dispatchers[expd_dispatcher].remove_listener(listener)
        
    async def add_cog(self, cog_cls: Type[Cog], **kwargs):
        if cog_cls in self.cogs:
            raise ValueError(f"Cog {cog_cls.__name__} is already loaded.")
            
        cog_instance = cog_cls(self, **kwargs)
        self.cogs[cog_cls.__name__] = cog_instance
        
        for listener in cog_instance.listeners:
            self.add_listener(listener)
            
        await cog_instance.setup()
        
    async def remove_cog(self, cog_cls: Type[Cog] | str):
        if isinstance(cog_cls, str):
            cog_name = cog_cls
        else:
            cog_name = cog_cls.__name__
            
        if cog_name not in self.cogs:
            raise ValueError(f"Cog {cog_cls.__name__} is not loaded.")
                
        cog_instance = self.cogs[cog_name]
        
        for listener in cog_instance.listeners:
            try:
                self.remove_listener(listener)
            except ValueError:
                pass

        try:
            await cog_instance.stop()
        except Exception as e:
            LOGGER.error(f"Error occured while stopping cog: {e}", exc_info=True)
                   
        del self.cogs[cog_cls.__name__]
        
    async def reload_cog(self, cog_cls: Type[Cog]) -> Type[Cog]:
        import importlib
        import sys
        
        module_name = cog_cls.__module__
        cog_name = cog_cls.__name__
        
        if cog_cls in self.cogs:
            await self.remove_cog(cog_cls)
            
        if module_name in sys.modules:
            try:
                module = importlib.reload(sys.modules[module_name])
            except Exception as e:
                LOGGER.error(f"Failed to reload module {module_name}: {e}")
                raise e
        else:
            module = importlib.import_module(module_name)
            
        new_cog_cls = getattr(module, cog_name)
        await self.add_cog(new_cog_cls)
        
        return new_cog_cls

    def add_event_middleware(self, target_event_cls: Type[BaseEvent], func: MiddlewareFunc):
        self.event_middlewares[target_event_cls].append(func)
    
    def remove_event_middleware(self, func: MiddlewareFunc, target_event_cls: Type[BaseEvent] | None = None):
        if target_event_cls:
            self.event_middlewares[target_event_cls].remove(func)
            return
        for t, m in self.event_middlewares.items():
            for mware in m:
                if mware == func:
                    m.remove(func)
                    return

    async def process_event(self, event: BaseEvent):
        LOGGER.debug(f"Processing event {event}")
        matching_middlewares: List[MiddlewareFunc] = []
        for t, m in self.event_middlewares.items():
            if isinstance(event, t):
                matching_middlewares.extend(m)
                
        for m in matching_middlewares:
            event = dataclasses.replace(event)
            result = m(self.global_context, event)
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, BaseEvent):
                event = result
        
        matching_dispatchers: Dict[BaseDispatcher, None] = {}
        for category in event.categories:
            for dispatcher in self.dispatchers.values():
                if category in dispatcher.categories:
                    matching_dispatchers[dispatcher] = None
        if not matching_dispatchers:
            LOGGER.warning(f"A matching dispatcher for event \"{event}\" was not found.")
            
        for dispatcher in matching_dispatchers.keys():
            try:
                await dispatcher.dispatch(self.global_context, event)
            except Exception as e:
                LOGGER.error(f"Exception while sending event to dispatcher: {e}", exc_info=True)
    
    async def stop_all(self):
        tasks = []
        for cog in self.cogs.keys():
            tasks.append(self.remove_cog(cog))
        await asyncio.gather(*tasks, return_exceptions=True)
        
    async def execute_text(
        self, text: str, event: Optional[MessageEvent] = None,
        roles: Sequence[UserRole] = [UserRole.USER], capture_responses: bool = False
        ) -> CommandExecutionResult:
        if not Dispatcher.Command in self.dispatchers:
            raise Exception("The event manager doesn't have a Command dispatcher registered.")
        if event:
            event = dataclasses.replace(event, text=text, author_roles=set(roles))
        else:
            event = MessageEvent(
                text=text,
                id="bot",
                author_login="bot",
                author_name="bot",
                author_id="bot",
                author_roles=roles,
                room_name="bot",
                room_id="bot",
                room_capabilities=ChatRoomCapabilities(False, 999999),
                bot_user_login="bot",
                bot_user_name="bot",
                bot_user_id="bot"
            )
        responses: List[CommandResponse] = []
        if capture_responses:
            async def message(self, text: str, *args, **kwargs):
                responses.append(CommandResponse(text, args, kwargs))
            event.reply = MethodType(message, event)
            event.send = MethodType(message, event)
        d: CommandDispatcher = self.dispatchers[Dispatcher.Command]
        command_exception = None
        try:
            result = await d.dispatch(
                self.global_context, event, no_prefix=True,
            )
            command_exception = result.error
        except Exception as e:
            if not capture_responses:
                raise e
            command_exception = e
        return CommandExecutionResult(
            responses, command_exception
        )
