# pyright: reportAny=false, reportExplicitAny=false
from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Collection


class MetaFilter(NamedTuple):
    categories: Collection[str]
    invert_categories: bool  # only include the listed categories
    platforms: Collection[str]
    invert_platforms: bool  # only include the listed platforms
