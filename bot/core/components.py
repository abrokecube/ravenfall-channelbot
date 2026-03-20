from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING
from collections import defaultdict
from collections.abc import Coroutine, Collection, Awaitable
import sys
import importlib
import dataclasses
from dataclasses import dataclass
import asyncio
import logging
from uuid import uuid4

from .enums import Dispatcher, EventCategory, EventSource
from .exceptions import ListenerError

if TYPE_CHECKING:
    from .types import EventProcessor

LOGGER = logging.getLogger(__name__)


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
            
class EventManager:
    def __init__(self, global_context: GlobalContext):
        self.event_sources: list[BaseEventSource] = []
        self.event_processors: dict[type[BaseEvent], list[EventProcessor]] = defaultdict(list)
        self.dispatchers: dict[Dispatcher, BaseDispatcher] = {
            # Dispatcher.Generic: SimpleDispatcher()
        }
        self.cogs: dict[str, Cog] = {}
        self.global_context: GlobalContext = global_context
        
        # self.add_event_processor(MessageEvent, cast(EventProcessor, event_processors.filter_message_event_text))
        
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
        
    # async def execute_text(
    #     self, text: str, event: MessageEvent | None = None,
    #     roles: Collection[UserRole] | None = None, capture_responses: bool = False
    #     ) -> CommandExecutionResult:
    #     if not roles:
    #         roles = [UserRole.USER]
    #     if not Dispatcher.Command in self.dispatchers:
    #         raise Exception("The event manager doesn't have a Command dispatcher registered.")
    #     if event:
    #         event = dataclasses.replace(event, text=text, author_roles=set(roles))
    #     else:
    #         event = MessageEvent(
    #             text=text,
    #             id="bot",
    #             author_login="bot",
    #             author_name="bot",
    #             author_id="bot",
    #             author_roles=set(roles),
    #             room_name="bot",
    #             room_id="bot",
    #             room_capabilities=ChatRoomCapabilities(False, 999999),
    #             bot_user_login="bot",
    #             bot_user_name="bot",
    #             bot_user_id="bot",
    #             data={}
    #         )
    #     responses: list[CommandResponse] = []
    #     if capture_responses:
    #         async def message(_: MessageEvent, text: str, *args: Any, **kwargs: Any):
    #             responses.append(CommandResponse(text, args, kwargs))
    #         event.reply = MethodType(message, event)
    #         event.send = MethodType(message, event)
    #     d: BaseDispatcher = self.dispatchers[Dispatcher.Command]
    #     if not isinstance(d, CommandDispatcher):
    #         raise TypeError(f"Expected CommandDispatcher, got {type(d)}")
    #     command_exception = None
    #     try:
    #         result = await d.dispatch(
    #             self.global_context, event, no_prefix=True,
    #         )
    #         command_exception = result.error
    #     except Exception as e:
    #         if not capture_responses:
    #             raise e
    #         command_exception = e
    #     return CommandExecutionResult(
    #         responses, command_exception
    #     )

class BaseEventSource:
    requirements: list[str] = []
    event_platform: EventSource = EventSource.Any

    def __init__(self):
        self.event_processor_callback: Callable[[BaseEvent], Awaitable[None]] | None = None
        
    async def setup(self, event_manager: EventManager):
        pass

    async def teardown(self):
        pass
        
    async def send_event(self, event: BaseEvent):
        if self.event_processor_callback:
            _ = await self.event_processor_callback(event)

@dataclass(kw_only=True)
class BaseEvent:
    categories: Collection[EventCategory]
    platform: EventSource = EventSource.Any
    data: Any

    async def get_bucket_key(self, bucket_type: str) -> str:  # pyright: ignore[reportUnusedParameter]
        """Return a string key used for rate limiting/bucketing.
        
        Subclasses should override this to provide meaningful bucket keys.
        """
        return ""

class GlobalContext:
    def __init__(self):
        self._services: dict[type[Any], Any] = {}
        
    def register_service[T](self, service_type: type[T], instance: T) -> None:
        """Registers a service for cross-module sharing."""
        self._services[service_type] = instance
        
    def get_service[T](self, service_type: type[T]) -> T | None:
        """Retrieves a service. Returns None if not found."""
        return self._services.get(service_type)
        
    def require_service[T](self, service_type: type[T]) -> T:
        """Retrieves a service, raising an error if it doesn't exist."""
        service = self.get_service(service_type)
        if service is None:
            raise RuntimeError(f"Required service {service_type.__name__} is not registered in GlobalContext")
        return service

class BaseListener:
    def __init__(self, func: Callable[[GlobalContext, BaseEvent], None | Awaitable[None]], cog: Cog | None = None):
        self.id: str = f"{func.__name__}_{uuid4()}"
        self.expected_dispatcher: Dispatcher = Dispatcher.Base
        self.func: Callable[..., None | Awaitable[None]] = func
        self.cog: Cog | None = None
    
    async def check_for_match(self, event: BaseEvent) -> bool:  # pyright: ignore[reportUnusedParameter]
        return True
    
    async def _run_func(self, global_ctx: GlobalContext, event: BaseEvent, *args: Any, **kwargs: Any) -> None:
        try:
            if self.cog is not None:
                result = self.func(event, *args, **kwargs)
                if asyncio.iscoroutine(result):
                    await result
            else:
                result = self.func(global_ctx, event, *args, **kwargs)
                if asyncio.iscoroutine(result):
                    await result
        except Exception as e:
            await self.on_func_exception(global_ctx, event, e, *args, **kwargs)
            raise e
                
    async def invoke(self, global_ctx: GlobalContext, event: BaseEvent, *args: Any, **kwargs: Any) -> None:
        await self._run_func(global_ctx, event, *args, **kwargs)
        
    async def on_func_exception(self, global_ctx: GlobalContext, event: BaseEvent, error: Exception, *args: Any, **kwargs: Any) -> None:  # pyright: ignore[reportUnusedParameter]
        pass

class Cog:
    def __init__(self, event_manager: EventManager):
        self.event_manager: EventManager = event_manager
        self.global_context: GlobalContext = event_manager.global_context
        self.g_ctx: GlobalContext = event_manager.global_context
        self.name: str = self.__class__.__name__
        self.listeners: list[BaseListener] = []
        for attr_name in dir(self):
            attr: Any = getattr(self, attr_name)
            listener_dispatcher: Dispatcher | None = getattr(attr, "_listener_dispatcher", None)
            if not listener_dispatcher:
                continue
            d = event_manager.dispatchers.get(listener_dispatcher, None)
            if not d:
                LOGGER.warning(
                    f"Cog {self.name}: Listener '{attr_name}' could not be added. " +
                    f"The event manager does not have a '{listener_dispatcher.name}' dispatcher registered."
                )
                continue
            init_params = getattr(attr, "_listener_init_params", {})
            listener_cls = getattr(attr, "_listener_class", None) or d._func_listener  # pyright: ignore[reportPrivateUsage]
            new_listener = listener_cls(attr, **init_params)
            self.listeners.append(new_listener)
            new_listener.cog = self
        
    async def setup(self):
        """Called when cog is being added"""
        pass

    async def stop(self):
        """Called when cog is being removed"""
        pass
