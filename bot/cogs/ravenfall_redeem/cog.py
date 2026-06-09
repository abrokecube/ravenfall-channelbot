from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, override

import rapidfuzz
from anyio import Path as AsyncPath
from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import DateTime, Float, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from bot.cogs.accounts.service import AccountService
from bot.cogs.currency import CurrencyService
from bot.core.components import (
    Cog,
    Cooldown,
    GlobalContext,
    fire_and_forget,
)
from bot.core.enums import BucketType
from bot.db import Base
from bot.db.session import get_async_session
from bot.integrations.chat_messages import UserRole, checks
from bot.integrations.chat_messages.utils import min_permission_level
from bot.integrations.commands import (
    ArgumentConversionError,
    BaseConverter,
    CommandError,
    CommandEvent,
    MinPermissionLevel,
    command,
    parameter,
)
from bot.integrations.ravenfall import (
    RavenfallInstance,
    RavenfallInstanceConverter,
    RavenfallService,
)
from bot.integrations.twitch import (
    EVENT_SOURCE_TWITCH,
    on_twitch_redeem,
)
from bot.mixins.config_subscriber import ConfigSubscriberMixin
from bot.services.config_service import ConfigModel, ConfigService
from bot.services.pastebin_service import PastebinService
from bot.services.ravenfall_channels import RavenfallChannelService
from ravenpy import ravenpy
from ravenpy.enums import ItemCategory
from utils.routines import routine
from utils.strings import MULT_SIGN
from utils.strutils import pl2, strjoin

from . import exceptions, helpers
from . import items as bot_items
from .items import BaseItem, ItemContext

if TYPE_CHECKING:
    from collections.abc import Collection

    from bot.core.components import (
        EventManager,
    )
    from bot.integrations.twitch import (
        TwitchRedemptionEvent,
    )

LOGGER = logging.getLogger(__name__)


class UserCreditIdleEarn(Base):
    __tablename__: str = "ravenfall_redeem_idle_earn"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    char_id: Mapped[str] = mapped_column(String, unique=True)
    total_time: Mapped[float] = mapped_column(Float, default=0)  # in seconds
    last_seen_timestamp: Mapped[datetime] = mapped_column(DateTime)


class RFRedeemChannelConfig(BaseModel):
    channel_name: str
    earn_rate: int = 1
    earn_interval: float = 100


class RFRedeemConfig(ConfigModel):
    config_table_name: ClassVar[str | None] = "cogs.ravenfall_redeem"
    item_prices_json_path: str = "./data/item_values.json"
    instances: list[RFRedeemChannelConfig] = Field(default_factory=list)
    instances_dict: dict[str, RFRedeemChannelConfig] = Field(default_factory=dict)

    @override
    def model_post_init(self, context: Any, /) -> None:  # pyright: ignore[reportExplicitAny, reportAny]
        self.instances_dict = {x.channel_name: x for x in self.instances}


class ItemConverter(BaseConverter):
    title: str = "Item"
    short_help: str = "A shop item name"
    help: str = "A shop item name"
    _min_match_score: int = 85

    def __init__(self):
        self.strings: tuple[str, ...] = ()
        self.string_to_item_mapping: dict[str, ravenpy.Item | BaseItem] = {}
        self.match_string_to_actual_string_mapping: dict[str, str] = {}

    def set_items(self, items: Collection[ravenpy.Item | BaseItem]):
        """Set item list."""
        self.string_to_item_mapping.clear()
        self.match_string_to_actual_string_mapping.clear()
        for item in items:
            if isinstance(item, ravenpy.Item):
                match_strings = [item.name.lower()]
                item_string = item.name
            else:
                match_strings = [item.name.lower()] + [x.lower() for x in item.aliases]
                item_string = item.name
            self.string_to_item_mapping[item_string] = item
            for m_str in match_strings:
                self.match_string_to_actual_string_mapping[m_str] = item_string
        self.strings = tuple(self.match_string_to_actual_string_mapping.keys())

    @override
    async def convert(
        self, g_ctx: GlobalContext, event: CommandEvent, arg: str | object
    ) -> ravenpy.Item | BaseItem:
        if not isinstance(arg, str):
            msg = "Input was an invalid type."
            raise TypeError(msg)
        results = rapidfuzz.process.extract(arg, self.strings, limit=10)
        if results[0][1] >= self._min_match_score:
            return self.string_to_item_mapping[
                self.match_string_to_actual_string_mapping[results[0][0]]
            ]
        possible_item_names: tuple[str, ...] = tuple(
            {self.match_string_to_actual_string_mapping[x[0]] for x in results}
        )
        did_you_mean = strjoin(
            ", ",
            *possible_item_names[:5],
            before_end="or ",
            include_conn_char_before_end=True,
        )
        msg = f"Couldn't find item. Did you mean: {did_you_mean}"
        raise ArgumentConversionError(msg)


item_converter = ItemConverter()


class RFRedeemCog(Cog, ConfigSubscriberMixin):
    def __init__(self, event_manager: EventManager) -> None:
        super().__init__(event_manager)
        self.item_prices: dict[str, int] = {}
        self.lurk_cd: Cooldown = Cooldown(1, 10, [BucketType.CHANNEL])
        self.config: RFRedeemConfig = RFRedeemConfig()
        self.shop_items: list[BaseItem] = []

    @override
    async def setup(self) -> None:
        config_srv = await self.global_context.wait_for_service(ConfigService)
        self.inject_config_service(config_srv)
        self.config = self.subscribe_config(RFRedeemConfig)
        await self._load_prices(self.config.item_prices_json_path)
        __ = self.idle_points.start()

        __ = await self.global_context.wait_for_service(RavenfallService)

        shop_items: list[ravenpy.Item | BaseItem] = [
            bot_items.ArmorSet("Bronze"),
            bot_items.ArmorSet("Iron"),
            bot_items.ArmorSet("Steel"),
            bot_items.ArmorSet("Black"),
            bot_items.ArmorSet("Mithril"),
            bot_items.ArmorSet("Adamantite"),
            bot_items.ArmorSet("Rune"),
            bot_items.ArmorSet("Dragon"),
            bot_items.ArmorSet("Abraxas"),
            bot_items.ArmorSet("Phantom"),
            bot_items.ArmorSet("Lionsbane"),
            bot_items.ArmorSet("Ether"),
            bot_items.ArmorSet("Ancient"),
            bot_items.ArmorSet("Atlarus"),
            bot_items.ArmorSet("Elder Bronze"),
            bot_items.ArmorSet("Elder Iron"),
            bot_items.ArmorSet("Elder Steel"),
            bot_items.ArmorSet("Elder Mithril"),
            bot_items.ArmorSet("Elder Adamantite"),
            bot_items.ArmorSet("Elder Rune"),
            bot_items.ArmorSet("Elder Dragon"),
            bot_items.ArmorSet("Elder Abraxas"),
            bot_items.ArmorSet("Elder Phantom"),
            bot_items.ArmorSet("Elder Lionsbane"),
            bot_items.ArmorSet("Elder Ether"),
            bot_items.ArmorSet("Elder Ancient"),
            bot_items.ArmorSet("Elder Atlarus"),
        ]
        shop_items.extend(self.shop_items)
        shop_items.extend(ravenpy.get_all_items())
        item_converter.set_items(shop_items)

    @override
    async def teardown(self) -> None:
        self.idle_points.cancel()

    @override
    async def on_config_changed(
        self, table: str, config: object, changed_fields: set[str]
    ) -> None:
        if not isinstance(config, RFRedeemConfig):
            return
        if "item_prices_json_path" in changed_fields:
            fire_and_forget(self._load_prices(config.item_prices_json_path))

    async def _load_prices(self, path: str):
        def_path = AsyncPath(path)
        f = await def_path.open("r")
        text = await f.read()
        adapter = TypeAdapter(dict[str, int])
        self.item_prices = adapter.validate_python(json.loads(text))

    async def _create_item_context(
        self,
        username: str = "",
        platform: str = "",
        rf_instance: RavenfallInstance | None = None,
    ):
        item_stock: dict[str, int] = {}
        if rf_instance:
            item_stock = await helpers.get_all_item_count(
                rf_instance.channel_id, self.global_context
            )

        return ItemContext(
            self.item_prices,
            item_stock,
            username,
            platform,
            rf_instance,
            self.global_context,
        )

    @routine(delta=timedelta(seconds=15), wait_remainder=True)
    async def idle_points(self):
        """Reward points to characters in town."""
        ravenfall_srv = await self.global_context.wait_for_service(RavenfallService)
        rf_channel_srv = await self.global_context.wait_for_service(
            RavenfallChannelService
        )
        currency_srv = self.global_context.require_service(CurrencyService)
        account_srv = self.global_context.require_service(AccountService)
        channels = [
            (x, self.config.instances_dict[x.channel_name])
            for x in ravenfall_srv.get_all_ravenfall_instances()
            if x.channel_name in self.config.instances_dict
        ]
        async with get_async_session() as session:
            for ch, config in channels:
                chars = await ch.get_players()
                if not chars:
                    continue
                char_ids = [x.id for x in chars]
                now = datetime.now(UTC)

                idle_earn_rows = await session.execute(
                    select(UserCreditIdleEarn).where(
                        UserCreditIdleEarn.char_id.in_(char_ids)
                    )
                )
                character_data = await rf_channel_srv.get_character_data_by_char_id(
                    char_ids, session
                )
                character_data_dict = {x.char_id: x for x in character_data}
                idle_earn_records = {rec.char_id: rec for rec in idle_earn_rows.scalars()}

                for char in chars:
                    earn_record = idle_earn_records.get(char.id)
                    if not earn_record:
                        earn_record = UserCreditIdleEarn(
                            char_id=char.id, total_time=0, last_seen_timestamp=now
                        )
                        session.add(earn_record)
                        idle_earn_records[char.id] = earn_record

                    elapsed = (now - earn_record.last_seen_timestamp).total_seconds()
                    if elapsed < 0:
                        elapsed = 0.0
                    _re_entry_threshold = 40
                    if elapsed > _re_entry_threshold:  # treat as a fresh re-entry
                        elapsed = 0.0

                    prev_total_time = earn_record.total_time
                    earn_record.total_time += elapsed
                    earn_record.last_seen_timestamp = now

                    earned_chunks = int(
                        earn_record.total_time // config.earn_interval
                    ) - int(prev_total_time // config.earn_interval)
                    if earned_chunks <= 0:
                        continue
                    account = await account_srv.get_or_create_account(
                        session,
                        EVENT_SOURCE_TWITCH,
                        character_data_dict[char.id].twitch_id,
                        character_data_dict[char.id].twitch_username,
                        None,
                        overwrite_username=False,
                    )
                    earned_credits = earned_chunks * config.earn_rate
                    __ = await currency_srv.add_currency(
                        account.id,
                        earned_credits,
                        "Idle credits",
                        session,
                        record_transaction=False,
                    )

    async def fulfill_coins_redeem(self, ctx: TwitchRedemptionEvent, amount: int):
        """Fulfill a coin redeem."""
        ravenfall_srv = self.global_context.require_service(RavenfallService)
        ravenfall = ravenfall_srv.get_ravenfall_instance(channel_id=ctx.channel_id)
        if not ravenfall:
            return
        await ctx.send(f"Sending {amount:,} coins to {ctx.data.user_login}...")
        try:
            await helpers.send_items(
                ctx.author_login,
                ctx.platform,
                ravenfall,
                "coins",
                amount,
                self.global_context,
            )
        except exceptions.OutOfItemsError as e:
            await ctx.cancel()
            LOGGER.error(f"Error in coin redeem: {e}")
            await ctx.send("There are not enough coins in stock. You have been refunded.")
            return
        except exceptions.RecipientNotFoundError as e:
            LOGGER.error(f"Error in command: {e}")
            await asyncio.sleep(0.5)
            await ctx.send("❌ Error: You are not in the game. You have been refunded.")
            return
        except (
            exceptions.CouldNotSendMessageError,
            exceptions.CouldNotSendItemsError,
            TimeoutError,
        ) as e:
            await ctx.cancel()
            LOGGER.error(f"Error in coin redeem: {e}")
            await ctx.send(f"❌ Error: {e}. Please try again. You have been refunded.")
            return
        except exceptions.PartialSendError as e:
            LOGGER.exception("Partial send error in coin redeem")
            await ctx.send(f"❌ {e}. pinging @{ravenfall.channel_name}")
            return
        except Exception:
            await ctx.cancel()
            LOGGER.exception("Unknown error occurred in coin redeem")
            await ctx.send(
                "❌ An unknown error occurred. Please try again later. "
                "You have been refunded."
            )
            return
        await ctx.fulfill()

    @staticmethod
    def _redeem_coins_matcher(ev: TwitchRedemptionEvent):
        for key in ev.internal_keys:
            re_match = re.match(r"^coins.(?P<amount>[0-9]+)$", key)
            if re_match:
                return re_match
        return None

    @on_twitch_redeem(_redeem_coins_matcher)
    async def redeem_coins(self, ctx: TwitchRedemptionEvent, result: re.Match[str]):
        """Handle redeeming coins via Twitch redeem."""
        coins_str: str = result.group("amount").replace(",", "")
        coins = int(coins_str)
        await self.fulfill_coins_redeem(ctx, coins)

    async def fulfill_credits_redeem(
        self,
        ctx: TwitchRedemptionEvent,
        amount: int,
        *,
        quiet: bool = False,
        transaction_text: str | None = None,
    ):
        """Credit `amount` item credits to the redeemer and optionally notify them."""
        currency_srv = self.global_context.require_service(CurrencyService)
        async with get_async_session() as session:
            __ = await currency_srv.add_currency(
                ctx.author_id,
                amount,
                transaction_text or "Item credits redeem",
                session,
            )
        if not quiet:
            await ctx.send(f"You have been given {amount:,} item credits.")
        await ctx.fulfill()

    @on_twitch_redeem(lambda e: "lurking" in e.internal_keys)
    async def lurking(self, ctx: TwitchRedemptionEvent, _result: object):
        """Handle redeem for lurking."""
        if await self.lurk_cd.get_retry_after(ctx) <= 0:
            await ctx.send("Thanks for lurking!")
            await self.lurk_cd.update_rate_limit(ctx)
        await self.fulfill_credits_redeem(
            ctx,
            ctx.data.reward.cost,
            quiet=True,
            transaction_text="Lurking",
        )

    @staticmethod
    def _redeem_credits_matcher(ev: TwitchRedemptionEvent):
        for key in ev.internal_keys:
            re_match = re.match(r"^item_credits.(?P<amount>[0-9]+)$", key)
            if re_match:
                return re_match
        return None

    @on_twitch_redeem(_redeem_credits_matcher)
    async def redeem_credits(self, ctx: TwitchRedemptionEvent, result: re.Match[str]):
        """Handle redeeming item credits via Twitch redeem."""
        credits_str: str = result.group("amount").replace(",", "")
        credits_count = int(credits_str)
        await self.fulfill_credits_redeem(ctx, credits_count)

    @parameter("item", regex=r"^[a-zA-Z ]+$", converter=item_converter)
    @command("credits value", aliases=["credits val", "cv"])
    async def credits_value(self, ctx: CommandEvent, item: ravenpy.Item | BaseItem):
        """Get the value of an item in credits."""
        price = 0
        if isinstance(item, ravenpy.Item):
            if item.soulbound:
                await ctx.reply(f"{item.name} is soulbound and cannot be redeemed.")
                return
            price = self.item_prices.get(item.name, 0)
        else:
            price = await item.get_value(await self._create_item_context())
        if price == 0:
            await ctx.reply(f"{item.name} is not redeemable.")
            return
        await ctx.reply(
            f"{item.name} is worth {price:,} item {pl2(price, 'credit', 'credits')}."
        )

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter("item", regex=r"^[a-zA-Z ]+$", converter=item_converter)
    @command("credits buy", aliases=["cb"])
    async def credits_buy(
        self,
        ctx: CommandEvent,
        item: ravenpy.Item | BaseItem,
        count: int,
        *,
        instance: RavenfallInstance,
    ):
        """Buy an item using your item credits."""
        currency_srv = self.global_context.require_service(CurrencyService)
        account_srv = self.global_context.require_service(AccountService)
        ravenfall_ch_srv = self.global_context.require_service(RavenfallChannelService)

        async with get_async_session() as session:
            account = await account_srv.get_or_create_account(
                session,
                ctx.message.platform,
                ctx.message.author_id,
                ctx.message.author_login,
                ctx.message.author_name,
            )
            twitch_link = await account.get_primary(session, EVENT_SOURCE_TWITCH)
        if not twitch_link:
            msg = "You have no connected Twitch account."
            raise CommandError(msg)

        player_username: str | None = None
        instance_players = await instance.get_players()
        if instance_players is None:
            msg = "Ravenfall is offline."
            raise CommandError(msg)
        for p in instance_players:
            if p.name.lower() == twitch_link.username.lower():
                player_username = twitch_link.username
                break
        else:
            async with get_async_session() as session:
                char_data = await ravenfall_ch_srv.get_character_data_by_twitch_id(
                    [twitch_link.platform_id], session
                )
            char_ids = {x.char_id: x for x in char_data}
            for p in instance_players:
                if p.id in char_ids:
                    player_username = char_ids[p.id].twitch_username
                    break
        if not player_username:
            msg = "You are currently not in this Ravenfall town."
            raise CommandError(msg)

        price = 0
        if isinstance(item, ravenpy.Item):
            if item.soulbound:
                await ctx.reply(f"{item.name} is soulbound and cannot be redeemed.")
                return
            price = self.item_prices.get(item.name, 0)
        else:
            price = await item.get_value(await self._create_item_context())
        if price == 0:
            await ctx.reply(f"{item.name} is not redeemable.")
            return
        async with get_async_session() as session:
            balance = await currency_srv.get_balance(account.id, session)
        if balance < price * count:
            await ctx.message.reply(
                f"You do not have enough credits to purchase {count:,}{MULT_SIGN} "
                f"{item.name}{pl2(count, '', '(s)')}. "
                f"You have {balance:,} {pl2(balance, 'credit', 'credits')}. "
                f"You need {price * count:,} {pl2(price * count, 'credit', 'credits')}."
            )
            return

        await ctx.message.reply(
            f"Sending you {count}{MULT_SIGN} {item.name}{pl2(count, '', '(s)')}..."
        )
        try:
            if isinstance(item, ravenpy.Item):
                await helpers.send_items(
                    player_username,
                    EVENT_SOURCE_TWITCH,
                    instance,
                    item.name,
                    count,
                    self.global_context,
                )
            else:
                await item.purchase(await self._create_item_context(), count)
        except exceptions.OutOfItemsError as e:
            LOGGER.error(f"Error in item redeem: {e}")
            await ctx.message.send(
                f"There are not enough {item.name}{pl2(count, '', '(s)')} in stock. "
                "Your credits were not deducted."
            )
            return
        except exceptions.PartialSendError as e:
            async with get_async_session() as session:
                trans_id = await currency_srv.remove_currency(
                    account.id,
                    -price * e.items_sent,
                    f"Shop purchase: {item.name} x{count}",
                    session,
                )
            await ctx.message.send(
                f"There were not enough {count:,}{MULT_SIGN} "
                f"{item.name}{pl2(count, '', '(s)')} in stock. "
                f"You received {e.items_sent:,}{MULT_SIGN} "
                f"{item.name}{pl2(e.items_sent, '', '(s)')}. "
                f"(ID: {trans_id})"
            )
            return
        except exceptions.RecipientNotFoundError as e:
            LOGGER.error(f"Error in command: {e}")
            await asyncio.sleep(0.5)
            await ctx.message.send(
                "❌ Error: You are not in the game. Your credits were not deducted."
            )
            return
        except (
            exceptions.CouldNotSendMessageError,
            exceptions.CouldNotSendItemsError,
            TimeoutError,
            exceptions.ItemNotFoundError,
        ) as e:
            LOGGER.error(f"Error in command: {e}")
            await ctx.message.send(f"❌ Error: {e}. Your credits were not deducted.")
            return
        except Exception:
            LOGGER.exception("Unknown error occurred in command")
            await ctx.message.send(
                "❌ An unknown error occurred. Please try again later. "
                "Your credits were not deducted."
            )
            return

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter("item", regex=r"^[a-zA-Z ]+$", converter=item_converter)
    @command("credits stock", aliases=["cs", "stock"])
    async def credits_stock(
        self,
        ctx: CommandEvent,
        item: ravenpy.Item | BaseItem,
        *,
        instance: RavenfallInstance,
    ):
        """Check an item's stock."""
        warning = ""
        if isinstance(item, ravenpy.Item):
            if item.soulbound:
                warning = " (This item cannot be redeemed.)"
            _, count = await helpers.get_item_count(
                instance.channel_id, item.name, self.global_context
            )
        else:
            count = await item.get_stock(
                await self._create_item_context(rf_instance=instance)
            )
        await ctx.message.reply(
            f"There {pl2(count, 'is', 'are')} currently "
            f"{count:,}{MULT_SIGN} {item.name}{pl2(count, '', '(s)')} in stock.{warning}"
        )

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter("item", regex=r"^[a-zA-Z ]+$", converter=item_converter)
    @command("credits stock coins", aliases=["cs coins", "stock coins"])
    async def credits_stock_coins(
        self,
        ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Get current coin stock."""
        count = await helpers.get_coins_count(instance.channel_id, self.global_context)
        await ctx.message.reply(
            f"There {pl2(count, 'is', 'are')} currently "
            f"{count:,} {pl2(count, 'coin', 'coins')} in stock."
        )

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter("item", regex=r"^[a-zA-Z ]+$", converter=item_converter)
    @command("credits stock all", aliases=["cs all", "stock all"])
    async def credits_stock_all(
        self,
        ctx: CommandEvent,
        *,
        instance: RavenfallInstance,
    ):
        """Get a catalog of available items."""
        paste_srv = self.global_context.require_service(PastebinService)
        item_ctx = await self._create_item_context(rf_instance=instance)
        rf_item_counts = item_ctx.item_stock
        coin_count = await helpers.get_coins_count(
            instance.channel_id, self.global_context
        )

        out_str = [
            "Stock list for channel: " + instance.channel_name,
            "",
            f"Coins: {coin_count:,}",
            "",
        ]
        categories: dict[str, list[str]] = {
            "Featured": [],
            "Raw Materials": [],
            "Materials": [],
            "Armor": [],
            "Weapons": [],
            "Accessories": [],
            "Pets": [],
            "Food": [],
            "Potions": [],
            "Cosmetics": [],
            "Scrolls": [],
            "Other": [],
        }

        item_cols = 35
        count_cols = 7
        price_cols = 6

        item_counts_list = [
            (x.name, await x.get_stock(item_ctx), await x.get_value(item_ctx))
            for x in self.shop_items
        ]
        item_counts_list = sorted(item_counts_list, key=lambda x: x[0])
        item_counts_list = sorted(item_counts_list, key=lambda x: x[1] > 0, reverse=True)
        for item_name, count, price in item_counts_list:
            item_str = (
                f"{item_name.ljust(item_cols)} "
                f"{str(count).rjust(count_cols)} "
                f"{str(price).rjust(price_cols)}c"
            )
            item_str = helpers.fill_whitespace(item_str, ".")
            item_str = f"  {item_str}"
            categories["Featured"].append(item_str)

        item_counts_list = [
            (x, rf_item_counts.get(x.name, 0), self.item_prices.get(x.name, 0))
            for x in ravenpy.get_all_items()
        ]
        item_counts_list = sorted(item_counts_list, key=lambda x: x[0].name)
        item_counts_list = sorted(
            item_counts_list,
            key=lambda x: getattr(x[0].material, "value", 0),
        )
        item_counts_list = sorted(item_counts_list, key=lambda x: x[1] > 0, reverse=True)
        for item, count, price in item_counts_list:
            warning = ""
            if item.soulbound:
                warning = " (Cannot be redeemed.)"
            item_str = (
                f"{item.name.ljust(item_cols)} "
                f"{str(count).rjust(count_cols)} "
                f"{str(price).rjust(price_cols)}c"
            )
            item_str = helpers.fill_whitespace(item_str, ".")
            item_str = f"  {item_str}{warning}"
            if item.category == ItemCategory.Resource and len(item.used_in) > 0:
                if not item.craft_ingredients:
                    categories["Raw Materials"].append(item_str)
                else:
                    categories["Materials"].append(item_str)
            else:
                match item.category:
                    case ItemCategory.Armor:
                        categories["Armor"].append(item_str)
                    case ItemCategory.Weapon:
                        categories["Weapons"].append(item_str)
                    case ItemCategory.Ring | ItemCategory.Amulet:
                        categories["Accessories"].append(item_str)
                    case ItemCategory.Pet:
                        categories["Pets"].append(item_str)
                    case ItemCategory.Food:
                        categories["Food"].append(item_str)
                    case ItemCategory.Potion:
                        categories["Potions"].append(item_str)
                    case ItemCategory.Cosmetic | ItemCategory.Skin:
                        categories["Cosmetics"].append(item_str)
                    case ItemCategory.Scroll:
                        categories["Scrolls"].append(item_str)
                    case _:
                        categories["Other"].append(item_str)
        for category_name, items in categories.items():
            if not items:
                continue
            out_str.append(f"{category_name} --- -- -- - -")
            out_str.extend(items)
            out_str.append("")
        result = await paste_srv.upload_text("\n".join(out_str))
        await ctx.reply(f"Stock list: {result.url}")

    @parameter(
        "instance",
        converter=RavenfallInstanceConverter,
        default=RavenfallInstanceConverter.MATCH_MESSAGE_EVENT,
    )
    @parameter("item", regex=r"^[a-zA-Z ]+$", converter=item_converter)
    @checks(MinPermissionLevel(UserRole.ADMINISTRATOR))
    @command("giftto")
    async def gift_to(
        self,
        ctx: CommandEvent,
        recipient: str,
        item: ravenpy.Item | BaseItem,
        count: int,
        *,
        instance: RavenfallInstance,
    ):
        """Gift an item to a user."""
        if instance.channel_id != ctx.message.room_id and not min_permission_level(
            ctx.message, UserRole.BOT_ADMINISTRATOR
        ):
            msg = "You do not have permission to specify an instance."
            raise CommandError(msg)
        account_srv = self.global_context.require_service(AccountService)
        ravenfall_ch_srv = self.global_context.require_service(RavenfallChannelService)

        async with get_async_session() as session:
            platform_link = await account_srv.find_link_by_username(
                session, ctx.message.platform, recipient
            ) or await account_srv.find_link_by_display_name(
                session, ctx.message.platform, recipient
            )
            if not platform_link:
                msg = "User has not used the bot."
                raise CommandError(msg)

            account = await account_srv.get_account_by_id(
                session, platform_link.account_id
            )
            if not account:
                msg = "Failed to get user account."
                raise CommandError(msg)

            twitch_link = await account.get_primary(session, EVENT_SOURCE_TWITCH)
        if not twitch_link:
            msg = "User has no connected Twitch account."
            raise CommandError(msg)

        player_username: str | None = None
        instance_players = await instance.get_players()
        if instance_players is None:
            msg = "Ravenfall is offline."
            raise CommandError(msg)
        for p in instance_players:
            if p.name.lower() == twitch_link.username.lower():
                player_username = twitch_link.username
                break
        else:
            async with get_async_session() as session:
                char_data = await ravenfall_ch_srv.get_character_data_by_twitch_id(
                    [twitch_link.platform_id], session
                )
            char_ids = {x.char_id: x for x in char_data}
            for p in instance_players:
                if p.id in char_ids:
                    player_username = char_ids[p.id].twitch_username
                    break
        if not player_username:
            msg = "User is not currently not in this Ravenfall town."
            raise CommandError(msg)

        if isinstance(item, ravenpy.Item) and item.soulbound:
            await ctx.reply(f"{item.name} is soulbound and cannot be gifted.")
            return

        await ctx.message.reply(
            f"Sending {twitch_link.display_name or twitch_link.username} "
            f"{count}{MULT_SIGN} {item.name}{pl2(count, '', '(s)')}..."
        )
        try:
            if isinstance(item, ravenpy.Item):
                await helpers.send_items(
                    player_username,
                    EVENT_SOURCE_TWITCH,
                    instance,
                    item.name,
                    count,
                    self.global_context,
                )
            else:
                await item.purchase(await self._create_item_context(), count)
        except exceptions.OutOfItemsError as e:
            LOGGER.error(f"Error in item redeem: {e}")
            await ctx.message.send(
                f"There are not enough {item.name}{pl2(count, '', '(s)')} in stock. "
            )
            return
        except exceptions.PartialSendError as e:
            await ctx.message.send(
                f"There were not enough {count:,}{MULT_SIGN} "
                f"{item.name}{pl2(count, '', '(s)')} in stock. "
                f"The user received {e.items_sent:,}{MULT_SIGN} "
                f"{item.name}{pl2(e.items_sent, '', '(s)')}. "
            )
            return
        except exceptions.RecipientNotFoundError as e:
            LOGGER.error(f"Error in command: {e}")
            await asyncio.sleep(0.5)
            await ctx.message.send("❌ Error: User is not in the game.")
            return
        except (
            exceptions.CouldNotSendMessageError,
            exceptions.CouldNotSendItemsError,
            TimeoutError,
            exceptions.ItemNotFoundError,
        ) as e:
            LOGGER.error(f"Error in command: {e}")
            await ctx.message.send(f"❌ Error: {e}.")
            return
        except Exception:
            LOGGER.exception("Unknown error occurred in command")
            await ctx.message.send(
                "❌ An unknown error occurred. Please try again later. "
            )
            return
