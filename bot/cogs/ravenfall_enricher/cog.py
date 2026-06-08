from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from bot.core.components import Cog
from bot.core.decorators import on_match, priority
from bot.integrations.ravenfall import RavenfallMessageEvent
from bot.integrations.ravenfall.models import RavenfallFormattedMessage
from ravenpy import experience_for_level

if TYPE_CHECKING:
    from bot.core.components import EventManager, GlobalContext

LOGGER = logging.getLogger(__name__)


class RavenfallEnricherCog(Cog):
    """Adds computed fields to Ravenfall message events.

    Listens for RavenfallMessageEvent types that need extra computed data
    and adds them to event.message.format_args so downstream senders can use them.
    Does not send any messages itself.
    """

    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)

    @priority(10)
    @on_match(
        RavenfallMessageEvent,
        lambda e: (
            e.message_match is not None
            and e.message_match.identifier in ("village_boost", "village_boost_no_boost")
        ),
    )
    async def _on_village_boost(
        self, _g_ctx: GlobalContext, event: RavenfallMessageEvent, _match: object
    ):
        msg = event.message
        if not isinstance(msg, RavenfallFormattedMessage):
            return

        args = msg.format_args
        town_level = cast("int", args.get("townHallLevel", 0))
        remaining_exp = int(cast("float", args.get("remainingExp", 0)))

        level_exp = experience_for_level(town_level + 1)
        current_exp = level_exp - remaining_exp
        level_percent = f"{current_exp / level_exp:.2%}" if level_exp > 0 else "0%"

        args["requiredExp"] = level_exp
        args["currentExp"] = current_exp
        args["levelPercent"] = level_percent
