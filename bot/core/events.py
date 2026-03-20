from __future__ import annotations
from typing import Any, TYPE_CHECKING, override, cast
from collections.abc import Collection
from dataclasses import dataclass, field
import logging

LOGGER = logging.getLogger(__name__)

from .enums import EventCategory, EventSource, UserRole
from .modals import ChatRoomCapabilities
from .components import BaseEvent

