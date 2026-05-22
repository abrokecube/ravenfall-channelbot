from dataclasses import dataclass, field


@dataclass
class ItemContext:
    item_prices: dict[str, int] = field(default_factory=dict)


class BaseItem:
    name: str = ""
    aliases: tuple[str, ...] = ()
    description: str = ""

    def __init__(self) -> None:
        pass

    async def get_stock(self, ctx: ItemContext) -> int:
        """Get the stock of the item.
        -1 means infinite stock.
        """
        raise NotImplementedError

    async def get_value(self, ctx: ItemContext) -> int:
        """Get the stock of the item.
        -1 means infinite stock.
        """
        raise NotImplementedError

    async def purchase(self, ctx: ItemContext, quantity: int) -> None:
        """Handle the purchase of the item."""
        raise NotImplementedError
