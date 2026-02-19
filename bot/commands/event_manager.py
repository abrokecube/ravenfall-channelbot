from __future__ import annotations
from typing import TYPE_CHECKING, List, Dict, Callable, Awaitable, Type
from collections import defaultdict
import logging
if TYPE_CHECKING:
    from .event_sources import BaseEventSource
    from .dispatchers import BaseDispatcher
    from .events import BaseEvent
    from .global_context import GlobalContext
    from .cog import Cog
    
from .enums import Dispatcher, EventCategory
from .dispatchers import SimpleDispatcher
from .listeners import BaseListener
import asyncio

LOGGER = logging.getLogger(__name__)

class EventManager:
    def __init__(self, global_context: GlobalContext):
        self.event_sources: List[BaseEventSource] = []
        self.dispatchers: Dict[Dispatcher, BaseDispatcher] = {
            Dispatcher.Generic: SimpleDispatcher()
        }
        self.cogs: Dict[str, Cog] = {}
        self.global_context: GlobalContext = global_context
        
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
        
    async def process_event(self, event: BaseEvent):
        LOGGER.debug(f"Processing event {event}")
        
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
