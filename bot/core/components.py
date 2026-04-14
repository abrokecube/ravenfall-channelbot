from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Never, Self, cast, override
from uuid import uuid4

from . import EVENT_CATEGORY_GENERIC, EVENT_SOURCE_ANY, exceptions
from .exceptions import ListenerError

# import sys
# import importlib

"""Event handling core components.

This module defines the event manager architecture used by the ravenfall channel bot.
It includes event sources, dispatchers, listeners, cogs, and rate-limiting helpers.
"""


if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Coroutine

    from .enums import BucketType
    from .types import EventProcessor

LOGGER = logging.getLogger(__name__)


async def _log_on_invoke_error[T](awaitable: Awaitable[T], error_msg: str) -> T:
    try:
        return await awaitable
    except Exception:
        LOGGER.exception(error_msg)
        raise


class GlobalContext:
    """Global dependency injection and service registry.

    Stores singleton service objects by type, enabling retrieval from
    across modules and cogs.
    """

    def __init__(self) -> None:
        """Initialize the global service registry."""
        self.event_manager: EventManager | None = None
        self._services: dict[type[BaseService], BaseService] = {}
        self._service_events: dict[type[BaseService], asyncio.Event] = {}
        self._service_waiters: dict[
            type[BaseService],
            list[asyncio.Future[BaseService]],
        ] = {}

    async def register_service(
        self,
        instance: BaseService,
    ) -> None:
        """Register a service for cross-module sharing."""
        service_type = type(instance)
        if service_type in self._services:
            msg = "Service of the same type already exists"
            raise ValueError(msg)

        instance.global_context = self

        await instance.setup()

        self._services[service_type] = instance

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
        return cast("T | None", service)

    def require_service[T: BaseService](self, service_type: type[T]) -> T:
        """Retrieve a service, raising an error if it doesn't exist."""
        service = self.get_service(service_type)
        if service is None:
            msg = (
                f"Required service {service_type.__name__}"
                " is not registered in GlobalContext"
            )
            raise RuntimeError(msg)
        return service

    async def wait_for_service[T: BaseService](
        self,
        service_type: type[T],
        max_wait: float | None = 30,
    ) -> T:
        """Wait for a service to become available, with optional timeout.

        Args:
            service_type: The type of service to wait for
            max_wait: Maximum time to wait, None for no timeout
            _tracker: Optional ServiceResolutionContext to track the waiter

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
            cast("asyncio.Future[BaseService]", waiter),
        )

        try:
            if max_wait is None:
                instance = await waiter
            else:
                instance = await asyncio.wait_for(waiter, max_wait)
            return instance
        finally:
            waiters = self._service_waiters.get(service_type)
            if waiters and waiter in waiters:
                waiters.remove(cast("asyncio.Future[BaseService]", waiter))

    async def stop_all(self) -> None:
        """Stop all services."""
        tasks: list[Awaitable[None]] = []
        for s in self._services.values():
            tasks.append(  # noqa: PERF401
                _log_on_invoke_error(
                    s.teardown(), f"Error while stopping service {type(s)}"
                )
            )
        __ = await asyncio.gather(*tasks)
        self._services.clear()


class DummyGlobalContext(GlobalContext):
    """Dummy global context.

    For classes that get a global context from the event manager.
    """

    def __init__(self) -> None:
        super().__init__()

    def raise_error(self) -> Never:
        """Raise the error."""
        msg = "This object must be added to an event manager!"
        raise RuntimeError(msg)

    @override
    async def register_service(self, instance: BaseService) -> None:
        self.raise_error()

    @override
    def get_service[T](self, service_type: type[T]) -> T | None:
        self.raise_error()

    @override
    def require_service[T](self, service_type: type[T]) -> T:
        self.raise_error()

    @override
    async def wait_for_service[T](
        self,
        service_type: type[T],
        max_wait: float | None = None,
    ) -> T:
        self.raise_error()


class BaseService:
    """Base class for a GlobalContext service."""

    def __init__(self) -> None:
        self.global_context: GlobalContext = DummyGlobalContext()

    async def setup(self) -> None:
        pass

    async def teardown(self) -> None:
        pass


class BaseEventSource:
    """Base class for event sources.

    Subclasses should implement setup/teardown and send events via
    `event_processor_callback` to the registered event manager.
    """

    event_platform: str = EVENT_SOURCE_ANY

    def __init__(self) -> None:
        """Initialize the base event source."""
        self.event_processor_callback: Callable[[BaseEvent], Awaitable[None]] | None = (
            None
        )
        self.global_context: GlobalContext = DummyGlobalContext()

    async def setup(self, event_manager: EventManager) -> None:
        """Set up the source with an EventManager reference."""

    async def teardown(self) -> None:
        """Tear down the source, releasing resources."""

    async def send_event(self, event: BaseEvent) -> None:
        """Forward an event to the registered event processor."""
        if self.event_processor_callback:
            _ = await self.event_processor_callback(event)


@dataclass(kw_only=True)
class BaseEvent:
    """Base event type passed through the event system."""

    categories: Collection[str]
    platform: str = EVENT_SOURCE_ANY
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

    def __init__(self, global_context: GlobalContext) -> None:
        """Initialize the event manager with context and empty registries."""
        self.event_sources: list[BaseEventSource] = []
        self.event_processors: dict[type[BaseEvent], list[EventProcessor[Any]]] = (
            defaultdict(list)
        )
        self.dispatchers: dict[type[BaseDispatcher], BaseDispatcher] = {
            BaseDispatcher: BaseDispatcher(),
        }
        self.cogs: dict[str, Cog] = {}
        self.global_context: GlobalContext = global_context
        global_context.event_manager = self

    async def add_event_source(self, source: BaseEventSource) -> None:
        """Add a source to begin receiving events."""
        source.event_processor_callback = self.process_event
        source.global_context = self.global_context
        self.event_sources.append(source)
        await source.setup(self)

    async def remove_event_source(self, source: BaseEventSource) -> None:
        """Remove an event source and tear it down."""
        try:
            source_idx = self.event_sources.index(source)
        except ValueError as exc:
            msg = "Source not found"
            raise ValueError(msg) from exc
        self.event_sources[source_idx].event_processor_callback = None
        removed_source = self.event_sources.pop(source_idx)
        await removed_source.teardown()
        removed_source.global_context = DummyGlobalContext()

    async def add_dispatcher(self, dispatcher: BaseDispatcher) -> None:
        """Register a dispatcher that will route events to listeners."""
        if dispatcher.identifier in self.dispatchers:
            msg = f"A dispatcher of type '{dispatcher.identifier}' already exists"
            raise ValueError(msg)
        if not isinstance(dispatcher, dispatcher.identifier):
            msg = (
                f"Dispatcher {dispatcher} is not an instance of "
                f"its identifier {dispatcher.identifier}"
            )
            raise TypeError(msg)
        self.dispatchers[dispatcher.identifier] = dispatcher
        dispatcher.global_context = self.global_context
        await dispatcher.setup(self)

    async def remove_dispatcher(self, dispatcher: BaseDispatcher) -> None:
        """Unregister and tear down a dispatcher."""
        if dispatcher.identifier not in self.dispatchers:
            msg = f"Dispatcher '{dispatcher.identifier}' was not found"
            raise ValueError(msg)
        removed_dispatcher = self.dispatchers.pop(dispatcher.identifier)
        await removed_dispatcher.teardown()
        removed_dispatcher.global_context = DummyGlobalContext()

    def add_listener(self, listener: BaseListener) -> None:
        """Register a listener with its expected dispatcher."""
        expd_dispatcher = listener.expected_dispatcher
        if expd_dispatcher not in self.dispatchers:
            msg = f"No dispatcher exists for listener {listener}"
            raise ValueError(msg)
        self.dispatchers[expd_dispatcher].add_listener(listener)

    def remove_listener(self, listener: BaseListener) -> None:
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

    async def remove_cog(self, cog_cls: type[Cog] | str) -> None:
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
            await cog_instance.teardown()
        except Exception as e:
            LOGGER.exception(f"Error occurred while stopping cog: {e}")

        del self.cogs[cog_name]

    # async def reload_cog(self, cog_cls: type[Cog]) -> type[Cog]:
    #     """Reload a cog module and replace the existing cog instance."""
    #     module_name = cog_cls.__module__
    #     cog_name = cog_cls.__name__

    #     if cog_cls in self.cogs:
    #         await self.remove_cog(cog_cls)

    #     if module_name in sys.modules:
    #         try:
    #             module = importlib.reload(sys.modules[module_name])
    #         except Exception as e:
    #             LOGGER.error(f"Failed to reload module {module_name}: {e}")
    #             raise e
    #     else:
    #         module = importlib.import_module(module_name)

    #     new_cog_cls = getattr(module, cog_name)
    #     if not isinstance(new_cog_cls, type) or not issubclass(new_cog_cls, Cog):
    #         msg = f"Module {module_name} does not contain a Cog class."
    #         raise ValueError(msg)
    #     await self.add_cog(new_cog_cls)

    #     return new_cog_cls

    def add_event_processor[T: BaseEvent](
        self,
        target_event_cls: type[BaseEvent],
        func: EventProcessor[T],
    ) -> None:
        """Register an event processor middleware for a specific event type."""
        self.event_processors[target_event_cls].append(func)

    def remove_event_processor[T: BaseEvent](
        self,
        func: EventProcessor[T],
        target_event_cls: type[T] | None = None,
    ) -> None:
        """Remove an event processor from registry."""
        if target_event_cls:
            self.event_processors[target_event_cls].remove(func)
            return
        for processors in self.event_processors.values():
            for mware in processors:
                if mware == func:
                    processors.remove(func)
                    return

    async def process_event(self, event: BaseEvent) -> None:
        """Apply processors and dispatch the event to matching dispatchers."""
        LOGGER.debug(f"Processing event {event}")
        matching_processors: list[EventProcessor[BaseEvent]] = []
        for event_type, processors in self.event_processors.items():
            if isinstance(event, event_type):
                matching_processors.extend(processors)

        for processor in matching_processors:
            event = dataclasses.replace(event)
            result: None | BaseEvent | Awaitable[None | BaseEvent] = processor(
                self.global_context,
                event,
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

    async def teardown(self) -> None:
        """Stop and remove all loaded components."""
        tasks: list[Coroutine[None, None, None]] = []
        for cog in list(self.cogs):
            tasks.append(self.remove_cog(cog))  # noqa: PERF401
        __ = await asyncio.gather(*tasks, return_exceptions=True)
        tasks = []
        for src in self.event_sources:
            tasks.append(
                _log_on_invoke_error(
                    src.teardown(), f"Error while tearing down event source {src}"
                )
            )
        for disp in self.dispatchers.values():
            tasks.append(
                _log_on_invoke_error(
                    disp.teardown(), f"Error while tearing down dispatcher {disp}"
                )
            )
        __ = await asyncio.gather(*tasks, return_exceptions=True)
        self.event_sources.clear()
        self.dispatchers.clear()
        self.event_processors.clear()

    # async def execute_text(
    #     self, text: str, event: MessageEvent | None = None,
    #     roles: Collection[UserRole] | None = None, capture_responses: bool = False
    #     ) -> CommandExecutionResult:
    #     if not roles:
    #         roles = [UserRole.USER]
    #     if not Dispatcher.Command in self.dispatchers:
    #         raise Exception("The event manager doesn't have "
    # "a Command dispatcher registered.")
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

    def __init__(self) -> None:
        """Initialize dispatcher container and assignment metadata."""
        self.identifier: type[BaseDispatcher] = BaseDispatcher
        self._func_listener: type[BaseListener] = BaseListener
        self.listeners: dict[str, BaseListener] = {}
        self.categories: set[str] = {EVENT_CATEGORY_GENERIC}
        self.global_context: GlobalContext = DummyGlobalContext()

    async def setup(self, _event_manager: EventManager) -> None:
        """Set up dispatcher resources."""

    async def teardown(self) -> None:
        """Tear down dispatcher resources."""

    def add_listener(self, listener: BaseListener) -> None:
        """Add a listener to this dispatcher."""
        if listener.id in self.listeners:
            msg = f"Listener with id '{listener.id}' already exists!"
            raise ValueError(msg)
        if listener.expected_dispatcher != self.identifier:
            msg = f"Listener {listener} cannot be assigned to this dispatcher!"
            raise ValueError(msg)
        self.listeners[listener.id] = listener

    def remove_listener(self, listener: BaseListener) -> None:
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
    ) -> None:
        try:
            await listener.invoke(g_ctx, event, *_args, **_kwargs)
        except Exception as error:
            if not isinstance(error, ListenerError):
                LOGGER.exception(
                    f"Error in {listener.func.__name__}"
                    " occurred during command invocation"
                )
            else:
                LOGGER.exception(
                    f"Error in {listener.func.__name__} handled during command invocation"
                )
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
                await self._invoke_listener(l, global_context, event, match_result)

    async def on_invoke_error(
        self,
        global_context: GlobalContext,
        event: BaseEvent,
        error: Exception,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Handle listener invocation errors."""


class BaseListener:
    """Base listener wrapping a callback and match logic."""

    expected_dispatcher: type[BaseDispatcher] = BaseDispatcher

    def __init__(
        self,
        func: Callable[[GlobalContext, BaseEvent], None | Awaitable[None]],
        cog: Cog | None = None,
        cooldown: Cooldown | None = None,
    ) -> None:
        """Initialize a listener wrapper for the target callback."""
        self.id: str = f"{func.__name__}_{uuid4()}"
        self.func: Callable[..., None | Awaitable[None]] = func
        self.cog: Cog | None = cog
        self.cooldown: Cooldown | None = getattr(func, "_listener_cooldown", cooldown)

    async def check_for_match(self, _event: BaseEvent) -> bool:
        """Return whether the listener should run for the given event."""
        return True

    async def _check_cooldown(self, event: BaseEvent) -> None:
        if self.cooldown:
            retry_after = self.cooldown.get_retry_after(event)
            if retry_after > 0:
                raise exceptions.ListenerOnCooldownError(self.cooldown, retry_after)
            self.cooldown.update_rate_limit(event)

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
            raise

    async def invoke(
        self,
        global_ctx: GlobalContext,
        event: BaseEvent,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        """Invoke the listener action."""
        await self._check_cooldown(event)
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


class Cog:
    """Cog is a pluggable module containing listeners and lifecycle hooks."""

    def __init__(self, event_manager: EventManager) -> None:
        """Initialize cog with event manager reference and attach listeners."""
        self.event_manager: EventManager = event_manager
        self.global_context: GlobalContext = event_manager.global_context
        self.g_ctx: GlobalContext = event_manager.global_context
        self.name: str = self.__class__.__name__
        self.listeners: list[BaseListener] = []
        for attr_name in dir(self):
            attr_obj = cast("object", getattr(self, attr_name))
            listener_dispatcher: type[BaseDispatcher] | None = getattr(
                attr_obj,
                "_listener_dispatcher",
                None,
            )
            if not listener_dispatcher:
                continue
            d = event_manager.dispatchers.get(listener_dispatcher, None)
            if not d:
                LOGGER.warning(
                    "Cog %s: Listener %s could not be added. "
                    "The event manager does not have a %s dispatcher registered.",
                    self.name,
                    attr_name,
                    listener_dispatcher,
                )
                continue
            init_params = cast(
                "dict[str, object]",
                getattr(attr_obj, "_listener_init_params", {}),
            )
            listener_cls = getattr(attr_obj, "_listener_class", None) or d._func_listener
            callback = cast(
                "Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]",
                attr_obj,
            )
            listener_kwargs = {k: v for k, v in init_params.items() if k != "cog"}
            new_listener = listener_cls(
                func=callback,
                cog=self,
                cooldown=None,
                **listener_kwargs,
            )
            self.listeners.append(new_listener)
            new_listener.cog = self

    async def setup(self) -> None:
        """Set up resources after cog is added."""

    async def teardown(self) -> None:
        """Tear down resources before cog is removed."""


class Cooldown:
    """Simple rate limiting bucket per key.

    Use this helper in listeners/dispatchers to prevent rapid repeated
    event handling for the same bucket key.
    """

    def __init__(self, rate: int, per: float, bucket: str | list[str] = "user") -> None:
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

    def update_rate_limit(self, event: BaseEvent) -> None:
        """Record an event occurrence for rate limit tracking."""
        now = time.time()
        key = self._get_bucket_key(event)

        if key not in self._windows:
            self._windows[key] = []

        window = self._windows[key]
        # Remove expired timestamps
        window = [t for t in window if now - t < self.per]
        window.append(now)
        self._windows[key] = window
