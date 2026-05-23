from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override

import ravenpy

from . import helpers

if TYPE_CHECKING:
    from bot.core.components import GlobalContext
    from bot.integrations.ravenfall import RavenfallInstance


@dataclass
class ItemContext:
    item_prices: dict[str, int] = field(default_factory=dict)  # item name -> price
    item_stock: dict[str, int] = field(default_factory=dict)  # item name -> amount
    username: str = ""
    platform: str = ""
    rf_instance: RavenfallInstance | None = None
    g_ctx: GlobalContext | None = None

    async def send_items(self, item_name: str, amount: int, *, skip_warmup: bool = False):
        """Send item to user."""
        if (
            not self.username
            or not self.platform
            or not self.rf_instance
            or not self.g_ctx
        ):
            msg = "Missing context properties"
            raise ValueError(msg)
        await helpers.send_items(
            self.username,
            self.platform,
            self.rf_instance,
            item_name,
            amount,
            self.g_ctx,
            skip_warmup=skip_warmup,
        )


class BaseItem:
    name: str = ""
    aliases: tuple[str, ...] = ()
    description: str = ""

    def __init__(self) -> None:
        pass

    async def get_stock(self, ctx: ItemContext) -> int:  # pyright: ignore[reportUnusedParameter]
        """Get the stock of the item.
        -1 means infinite stock.
        """
        raise NotImplementedError

    async def get_value(self, ctx: ItemContext) -> int:  # pyright: ignore[reportUnusedParameter]
        """Get the stock of the item.
        -1 means infinite stock.
        """
        raise NotImplementedError

    async def purchase(self, ctx: ItemContext, quantity: int) -> None:  # pyright: ignore[reportUnusedParameter]
        """Handle the purchase of the item."""
        raise NotImplementedError


class RavenfallItemSet(BaseItem):
    name: str = ""
    aliases: tuple[str, ...] = ()
    description: str = ""

    def __init__(self, items: dict[str, int], value_multiplier: float = 0.8) -> None:
        self.items: dict[ravenpy.Item, int] = {}
        self.value_multiplier: float = value_multiplier
        for item, count in items.items():
            result = ravenpy.get_item(item)
            if not result:
                msg = f"Unknown item {item}"
                raise ValueError(msg)
            self.items[result] = count
        super().__init__()

    @override
    async def get_stock(self, ctx: ItemContext) -> int:
        return min(ctx.item_stock.get(x.name, 0) // c for x, c in self.items.items())

    @override
    async def get_value(self, ctx: ItemContext) -> int:
        return int(
            sum(ctx.item_prices.get(x.name, 0) * c for x, c in self.items.items())
            * self.value_multiplier
        )

    @override
    async def purchase(self, ctx: ItemContext, quantity: int) -> None:
        first = True
        for item, count in self.items.items():
            await ctx.send_items(item.name, count * quantity, skip_warmup=not first)
            first = False


class ArmorSet(RavenfallItemSet):
    def __init__(self, material: str, value_multiplier: float = 0.8) -> None:
        super().__init__(
            {
                f"{material} Helmet": 1,
                f"{material} Chest": 1,
                f"{material} Gloves": 1,
                f"{material} Boots": 1,
                f"{material} Leggings": 1,
            },
            value_multiplier,
        )
        self.name: str = f"{material} Armor set"
        self.description: str = f"{material} helmet, chest, gloves, boots, and leggings."
