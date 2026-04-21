from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bot.core.components import (
        BaseEvent,
        BaseListener,
        EventManager,
        GlobalContext,
        ListenerMetadata,
    )

LOGGER = logging.getLogger(__name__)


class EventReceiverMixin:
    """Mixin that allows any class to receive events from the event system.

    Designed for multiple inheritance, e.g.::

        class MyClass(EventReceiverMixin):
            def __init__(self, event_manager):
                self.inject_event_manager(event_manager)

            @on_match(TwitchChatEvent, lambda e: e.message.startswith("!hello"))
            async def on_chat(self, event):
                print(f"Chat: {event.message}")
    """

    _event_manager: EventManager | None = None
    _registered_listeners: dict[str, BaseListener] | None = None

    def inject_event_manager(self, event_manager: EventManager) -> None:
        """Store a reference to the EventManager and auto-register listeners.

        Args:
            event_manager: The event manager instance to use.
        """
        self._event_manager = event_manager
        self._register_decorated_listeners()

    def _require_event_manager(self) -> EventManager:
        """Return the injected event manager or raise."""
        event_manager: EventManager | None = getattr(self, "_event_manager", None)
        if event_manager is None:
            msg = "EventManager has not been injected. Call inject_event_manager() first."
            raise RuntimeError(msg)
        return event_manager

    def _register_decorated_listeners(self) -> None:
        """Auto-discover and register decorated methods as listeners."""
        event_manager = self._require_event_manager()
        if self._registered_listeners is None:
            self._registered_listeners = {}

        for attr_name in dir(self):
            attr_obj = cast("object", getattr(self, attr_name))
            metadata_list: list[ListenerMetadata] | None = getattr(
                attr_obj, "_listener_metadata", None
            )
            if not metadata_list:
                continue

            callback = cast(
                "Callable[[GlobalContext, BaseEvent], None | Awaitable[None]]",
                attr_obj,
            )

            for metadata in metadata_list:
                if metadata.dispatcher is None:
                    continue
                dispatcher = event_manager.dispatchers.get(metadata.dispatcher, None)
                if not dispatcher:
                    LOGGER.warning(
                        "Listener %s could not be registered. "
                        "The event manager does not have a %s dispatcher registered.",
                        attr_name,
                        metadata.dispatcher,
                    )
                    continue

                listener_cls = metadata.listener_cls or dispatcher._func_listener
                listener_kwargs = {
                    k: v for k, v in metadata.init_kwargs.items() if k != "cog"
                }

                new_listener = listener_cls(
                    func=callback,
                    cog=None,
                    cooldown=metadata.cooldown,
                    priority=metadata.priority,
                    **listener_kwargs,
                )

                event_manager.add_listener(new_listener)
                self._registered_listeners[new_listener.id] = new_listener

    def unregister_all_listeners(self) -> None:
        """Remove all registered listeners from the event manager."""
        event_manager = self._require_event_manager()

        if self._registered_listeners is None:
            return

        for listener in self._registered_listeners.values():
            try:
                event_manager.remove_listener(listener)
            except ValueError:
                LOGGER.warning(
                    "Failed to remove listener %s from event manager.",
                    listener.id,
                )

        self._registered_listeners.clear()
