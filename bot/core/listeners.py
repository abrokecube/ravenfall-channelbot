# pyright: reportAny=false, reportExplicitAny=false
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, override, Any
from collections.abc import Awaitable

if TYPE_CHECKING:
    from .components import BaseListener, Cog, Cooldown, BaseEvent
from .components import GlobalContext
from .enums import Dispatcher
from .modals import MetaFilter
from .exceptions import (
    ListenerOnCooldown,
)

import logging

LOGGER = logging.getLogger(__name__)

class GenericListener(BaseListener):
    def __init__(
        self, func: Callable[[GlobalContext, BaseEvent], None | Awaitable[None]], cog: Cog | None = None,
        cooldown: Cooldown | None | None = None, 
        expected_dispatcher: Dispatcher = Dispatcher.Generic
        ):
        super().__init__(func, cog)
        self.expected_dispatcher: Dispatcher = getattr(func, '_listener_dispatcher', expected_dispatcher)
        self.meta_filter: MetaFilter = getattr(func, '_listener_meta_filter', MetaFilter([], False, [], False))
        self.cooldown: Cooldown | None = getattr(func, '_listener_cooldown', cooldown)

    @override
    async def check_for_match(self, event: BaseEvent):
        matches_categories = event.categories in self.meta_filter.categories
        if not self.meta_filter.invert_categories:
            matches_categories = not matches_categories
        matches_platforms = event.platform in self.meta_filter.platforms
        if not self.meta_filter.invert_platforms:
            matches_platforms = not matches_platforms
        return matches_categories and matches_platforms

    async def _check_cooldown(self, event: BaseEvent):
        if self.cooldown:
            retry_after = self.cooldown.get_retry_after(event)
            if retry_after > 0:
                raise ListenerOnCooldown(self.cooldown, retry_after)
            self.cooldown.update_rate_limit(event)
    
    @override
    async def invoke(self, global_ctx: GlobalContext, event: BaseEvent, match_result: Any):
        await self._check_cooldown(event)
        await self._run_func(global_ctx, event, match_result)

class LambdaListener(GenericListener):
    def __init__(self, func: Callable[..., Any], cog: Cog | None = None, cooldown: Cooldown | None = None,
        event_types: list[type[BaseEvent]] | None = None,
        match_fn: Callable[[BaseEvent], bool] = lambda x: True,
        expected_dispatcher: Dispatcher = Dispatcher.Generic
        ):
        super().__init__(func, cog, cooldown, expected_dispatcher)
        self.event_types: tuple[type[BaseEvent], ...] = tuple(event_types or [])
        self.match_fn: Callable[[BaseEvent], bool] = match_fn
        
    @override
    async def check_for_match(self, event: BaseEvent) -> bool:
        if not isinstance(event, self.event_types):
            return False
        return self.match_fn(event)

