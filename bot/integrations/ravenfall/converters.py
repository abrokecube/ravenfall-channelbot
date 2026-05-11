from __future__ import annotations

from typing import TYPE_CHECKING, Final, override

import ravenpy
from bot.integrations.commands import (
    ArgumentConversionError,
    BaseConverter,
    Choice,
    CommandError,
)
from bot.services.ravenfall_channels import RavenfallChannelService

if TYPE_CHECKING:
    from bot.core.components import GlobalContext
    from bot.integrations.commands import (
        CommandEvent,
    )
    from bot.integrations.ravenfall import RavenfallInstance
    from ravenpy import Item as RFItem


class RavenfallItemConverter(BaseConverter):
    """Ravenfall item name converter."""

    title: str = "Item"
    short_help: str = "An item name"
    help: str = "A Ravenfall item name"
    _min_match_score: int = 85

    @override
    async def convert(
        self, g_ctx: GlobalContext, event: CommandEvent, arg: str | object
    ) -> RFItem:
        if not isinstance(arg, str):
            msg = "Input was an invalid type."
            raise TypeError(msg)
        item_search_results = ravenpy.search_item(arg, limit=1)
        if not item_search_results:
            msg = f"Could not identify item '{arg}'. Please check your spelling"
            raise ArgumentConversionError(msg)

        if item_search_results[0][1] < self._min_match_score:
            msg = f"Could not identify item '{arg}'. Please check your spelling"
            raise ArgumentConversionError(msg)
        return item_search_results[0][0]


class RavenfallInstanceConverter(BaseConverter):
    """If None is passed, will return the Ravenfall instance associated with
    the channel, otherwise, will take a instance channel name.

    Raises ArgumentConversionError if a matching instance could not be found.
    """

    title: str = "Ravenfall instance"
    short_help: str = "The Ravenfall instance associated with this channel."
    help: str = "The Ravenfall instance associated with this channel."
    MATCH_MESSAGE_EVENT: Final[object] = "__match_msg_event"

    @classmethod
    @override
    async def cls_convert(
        cls, g_ctx: GlobalContext, event: CommandEvent, arg: str | object
    ) -> RavenfallInstance:
        if not isinstance(arg, str):
            msg = "Invalid input type."
            raise TypeError(msg)
        from .services import RavenfallService

        ravenfall_srv = g_ctx.get_service(RavenfallService)
        ravenfall_channels_srv = g_ctx.get_service(RavenfallChannelService)
        if (not ravenfall_srv) or (not ravenfall_channels_srv):
            msg = "Ravenfall service has not been loaded. Try again later."
            raise CommandError(msg)
        if arg is cls.MATCH_MESSAGE_EVENT:
            instance = ravenfall_channels_srv.get_matching_instance_for_message_event(
                event.message
            )
            if not instance:
                msg = "A Ravenfall channel must be specified."
                raise CommandError(msg)
            return instance
        instance = ravenfall_srv.get_ravenfall_instance(channel_name=arg)
        if not instance:
            msg = f"Ravenfall instance '{arg}' was not found."
            raise CommandError(msg)
        return instance


class RavenfallSkillChoice(Choice):
    def __init__(self, *, case_sensitive: bool = False):
        definition = {
            "Attack": ["atk", "att"],
            "Defense": ["def"],
            "Strength": ["str"],
            "Health": ["hp"],
            "Woodcutting": ["wood", "chop", "wdc", "chomp"],
            "Mining": ["mine", "min"],
            "Crafting": ["craft"],
            "Cooking": ["cook", "ckn"],
            "Farming": ["farm", "fm"],
            "Slayer": ["slay"],
            "Magic": [],
            "Ranged": ["range"],
            "Sailing": ["sail"],
            "Healing": ["heal"],
            "Gathering": ["gath"],
            "Alchemy": ["brew", "alch"],
            "CombatLevel": ["combat"],
        }
        super().__init__(definition, "Ravenfall skill", case_sensitive=case_sensitive)
