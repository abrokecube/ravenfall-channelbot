# pyright: reportAny=false, reportExplicitAny=false
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cooldown import Cooldown
from .modals import Parameter
from .enums import ParameterType


class ListenerError(Exception):
    """Base exception for listener-related errors."""
    def __init__(self, message: str = "Listener error"):
        self.message: str = message
        super().__init__(self.message)
        
