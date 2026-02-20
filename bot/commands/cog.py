from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .event_manager import EventManager
    from .enums import Dispatcher

import logging

LOGGER = logging.getLogger(__name__)

class Cog:
    def __init__(self, event_manager: EventManager):
        self.event_manager = event_manager
        self.global_context = event_manager.global_context
        self.g_ctx = event_manager.global_context
        self.name = self.__class__.__name__
        self.listeners = []
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            listener_dispatcher: Dispatcher | None = getattr(attr, "_listener_dispatcher", None)
            if not listener_dispatcher:
                continue
            d = event_manager.dispatchers.get(listener_dispatcher, None)
            if not d:
                LOGGER.warning(
                    f"Cog {self.name}: Listener '{attr_name}' could not be added. "
                    f"The event manager does not have a '{listener_dispatcher.name}' dispatcher registered."
                )
                continue
            init_params = getattr(attr, "_listener_init_params", {})
            listener_cls = getattr(attr, "_listener_class", None) or d._func_listener
            new_listener = listener_cls(attr, **init_params)
            self.listeners.append(new_listener)
            new_listener.cog = self
        
    async def setup(self):
        """Called when cog is being added"""
        pass

    async def stop(self):
        """Called when cog is being removed"""
        pass
    
if __name__ == '__main__':
    c = Cog()
    