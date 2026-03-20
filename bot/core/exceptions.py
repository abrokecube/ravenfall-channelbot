# pyright: reportAny=false, reportExplicitAny=false
from __future__ import annotations
from bot.core.components import Cooldown

class ListenerError(Exception):
    """Base exception for listener-related errors."""
    def __init__(self, message: str = "Listener error"):
        self.message: str = message
        super().__init__(self.message)
        
class ListenerRegistrationError(ListenerError):
    """Raised when there's an error registering a listener or redeem."""
    def __init__(self, name: str, item_type: str = "Listener"):
        self.display_name: str = name
        self.item_type: str = item_type
        super().__init__(f"{item_type} '{name}' already exists")

class ListenerOnCooldown(ListenerError):
    """Raised when a listener is on cooldown."""
    def __init__(self, cooldown: Cooldown, retry_after: float):
        self.retry_after: float = retry_after
        self.cooldown: Cooldown = cooldown
        super().__init__(f"Listener is on cooldown. Try again in {retry_after:.2f}s")
