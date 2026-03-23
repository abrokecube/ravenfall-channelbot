from __future__ import annotations

from typing import Any, Callable, cast, TYPE_CHECKING, override
from collections import defaultdict
from collections.abc import Coroutine, Collection, Awaitable
import sys
import importlib
import dataclasses
import contextlib
from dataclasses import dataclass
import asyncio
import logging
from uuid import uuid4

"""Event handling core components.

This module defines the event manager architecture used by the ravenfall channel bot.
It includes event sources, dispatchers, listeners, cogs, and rate-limiting helpers.
"""

from .enums import Dispatcher, EventCategory, EventSource, BucketType
from .exceptions import ListenerError

if TYPE_CHECKING:
    from .types import EventProcessor

LOGGER = logging.getLogger(__name__)


class ServiceResolutionError(RuntimeError):
    """Raised when required services remain unresolved at context exit."""


class ServiceResolutionContext:
    """Context for waiting for required services before continuing."""

    def __init__(self, global_context: GlobalContext, max_wait: float | None = None):
        self.global_context: GlobalContext = global_context
        self.max_wait: float | None = max_wait
        self.required_services: set[type[BaseService]] = set()
        self.resolved_services: dict[type[BaseService], BaseService] = {}

    async def __aenter__(self) -> ServiceResolutionContext:
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_val,
        exc_tb,
    ):
        unresolved = [
            t for t in self.required_services if t not in self.resolved_services
        ]
        if unresolved:
            # Cancel any pending waiters for unresolved services
            for service_type in unresolved:
                waiters = self.global_context._service_waiters.get(service_type, [])
                for waiter in waiters:
                    if not waiter.done():
                        waiter.cancel()
                # Clear the waiters list for this service type
                self.global_context._service_waiters.pop(service_type, None)

            unresolved_list = ", ".join([t.__name__ for t in unresolved])
            error_msg = f"Unresolved services at context exit: {unresolved_list}"
            raise ServiceResolutionError(error_msg) from exc_val

        # Preserve raised exception from context body if there was one.
        return False

    async def require_service[T: BaseService](
        self,
        service_type: type[T],
        timeout: float | None = None,
    ) -> T:
        """Require a service to be resolved before continuing.

        Args:
            service_type: The type of service to wait for
            timeout: Maximum time to wait, None for no timeout

        Returns:
            The resolved service instance

        Raises:
            asyncio.TimeoutError: If the timeout is exceeded

        """
        self.required_services.add(service_type)
        service = await self.global_context.wait_for_service(
            service_type, max_wait=timeout if timeout is not None else self.max_wait
        )
        self.resolved_services[service_type] = service
        return service


class GlobalContext:
    """Global dependency injection and service registry.

    Stores singleton service objects by type, enabling retrieval from
    across modules and cogs.
    """

    def __init__(self):
        """Initialize the global service registry."""
        self._services: dict[type[BaseService], BaseService] = {}
        self._service_events: dict[type[BaseService], asyncio.Event] = {}
        self._service_waiters: dict[
            type[BaseService], list[asyncio.Future[BaseService]]
        ] = {}

    def register_service[T: BaseService](
        self, service_type: type[T], instance: T
    ) -> None:
        """Register a service for cross-module sharing."""
        if service_type in self._services:
            msg = "Service of the same type already exists"
            raise ValueError(msg)
        self._services[service_type] = instance
        instance.global_context = self

        ev = self._service_events.get(service_type)
        if ev is None:
            ev = asyncio.Event()
            self._service_events[service_type] = ev
        ev.set()

        waiters = self._service_waiters.pop(service_type, [])
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(instance)

    def get_service[T: BaseService](self, service_type: type[T]) -> T | None:
        """Retrieve a service. Returns None if not found."""
        service = self._services.get(service_type)
        return cast(T | None, service)

    def require_service[T: BaseService](self, service_type: type[T]) -> T:
        """Retrieve a service, raising an error if it doesn't exist."""
        service = self.get_service(service_type)
        if service is None:
            msg = f"Required service {service_type.__name__} is not registered in GlobalContext"
            raise RuntimeError(msg)
        return service

    async def wait_for_service[T: BaseService](
        self,
        service_type: type[T],
        max_wait: float | None = None,
    ) -> T:
        """Wait for a service to become available, with optional timeout.

        Args:
            service_type: The type of service to wait for
            max_wait: Maximum time to wait, None for no timeout

        Returns:
            The service instance when available

        Raises:
            asyncio.TimeoutError: If the timeout is exceeded

        """
        existing = self.get_service(service_type)
        if existing is not None:
            return existing

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[T] = loop.create_future()

        self._service_waiters.setdefault(service_type, []).append(
            cast(asyncio.Future[BaseService], waiter)
        )

        # re-check in case the service arrived before the waiter was registered
        existing = self.get_service(service_type)
        if existing is not None and not waiter.done():
            waiter.set_result(existing)

        try:
            if max_wait is None:
                instance = await waiter
            else:
                instance = await asyncio.wait_for(waiter, max_wait)
            return instance
        finally:
            waiters = self._service_waiters.get(service_type)
            if waiters and waiter in waiters:
                waiters.remove(cast(asyncio.Future[BaseService], waiter))

    def service_resolution_context(
        self, max_wait: float | None = None
    ) -> ServiceResolutionContext:
        """Create an async context where required services can be awaited."""
        return ServiceResolutionContext(self, max_wait)


class DummyGlobalContext(GlobalContext):
    """Dummy global context for classes that get a global context from the event manager."""

    def __init__(self):
        super().__init__()

    def raise_error(self):
        """Raise the error."""
        msg = "This object must be added to an event manager!"
        raise RuntimeError(msg)

    @override
    def register_service[T](self, service_type: type[T], instance: T) -> None:
        self.raise_error()

    @override
    def get_service[T](self, service_type: type[T]) -> T | None:
        self.raise_error()

    @override
    def require_service[T](self, service_type: type[T]) -> T:
        self.raise_error()

    @override
    async def wait_for_service[T](
        self, service_type: type[T], max_wait: float | None = None
    ) -> T:
        self.raise_error()


class BaseService:
    """Base class for a GlobalContext service."""

    def __init__(self) -> None:
        self.global_context: GlobalContext = DummyGlobalContext()


class BaseEventSource:
    """Base class for event sources.

    Subclasses should implement setup/teardown and send events via
    `event_processor_callback` to the registered event manager.
    """

    event_platform: EventSource = EventSource.Any

    def __init__(self):
        """Initialize the base event source."""
        self.event_processor_callback: Callable[[BaseEvent], Awaitable[None]] | None = (
            None
        )
        self.global_context: GlobalContext = DummyGlobalContext()

    async def setup(self, event_manager: EventManager):
        """Set up the source with an EventManager reference."""
        pass

    async def teardown(self):
        """Tear down the source, releasing resources."""
        pass

    async def send_event(self, event: BaseEvent):
        """Forward an event to the registered event processor."""
        if self.event_processor_callback:
            _ = await self.event_processor_callback(event)


@dataclass(kw_only=True)
class BaseEvent:
    """Base event type passed through the event system."""

    categories: Collection[EventCategory]
    platform: EventSource = EventSource.Any
    data: Any  # pyright: ignore[reportExplicitAny]

    async def get_bucket_key(self, bucket_type: str | BucketType) -> str:  # pyright: ignore[reportUnusedParameter]
        """Return a string key used for rate limiting/bucketing.

        Subclasses should override this to provide meaningful bucket keys.
        """
        return ""


class EventManager:
    """Coordinates event sources, processors, dispatchers, and cogs.

    EventManager receives events from event sources, applies event processors,
    and dispatches to the appropriate dispatcher(s) based on event categories.
    """

    def __init__(self, global_context: GlobalContext):
        """Initialize the event manager with context and empty registries."""
        self.event_sources: list[BaseEventSource] = []
        self.event_processors: dict[type[BaseEvent], list[EventProcessor]] = defaultdict(
            list
        )
        self.dispatchers: dict[Dispatcher, BaseDispatcher] = {
            # Dispatcher.Generic: SimpleDispatcher()
        }
        self.cogs: dict[str, Cog] = {}
        self.global_context: GlobalContext = global_context

        # self.add_event_processor(MessageEvent, cast(EventProcessor, event_processors.filter_message_event_text))

    async def add_event_source(self, source: BaseEventSource):
        """Add a source to begin receiving events."""
        source.event_processor_callback = self.process_event
        source.global_context = self.global_context
        self.event_sources.append(source)
        await source.setup(self)

    async def remove_event_source(self, source: BaseEventSource):
        """Remove an event source and tear it down."""
        try:
            source_idx = self.event_sources.index(source)
        except ValueError as exc:
            msg = "Source not found"
            raise ValueError(msg) from exc
        self.event_sources[source_idx].event_processor_callback = None
        self.event_sources[source_idx].global_context = DummyGlobalContext()
        removed_source = self.event_sources.pop(source_idx)
        await removed_source.teardown()

    async def add_dispatcher(self, dispatcher: BaseDispatcher):
        """Register a dispatcher that will route events to listeners."""
        if dispatcher.id in self.dispatchers:
            msg = f"Dispatcher with id '{dispatcher.id}' has already been added!"
            raise ValueError(msg)
        self.dispatchers[dispatcher.id] = dispatcher
        await dispatcher.setup(self)

    async def remove_dispatcher(self, dispatcher: BaseDispatcher):
        """Unregister and tear down a dispatcher."""
        if dispatcher.id not in self.dispatchers:
            msg = f"Dispatcher with id '{dispatcher.id}' was not found!"
            raise ValueError(msg)
        removed_dispatcher = self.dispatchers.pop(dispatcher.id)
        await removed_dispatcher.teardown()

    def add_listener(self, listener: BaseListener):
        """Register a listener with its expected dispatcher."""
        expd_dispatcher = listener.expected_dispatcher
        if expd_dispatcher not in self.dispatchers:
            msg = f"No dispatcher exists for listener {listener}"
            raise ValueError(msg)
        self.dispatchers[expd_dispatcher].add_listener(listener)

    def remove_listener(self, listener: BaseListener):
        """Remove a listener from its dispatcher."""
        expd_dispatcher = listener.expected_dispatcher
        if expd_dispatcher not in self.dispatchers:
            msg = f"No dispatcher exists for listener {listener}"
            raise ValueError(msg)
        self.dispatchers[expd_dispatcher].remove_listener(listener)

    async def add_cog(self, cog_cls: type[Cog], *args: object, **kwargs: object) -> None:
        """Load a new cog and register its listeners."""
        if cog_cls in self.cogs:
            msg = f"Cog {cog_cls.__name__} is already loaded."
            raise ValueError(msg)

        cog_instance = cog_cls(self, *args, **kwargs)
        self.cogs[cog_cls.__name__] = cog_instance

        for listener in cog_instance.listeners:
            self.add_listener(listener)

        await cog_instance.setup()

    async def remove_cog(self, cog_cls: type[Cog] | str):
        """Unload a cog and clean up its listeners."""
        cog_name = cog_cls if isinstance(cog_cls, str) else cog_cls.__name__

        if cog_name not in self.cogs:
            msg = f"Cog {cog_name} is not loaded."
            raise ValueError(msg)

        cog_instance = self.cogs[cog_name]

        for listener in cog_instance.listeners:
            with contextlib.suppress(ValueError):
                self.remove_listener(listener)

        try:
            await cog_instance.stop()
        except Exception as e:
            LOGGER.exception(f"Error occured while stopping cog: {e}")

        del self.cogs[cog_name]

    async def reload_cog(self, cog_cls: type[Cog]) -> type[Cog]:
        """Reload a cog module and replace the existing cog instance."""
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
            msg = f"Module {module_name} does not contain a Cog class."
            raise ValueError(msg)
        await self.add_cog(new_cog_cls)

        return new_cog_cls

    def add_event_processor(
        self, target_event_cls: type[BaseEvent], func: EventProcessor
    ):
        """Register an event processor middleware for a specific event type."""
        self.event_processors[target_event_cls].append(func)

    def remove_event_processor(
        self, func: EventProcessor, target_event_cls: type[BaseEvent] | None = None
    ):
        """Remove an event processor from registry."""
        if target_event_cls:
            self.event_processors[target_event_cls].remove(func)
            return
        for _, processors in self.event_processors.items():
            for mware in processors:
                if mware == func:
                    processors.remove(func)
                    return

    async def process_event(self, event: BaseEvent):
        """Apply processors and dispatch the event to matching dispatchers."""
        LOGGER.debug(f"Processing event {event}")
        matching_processors: list[EventProcessor] = []
        for event_type, processors in self.event_processors.items():
            if isinstance(event, event_type):
                matching_processors.extend(processors)

        for processor in matching_processors:
            event = dataclasses.replace(event)
            result: None | BaseEvent | Awaitable[None | BaseEvent] = processor(
                self.global_context, event
            )
            if isinstance(result, Awaitable):
                result = await result
            if isinstance(result, BaseEvent):
                event = result

        matching_dispatchers: dict[BaseDispatcher, None] = {}
        for category in event.categories:
            for dispatcher in self.dispatchers.values():
                if category in dispatcher.categories:
                    matching_dispatchers[dispatcher] = None
        if not matching_dispatchers:
            LOGGER.warning(f'A matching dispatcher for event "{event}" was not found.')

        for dispatcher in matching_dispatchers:
            try:
                await dispatcher.dispatch(self.global_context, event)
            except Exception as e:
                LOGGER.exception(f"Exception while sending event to dispatcher: {e}")

    async def stop_all(self):
        """Stop all loaded cogs gracefully."""
        tasks: list[Coroutine[None, None, None]] = []
        for cog in list(self.cogs):
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


class BaseDispatcher:
    """Base dispatcher for routing events to listeners."""

    def __init__(self):
        """Initialize dispatcher container and assignment metadata."""
        self.id: Dispatcher = Dispatcher.Base
        self._func_listener: type[BaseListener] = BaseListener
        self.listeners: dict[str, BaseListener] = {}
        self.categories: set[EventCategory] = set([EventCategory.Generic])

    async def setup(self, _event_manager: EventManager):
        """Set up dispatcher resources."""
        pass

    async def teardown(self):
        """Tear down dispatcher resources."""
        pass

    def add_listener(self, listener: BaseListener):
        """Add a listener to this dispatcher."""
        if listener.id in self.listeners:
            msg = f"Listener with id '{listener.id}' already exists!"
            raise ValueError(msg)
        if listener.expected_dispatcher != self.id:
            msg = f"Listener {listener} cannot be assigned to this dispatcher!"
            raise ValueError(msg)
        self.listeners[listener.id] = listener

    def remove_listener(self, listener: BaseListener):
        """Unregister a listener from its dispatcher."""
        listener_id = listener.id
        if listener_id not in self.listeners:
            msg = f"Listener with id '{listener_id}' doesn't exist!"
            raise ValueError(msg)
        __ = self.listeners.pop(listener_id)

    async def _invoke_listener(
        self,
        listener: BaseListener,
        g_ctx: GlobalContext,
        event: BaseEvent,
        *_args: object,
        **_kwargs: object,
    ):
        try:
            await listener.invoke(g_ctx, event, *_args, **_kwargs)
        except Exception as error:
            if not isinstance(error, ListenerError):
                LOGGER.exception(
                    "Error in %s occurred during command invocation: %s",
                    listener.func.__name__,
                    error,
                )  # ty:ignore[unresolved-attribute]
            else:
                LOGGER.exception(
                    "Error in %s handled during command invocation: %s",
                    listener.func.__name__,
                    error,
                )  # ty:ignore[unresolved-attribute]
            await self.on_invoke_error(g_ctx, event, error)

    async def dispatch(
        self,
        global_context: GlobalContext,
        event: BaseEvent,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        """Check and invoke listeners matching the event."""
        for l in self.listeners.values():
            match_result = False
            try:
                match_result = await l.check_for_match(event)
            except Exception as e:
                LOGGER.exception("Listener matcher returned an error: %s", e)

            if match_result:
                await self._invoke_listener(l, global_context, event)

    async def on_invoke_error(
        self,
        global_context: GlobalContext,
        event: BaseEvent,
        error: Exception,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Handle listener invocation errors."""
        pass


class BaseListener:
    """Base listener wrapping a callback and match logic."""

    id: str
    expected_dispatcher: Dispatcher
    func: Callable[..., None | Awaitable[None]]
    cog: Cog | None

    def __init__(
        self,
        func: Callable[[GlobalContext, BaseEvent], None | Awaitable[None]],
        cog: Cog | None = None,
    ):
        """Initialize a listener wrapper for the target callback."""
        self.id = f"{func.__name__}_{uuid4()}"
        self.expected_dispatcher = Dispatcher.Base
        self.func = func
        self.cog = cog

    async def check_for_match(self, _event: BaseEvent) -> bool:
        """Return whether the listener should run for the given event."""
        return True

    async def _run_func(
        self,
        global_ctx: GlobalContext,
        event: BaseEvent,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Execute the wrapped function with proper context and exception handling."""
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

    async def invoke(
        self,
        global_ctx: GlobalContext,
        event: BaseEvent,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        """Invoke the listener action."""
        await self._run_func(global_ctx, event, *_args, **_kwargs)

    async def on_func_exception(
        self,
        global_ctx: GlobalContext,
        event: BaseEvent,
        error: Exception,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Handle exceptions that occur during function execution.

        Args:
            global_ctx: The global context
            event: The event being processed
            error: The exception that occurred
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments

        """
        pass


class Cog:
    """Cog is a pluggable module containing listeners and lifecycle hooks."""

    def __init__(self, event_manager: EventManager):
        """Initialize cog with event manager reference and attach listeners."""
        self.event_manager: EventManager = event_manager
        self.global_context: GlobalContext = event_manager.global_context
        self.g_ctx: GlobalContext = event_manager.global_context
        self.name: str = self.__class__.__name__
        self.listeners: list[BaseListener] = []
        for attr_name in dir(self):
            attr_obj = cast(object, getattr(self, attr_name))
            listener_dispatcher: Dispatcher | None = getattr(
                attr_obj, "_listener_dispatcher", None
            )
            if not listener_dispatcher:
                continue
            d = event_manager.dispatchers.get(listener_dispatcher, None)
            if not d:
                LOGGER.warning(
                    "Cog %s: Listener %s could not be added. The event manager does not have a %s dispatcher registered.",
                    self.name,
                    attr_name,
                    listener_dispatcher.name,
                )
                continue
            init_params = cast(
                dict[str, object], getattr(attr_obj, "_listener_init_params", {})
            )
            listener_cls = (
                getattr(attr_obj, "_listener_class", None) or d._func_listener  # pyright: ignore[reportPrivateUsage]
            )
            callback = cast(
                Callable[[GlobalContext, BaseEvent], None | Awaitable[None]], attr_obj
            )
            listener_kwargs = {k: v for k, v in init_params.items() if k != "cog"}
            new_listener = listener_cls(callback, self, **listener_kwargs)
            self.listeners.append(new_listener)
            new_listener.cog = self

    async def setup(self):
        """Set up resources after cog is added."""
        pass

    async def stop(self):
        """Tear down resources before cog is removed."""
        pass


class Cooldown:
    """Simple rate limiting bucket per key.

    Use this helper in listeners/dispatchers to prevent rapid repeated
    event handling for the same bucket key.
    """

    def __init__(self, rate: int, per: float, bucket: str | list[str] = "user"):
        """Initialize a cooldown bucket with rate and period."""
        self.rate: int = rate
        self.per: float = per

        if not isinstance(bucket, list):
            bucket = [bucket]
        self.bucket: list[str] = bucket
        self._windows: dict[str, list[float]] = {}

    def _get_bucket_key(self, event: BaseEvent) -> str:
        """Create a bucket key from event-specific bucket types."""
        if hasattr(event, "get_bucket_key"):
            keys: list[str] = [str(event.get_bucket_key(t)) for t in self.bucket]
            return ":".join(keys)
        return ""

    def get_retry_after(self, event: BaseEvent) -> float:
        """Return seconds until the next allowed invocation for this bucket."""
        import time

        now = time.time()
        key = self._get_bucket_key(event)

        if key not in self._windows:
            return 0.0

        window = self._windows[key]
        # Remove expired timestamps
        window = [t for t in window if now - t < self.per]
        self._windows[key] = window

        if len(window) < self.rate:
            return 0.0

        return self.per - (now - window[0])

    def update_rate_limit(self, event: BaseEvent):
        """Record an event occurrence for rate limit tracking."""
        import time

        now = time.time()
        key = self._get_bucket_key(event)

        if key not in self._windows:
            self._windows[key] = []

        window = self._windows[key]
        # Remove expired timestamps
        window = [t for t in window if now - t < self.per]
        window.append(now)
        self._windows[key] = window
