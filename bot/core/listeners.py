# pyright: reportAny=false, reportExplicitAny=false
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, override

from .components import (
    BaseDispatcher,
    BaseListener,
)
from .modals import MetaFilter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .components import (
        BaseEvent,
        Cog,
        Cooldown,
        GlobalContext,
    )

LOGGER = logging.getLogger(__name__)


class GenericListener(BaseListener):
    def __init__(
        self,
        func: Callable[[GlobalContext, BaseEvent], None | Awaitable[None]],
        cog: Cog | None = None,
        cooldown: Cooldown | None = None,
        expected_dispatcher: type[BaseDispatcher] = BaseDispatcher,
    ):
        super().__init__(func, cog, cooldown)
        self.expected_dispatcher: type[BaseDispatcher] = getattr(
            func, "_listener_dispatcher", expected_dispatcher
        )
        self.meta_filter: MetaFilter = getattr(
            func, "_listener_meta_filter", MetaFilter([], False, [], False)
        )

    @override
    async def check_for_match(self, event: BaseEvent):
        matches_categories = event.categories in self.meta_filter.categories
        if not self.meta_filter.invert_categories:
            matches_categories = not matches_categories
        matches_platforms = event.platform in self.meta_filter.platforms
        if not self.meta_filter.invert_platforms:
            matches_platforms = not matches_platforms
        return matches_categories and matches_platforms

    @override
    async def invoke(
        self,
        global_ctx: GlobalContext,
        event: BaseEvent,
        match_result: Any,
        *args: Any,
        **kwargs: Any,
    ):
        await self._check_cooldown(event)
        await self._run_func(global_ctx, event, match_result)


class LambdaListener(GenericListener):
    def __init__(
        self,
        func: Callable[..., Any],
        cog: Cog | None = None,
        cooldown: Cooldown | None = None,
        event_types: list[type[BaseEvent]] | None = None,
        match_fn: Callable[[BaseEvent], bool] = lambda x: True,
        expected_dispatcher: type[BaseDispatcher] = BaseDispatcher,
    ):
        super().__init__(func, cog, cooldown, expected_dispatcher)
        self.event_types: tuple[type[BaseEvent], ...] = tuple(event_types or [])
        self.match_fn: Callable[[BaseEvent], bool] = match_fn

    @override
    async def check_for_match(self, event: BaseEvent) -> bool:
        if not isinstance(event, self.event_types):
            return False
        return self.match_fn(event)
