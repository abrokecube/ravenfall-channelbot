from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .event_manager import EventManager

class Cog:
    def __init__(self, event_manager: EventManager):
        self.event_manager = event_manager
        self.global_context = event_manager.global_context
        self.g_ctx = event_manager.global_context
        self.listeners = []
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            d = event_manager.dispatchers.get(getattr(attr, "_listener_expected_dispatcher", None), None)
            if not d:
                continue
            setattr(attr, "_listener_from_cog", True)
            self.listeners.append(d._func_listener(attr))
        
    async def setup(self):
        """Called when cog is being added"""
        pass

    async def stop(self):
        """Called when cog is being removed"""
        pass
    
if __name__ == '__main__':
    c = Cog()
    