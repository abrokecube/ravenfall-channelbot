# pyright: reportAny=false, reportExplicitAny=false
from __future__ import annotations
from typing import NamedTuple
from collections.abc import Collection
from .enums import EventCategory, EventSource


class MetaFilter(NamedTuple):
    categories: Collection[EventCategory]
    invert_categories: bool  # only include the listed categories
    platforms: Collection[EventSource]
    invert_platforms: bool  # only include the listed platforms
