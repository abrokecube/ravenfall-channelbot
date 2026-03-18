from __future__ import annotations
from typing import TYPE_CHECKING, Any, cast
from collections.abc import Coroutine, Collection
from collections import defaultdict
from types import MethodType
import logging
if TYPE_CHECKING:
    from .event_sources import BaseEventSource
    from .dispatchers import BaseDispatcher
    from .cog import Cog
    from .types import EventProcessor
    
from .events import BaseEvent, MessageEvent
from .global_context import GlobalContext
from .enums import Dispatcher, UserRole
from .dispatchers import SimpleDispatcher, CommandDispatcher
from .listeners import BaseListener
from .modals import ChatRoomCapabilities, CommandResponse, CommandExecutionResult
from . import event_processors
import asyncio
import dataclasses
import importlib
import sys

LOGGER = logging.getLogger(__name__)


class EventManager:
    def __init__(self, global_context: GlobalContext):
        self.event_sources: list[BaseEventSource] = []
        self.event_processors: dict[type[BaseEvent], list[EventProcessor]] = defaultdict(list)
        self.dispatchers: dict[Dispatcher, BaseDispatcher] = {
            Dispatcher.Generic: SimpleDispatcher()
        }
        self.cogs: dict[str, Cog] = {}
        self.global_context: GlobalContext = global_context
        
        self.add_event_processor(MessageEvent, cast(EventProcessor, event_processors.filter_message_event_text))
        
    async def add_event_source(self, source: BaseEventSource):
        source.event_processor_callback = self.process_event
        self.event_sources.append(source)
        await source.setup(self)
        
    async def remove_event_source(self, source: BaseEventSource):
        try:
            source_idx = self.event_sources.index(source)
        except:
            raise ValueError("Source not found")
        self.event_sources[source_idx].event_processor_callback = None
        removed_source = self.event_sources.pop(source_idx)
        await removed_source.teardown()
        
    async def add_dispatcher(self, dispatcher: BaseDispatcher):
        if dispatcher.id in self.dispatchers:
            raise ValueError(f"Dispatcher with id '{dispatcher.id}' has already been added!")
        self.dispatchers[dispatcher.id] = dispatcher
        await dispatcher.setup(self)
        
    async def remove_dispatcher(self, dispatcher: BaseDispatcher):
        if not dispatcher.id in self.dispatchers:
            raise ValueError(f"Dispatcher with id '{dispatcher.id}' was not found!")
        removed_dispatcher = self.dispatchers.pop(dispatcher.id)
        await removed_dispatcher.teardown()
    
    def add_listener(self, listener: BaseListener):
        expd_dispatcher = listener.expected_dispatcher
        if not expd_dispatcher in self.dispatchers:
            raise ValueError(f"No dispatcher exists for listener {listener}")
        self.dispatchers[expd_dispatcher].add_listener(listener)
    
    def remove_listener(self, listener: BaseListener):
        expd_dispatcher = listener.expected_dispatcher
        if not expd_dispatcher in self.dispatchers:
            raise ValueError(f"No dispatcher exists for listener {listener}")
        self.dispatchers[expd_dispatcher].remove_listener(listener)
        
    async def add_cog(self, cog_cls: type[Cog], *args: Any, **kwargs: Any) -> None:  # pyright: ignore [reportExplicitAny, reportAny]
        if cog_cls in self.cogs:
            raise ValueError(f"Cog {cog_cls.__name__} is already loaded.")
            
        cog_instance = cog_cls(self, *args, **kwargs)
        self.cogs[cog_cls.__name__] = cog_instance
        
        for listener in cog_instance.listeners:
            self.add_listener(listener)
            
        await cog_instance.setup()
        
    async def remove_cog(self, cog_cls: type[Cog] | str):
        if isinstance(cog_cls, str):
            cog_name = cog_cls
        else:
            cog_name = cog_cls.__name__
            
        if cog_name not in self.cogs:
            raise ValueError(f"Cog {cog_name} is not loaded.")
                
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
                   
        del self.cogs[cog_name]
        
    async def reload_cog(self, cog_cls: type[Cog]) -> type[Cog]:       
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
            
        new_cog_cls = getattr(module, cog_name)  # pyright: ignore[reportAny]
        if not isinstance(new_cog_cls, type) or not issubclass(new_cog_cls, Cog):
            raise ValueError(f"Module {module_name} does not contain a Cog class.")
        await self.add_cog(new_cog_cls)
        
        return new_cog_cls

    def add_event_processor(self, target_event_cls: type[BaseEvent], func: EventProcessor):
        self.event_processors[target_event_cls].append(func)
    
    def remove_event_processor(self, func: EventProcessor, target_event_cls: type[BaseEvent] | None = None):
        if target_event_cls:
            self.event_processors[target_event_cls].remove(func)
            return
        for t, m in self.event_processors.items():
            for mware in m:
                if mware == func:
                    m.remove(func)
                    return

    async def process_event(self, event: BaseEvent):
        LOGGER.debug(f"Processing event {event}")
        matching_processors: list[EventProcessor] = []
        for t, m in self.event_processors.items():
            if isinstance(event, t):
                matching_processors.extend(m)
                
        for processor in matching_processors:
            event = dataclasses.replace(event)
            result = processor(self.global_context, event)
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, BaseEvent):
                event = result
        
        matching_dispatchers: dict[BaseDispatcher, None] = {}
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
        tasks: list[Coroutine[None, None, None]] = []
        for cog in self.cogs.keys():
            tasks.append(self.remove_cog(cog))
        __ = await asyncio.gather(*tasks, return_exceptions=True)
        
    async def execute_text(
        self, text: str, event: MessageEvent | None = None,
        roles: Collection[UserRole] | None = None, capture_responses: bool = False
        ) -> CommandExecutionResult:
        if not roles:
            roles = [UserRole.USER]
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
                author_roles=set(roles),
                room_name="bot",
                room_id="bot",
                room_capabilities=ChatRoomCapabilities(False, 999999),
                bot_user_login="bot",
                bot_user_name="bot",
                bot_user_id="bot",
                data={}
            )
        responses: list[CommandResponse] = []
        if capture_responses:
            async def message(_: MessageEvent, text: str, *args: Any, **kwargs: Any):
                responses.append(CommandResponse(text, args, kwargs))
            event.reply = MethodType(message, event)
            event.send = MethodType(message, event)
        d: BaseDispatcher = self.dispatchers[Dispatcher.Command]
        if not isinstance(d, CommandDispatcher):
            raise TypeError(f"Expected CommandDispatcher, got {type(d)}")
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
